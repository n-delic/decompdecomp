"""Durable project-wide deep passes; each candidate gets a bounded turn through the normal judges."""
import hashlib
import json
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import candidate_scope, claim_stalls, scoring
from ..network import IN_BUILD, NetworkError
from . import agent_tokens, deep_permuter, workers
from .deep_activity import Activity

POLL_SECONDS = 15
QUEUE_CACHE_SECONDS = 10
# Scope checks need candidate inputs, never archived matched source or unrelated function metadata.
SCOPE_COLUMNS = ",".join("f."+name for name in ("id","project_id","name","status","best_source","best_score",
    "unit","vram","rom_offset","asm","seed","relocs","target"))
SCHEMA = '''CREATE TABLE IF NOT EXISTS deep_permutation_passes(
 function_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
 minutes INTEGER NOT NULL, max_variants INTEGER NOT NULL, completed_at REAL,
 result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')'''


def signature(net, proj, fn, minutes, variants, compiler_profiles=None, shared=None):
    sweeps = getattr(net, 'deep_sweeps', None)
    count = compiler_profiles
    if count is None:
        count = sweeps.config(proj['slug']).get('compiler_profiles', 1) if sweeps else 1
    target = (agent_tokens.target_scope(net, fn, '') if shared is None else
              hashlib.sha256(repr(('', candidate_scope.key(net, proj, fn, shared), claim_stalls.repair_rules())).encode()).hexdigest())
    inputs = ('deep-pass-profiles-v1', count, target, fn['best_source'], minutes, variants)
    # Retry the two formerly blocked states once under the new recovery rules. Keep unrelated
    # completed passes resumable; users can explicitly repeat them to try the new search recipes.
    if fn['status'] == 'open' and fn['best_score'] == 0:
        inputs += ('reverify-zero-v1',)
    elif fn['best_source'] and not (shared['fork'] if shared is not None and 'fork' in shared else net.fork_slug(proj['slug'])):
        from .prototype_alignment import prepare
        from .prototype_alignment import variants as align
        ctx = candidate_scope.context(net, proj, fn['unit'], shared)
        prepared = None
        if shared is not None and ctx:
            alignments = shared.setdefault('alignments', {})
            if fn['unit'] not in alignments:
                alignments[fn['unit']] = prepare(ctx['header'])
            prepared = alignments[fn['unit']]
        if ctx and align(fn['best_source'], fn['name'], ctx['header'], prepared):
            inputs += ('return-alignment-v1',)
    return hashlib.sha256(repr(inputs).encode()).hexdigest()


class Sweeps:
    def __init__(self, net, jobs):
        self.net, self.jobs = net, jobs
        self.active = {}
        self.queue_cache = {}
        self.lock = threading.RLock()
        self.ready = set()
        self._prepare(net.store)

    def _prepare(self, store):
        with self.lock:
            if store in self.ready:return store
            self._install(store)
            self.ready.add(store)
        return store

    def _store(self, project):
        return self._prepare(self.net.project_store(project))

    def _install(self, store):
        with store.tx() as db:
            db.execute(SCHEMA)
            store._track_reads('deep_permutation_passes', 'passes')
            db.execute('''CREATE TABLE IF NOT EXISTS deep_permutation_recipes(
                function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE, family TEXT NOT NULL,
                tested INTEGER NOT NULL, skipped INTEGER NOT NULL, rejected INTEGER NOT NULL,
                improvements INTEGER NOT NULL, matches INTEGER NOT NULL, score_gain INTEGER NOT NULL,
                PRIMARY KEY(function_id,family))''')
            db.execute('''CREATE TABLE IF NOT EXISTS deep_permutation_measurements(
                function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
                family TEXT NOT NULL, phase TEXT NOT NULL, attempted INTEGER NOT NULL,
                compile_failed INTEGER NOT NULL, unscored INTEGER NOT NULL, unchanged INTEGER NOT NULL,
                worse INTEGER NOT NULL, improved INTEGER NOT NULL, matched INTEGER NOT NULL,
                elapsed_ms INTEGER NOT NULL, score_gain INTEGER NOT NULL, PRIMARY KEY(function_id,family,phase))''')
            db.execute('''CREATE TABLE IF NOT EXISTS deep_permutation_recipe_wins(
                id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
                family TEXT NOT NULL, previous_score INTEGER NOT NULL, new_score INTEGER NOT NULL,
                provenance TEXT NOT NULL, created_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS deep_permutation_improvements(
                function_id INTEGER PRIMARY KEY, starting_score INTEGER NOT NULL,
                starting_percent REAL, new_score INTEGER NOT NULL,
                new_percent REAL, updated_at REAL NOT NULL)''')

    def record_recipes(self, function_id, totals, wins, measurements=None, *, project=None):
        """Save counts and individual verified improvements, including searches stopped part way through."""
        store = self._store(project) if project is not None else self.net.function_store(function_id)
        if store is None:raise NetworkError('This function no longer exists.')
        with self._prepare(store).tx() as db:
            for family, values in totals.items():
                db.execute("""INSERT INTO deep_permutation_recipes
                    (function_id,family,tested,skipped,rejected,improvements,matches,score_gain)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(function_id,family) DO UPDATE SET
                    tested=tested+excluded.tested, skipped=skipped+excluded.skipped,
                    rejected=rejected+excluded.rejected, improvements=improvements+excluded.improvements,
                    matches=matches+excluded.matches, score_gain=score_gain+excluded.score_gain""",
                    (function_id, family, *values))
            for (family, phase), values in (measurements or {}).items():
                db.execute("""INSERT INTO deep_permutation_measurements
                    (function_id,family,phase,attempted,compile_failed,unscored,unchanged,worse,improved,matched,elapsed_ms,score_gain)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(function_id,family,phase) DO UPDATE SET
                    attempted=attempted+excluded.attempted, compile_failed=compile_failed+excluded.compile_failed,
                    unscored=unscored+excluded.unscored, unchanged=unchanged+excluded.unchanged,
                    worse=worse+excluded.worse, improved=improved+excluded.improved,
                    matched=matched+excluded.matched, elapsed_ms=elapsed_ms+excluded.elapsed_ms,
                    score_gain=score_gain+excluded.score_gain""",
                    (function_id, family, phase, *values))
            for win in wins:
                db.execute("""INSERT INTO deep_permutation_recipe_wins
                    (function_id,family,previous_score,new_score,provenance,created_at)
                    VALUES(?,?,?,?,?,?)""", (function_id, *win))

    def reset_metrics(self, slug):
        """Reset this project's counters without deleting code, pass records or rewrite evidence."""
        project_id = self.net.project_row(slug)['id']
        if any(job.get('state') == 'running' and job.get('kind') in {'deep-permuter', 'deep-permutation-pass'}
               for job in self.jobs.jobs.values()):
            raise NetworkError('Stop active deep permuter searches before resetting metrics.')
        with self._store(project_id).tx() as db:
            for table in ('deep_permutation_recipes', 'deep_permutation_improvements', 'deep_permutation_measurements'):
                db.execute(f'DELETE FROM {table} WHERE function_id IN '
                           '(SELECT id FROM functions WHERE project_id=?)', (project_id,))
            last = db.execute('SELECT COALESCE(MAX(id),0) FROM deep_permutation_recipe_wins').fetchone()[0]
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (f'deep-metrics-reset:{project_id}', str(last)))
        self.save(slug, self.config(slug) | {'metrics_started': False})
        with self.lock:
            self.active.pop(slug, None)
        return self.status(slug)

    def recipes(self, project_id):
        rows = self._store(project_id).all("""SELECT r.family, SUM(r.tested) tested, SUM(r.skipped) skipped,
            SUM(r.rejected) rejected, SUM(r.improvements) improvements, SUM(r.matches) matches,
            SUM(r.score_gain) score_gain, COUNT(DISTINCT r.function_id) functions
            FROM deep_permutation_recipes r JOIN functions f ON f.id=r.function_id
            WHERE f.project_id=? GROUP BY r.family ORDER BY improvements DESC, tested DESC, r.family""", project_id)
        return [dict(row) for row in rows]

    def measurements(self, project_id):
        rows = self._store(project_id).all("""SELECT m.family,m.phase,SUM(m.attempted) attempted,
            SUM(m.compile_failed) compile_failed,SUM(m.unscored) unscored,SUM(m.unchanged) unchanged,
            SUM(m.worse) worse,SUM(m.improved) improved,SUM(m.matched) matched,SUM(m.elapsed_ms) elapsed_ms,
            SUM(m.score_gain) score_gain
            FROM deep_permutation_measurements m JOIN functions f ON f.id=m.function_id
            WHERE f.project_id=? GROUP BY m.family,m.phase ORDER BY improved DESC,attempted DESC""", project_id)
        return [dict(row) for row in rows]

    def recipe_wins(self, project_id):
        rows = self._store(project_id).all("""SELECT w.family,w.previous_score,w.new_score,w.provenance,w.created_at,
            f.name AS function FROM deep_permutation_recipe_wins w JOIN functions f ON f.id=w.function_id
            WHERE f.project_id=? AND w.id>COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key=?),0)
            ORDER BY w.id DESC LIMIT 50""", project_id, f'deep-metrics-reset:{project_id}')
        return [dict(row) | {'provenance': json.loads(row['provenance'])} for row in rows]

    def config(self, slug):
        proj = self.net.project_row(slug)
        row = self._store(slug).one('SELECT value FROM settings WHERE key=?', f'deep-sweep:{proj["id"]}')
        return {'threads': 0, 'threads_per_function': 1, 'compiler_profiles': 1, 'loop': False} | (json.loads(row['value']) if row else {'enabled': False, 'minutes': 5, 'max_variants': 10000})

    def save(self, slug, cfg):
        proj = self.net.project_row(slug)
        with self._store(slug).tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (f'deep-sweep:{proj["id"]}', json.dumps(cfg)))

    def history(self, slug, fn):
        row = self._store(slug).one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
        if not row:
            return {'state': 'not tried', 'completed': False}
        result = dict(row)
        result['stale'] = row['scope'] != signature(self.net, self.net.project_row(slug), fn,
                                                   row['minutes'], row['max_variants'])
        result['completed'] = row['state'] == 'completed' and not result['stale']
        result['result'] = json.loads(row['result'])
        return result

    def improvement(self, before, score):
        """Keep only scores measured by this search, including wins before cancellation."""
        start = before['best_score']
        if score is None or start is None or score >= start:
            return
        def percent(value):
            if value == 0:
                return 100.0
            if not before['n_insns']:
                return None
            return max(0.0, min(99.9 if value else 100.0,
                               100.0 * (1 - value / max(1, before['n_insns'] * 100))))
        with self._store(before['project_id']).tx() as db:
            db.execute('''INSERT INTO deep_permutation_improvements
                (function_id,starting_score,starting_percent,new_score,new_percent,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(function_id) DO UPDATE SET
                new_score=excluded.new_score,new_percent=excluded.new_percent,updated_at=excluded.updated_at
                WHERE excluded.new_score < deep_permutation_improvements.new_score''',
                (before['id'], start, percent(start), score, percent(score), time.time()))

    def improvements(self, project_id):
        rows = self._store(project_id).all('''SELECT i.*, f.name AS function FROM deep_permutation_improvements i
            JOIN functions f ON f.id=i.function_id WHERE f.project_id=? ORDER BY i.updated_at DESC,f.id''', project_id)
        return [dict(row) | {'starting_percent_text': scoring.percent_text(row['starting_percent']),
                             'new_percent_text': scoring.percent_text(row['new_percent'])} for row in rows]

    def attention(self, proj):
        return self._store(proj['id']).read_cache.get(('deep-attention', proj['id']),
            (self.config(proj['slug']).get('compiler_profiles',1), self._store(proj['id']).read_version('inputs', proj['id']), self._store(proj['id']).read_version('passes', proj['id']), claim_stalls.repair_rules()),
            lambda: self._attention(proj), ttl=QUEUE_CACHE_SECONDS if self.net.fork_slug(proj['slug']) else None)

    def _attention(self, proj):
        """Only failures that still block this build's unchanged, eligible candidates."""
        rows = self._store(proj['id']).all(
            f'SELECT {SCOPE_COLUMNS},d.scope AS pass_scope,d.error AS pass_error,d.minutes AS pass_minutes, '
            'd.max_variants AS pass_variants FROM functions f '
            'JOIN deep_permutation_passes d ON d.function_id=f.id '
            f"WHERE f.project_id=? AND f.status='open' AND f.{IN_BUILD} AND d.state='error'",
            proj['id'])
        from .deep_room import unavailable
        issues = []
        shared = candidate_scope.batch_context(self.net, proj)
        profiles = self.config(proj['slug']).get('compiler_profiles', 1)
        for fn in rows:
            if unavailable(fn['pass_error']):
                continue
            if not fn['best_source'] or fn['best_score'] is None:
                continue
            if fn['pass_scope'] != signature(self.net, proj, fn, fn['pass_minutes'], fn['pass_variants'], profiles, shared):
                continue
            error = fn['pass_error'] or 'The search stopped without recording an error message.'
            lower = error.lower()
            if 'quality' in lower:
                action = 'Review this candidate in Repair examples before searching again.'
            elif 'does not compile' in lower or 'no longer compile' in lower or 'declarations' in lower:
                action = 'Open the function and compile its saved C; fix any reported declaration or compiler errors.'
            elif 'token' in lower:
                action = 'An internal search worker lost authentication. Retry with the updated app.'
            elif 'compiler' in lower or 'toolchain' in lower:
                action = 'Check this function’s compiler settings and test its saved C.'
            else:
                action = 'Open the function to inspect its saved C and compiler settings.'
            issues.append({'function': fn['name'], 'error': error, 'action': action})
        return sorted(issues, key=lambda issue: issue['function'])

    def recover_room_transport(self, slug):
        """Requeue transient failures and turns cut short by the retired budget policy."""
        from .deep_room import unavailable
        proj=self.net.project_row(slug)
        rows=self._store(slug).all("SELECT function_id,state,error FROM deep_permutation_passes WHERE state IN ('error','rotated') AND function_id IN (SELECT id FROM functions WHERE project_id=?)",proj['id'])
        ids=[r['function_id'] for r in rows if r['state']=='rotated' or unavailable(r['error'])]
        if ids:
            with self._store(slug).tx() as db:
                db.executemany("UPDATE deep_permutation_passes SET state='pending',error='' WHERE function_id=?",[(fid,) for fid in ids])
            with self.lock:self.queue_cache.clear()

    def control(self, slug):
        """Discover work and controls without scanning saved searches or build inputs."""
        cfg = self.config(slug)
        with self.lock:
            current = dict(self.active.get(slug, {}))
        with self.jobs._lock:
            job = self.jobs.jobs.get(current.get('job'), {})
            live = {key:job[key] for key in ('state', 'progress', 'error') if key in job}
        return cfg | {'control_only':True, 'threads_default':workers.default_workers(),
                      'has_activity':bool(job or cfg.get('metrics_started')), 'state':'idle', **current, **live}

    def status(self, slug):
        # A status poll must never queue behind a writer to repair old pass records.
        # The search worker performs recovery; until then transport errors and rotations are retryable.
        proj = self.net.project_row(slug)
        counts = self._store(slug).one("SELECT COUNT(*) total, SUM(best_source IS NOT NULL AND best_score IS NOT NULL) ready "
                                    f"FROM functions WHERE project_id=? AND status='open' AND {IN_BUILD}", proj['id'])
        completed = self._store(slug).one("SELECT COUNT(*) n FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id "
                                      "WHERE f.project_id=? AND d.state='completed'", proj['id'])['n']

        improvements = self.improvements(proj['id'])
        cfg = self.config(slug)
        attention = self.attention(proj)
        errors = len(attention)
        # Matching the current build can inspect project files. Cache the result briefly so opening
        # details or polling a running pass does not repeatedly hash every completed function.
        ready = counts['ready'] or 0
        with self.lock:
            current = dict(self.active.get(slug, {}))
        def count_queue():
            shared = candidate_scope.batch_context(self.net, proj)
            done_open = done_matched = rotated = blocked = 0
            rows = self._store(slug).all(f"""SELECT {SCOPE_COLUMNS},d.error AS pass_error,d.scope AS pass_scope,d.minutes AS pass_minutes,
                d.max_variants AS pass_variants,d.state AS pass_state FROM functions f JOIN deep_permutation_passes d ON d.function_id=f.id
                WHERE f.project_id=? AND f.gone_mark IS NULL AND f.status IN ('open','matched')
                AND d.state IN ('completed','error') AND f.best_source IS NOT NULL AND f.best_score IS NOT NULL
                AND d.minutes=? AND d.max_variants=?""", proj['id'], cfg['minutes'], cfg['max_variants'])
            from .deep_room import unavailable
            for row in rows:
                if row['pass_state'] == 'error' and unavailable(row['pass_error']):
                    continue
                if (row['best_source'] and row['best_score'] is not None
                        and row['pass_minutes'] == cfg['minutes'] and row['pass_variants'] == cfg['max_variants']
                        and row['pass_scope'] == signature(self.net, proj, row, cfg['minutes'], cfg['max_variants'], cfg.get('compiler_profiles', 1), shared)):
                    if row['pass_state'] == 'completed':
                        if row['status'] == 'open':
                            done_open += 1
                        else:
                            done_matched += 1
                    elif row['status'] == 'open':
                        blocked += int(row['pass_state'] == 'error')
            return done_open, done_matched, rotated, blocked
        done_open, done_matched, rotated, blocked = self._store(slug).read_cache.get(
            ('deep-queue', proj['id']),
            (cfg['minutes'], cfg['max_variants'], cfg.get('compiler_profiles',1), self._store(slug).read_version('inputs', proj['id']), self._store(slug).read_version('passes', proj['id']), claim_stalls.repair_rules()),
            count_queue, ttl=QUEUE_CACHE_SECONDS if self.net.fork_slug(slug) else None)
        job = self.jobs.jobs.get(current.get('job'), {})
        recipes, measurements, wins = self.recipes(proj['id']), self.measurements(proj['id']), self.recipe_wins(proj['id'])
        reset = self._store(slug).one('SELECT value FROM settings WHERE key=?', f'deep-metrics-reset:{proj["id"]}')
        has_activity = bool(job or improvements or recipes or measurements or wins
                            or cfg.get('metrics_started', bool((completed or errors) and not reset)))
        return cfg | {'has_activity': has_activity, 'threads_default': workers.default_workers(),
                      'total': counts['total'], 'ready': ready, 'queue_done': done_open + done_matched,
                      'queue_left': max(0, ready - done_open - blocked), 'queue_rotated': rotated, 'queue_blocked': blocked, 'awaiting_source': max(0, counts['total'] - ready),
                                   'improved': len(improvements), 'improvements': improvements, 'recipes': recipes, 'recipe_wins': wins,
                                   'measurements': measurements,
                                   'completed': completed, 'errors': errors, 'attention': attention, 'state': job.get('state', 'idle'), **current,
                                   'progress': job.get('progress', {}), 'error': job.get('error')}

    def start(self, slug, minutes=5, max_variants=10000, repeat=False, threads=0, compiler_profiles=1, loop=False, threads_per_function=1):
        from . import deep_profiles
        if not isinstance(loop, bool):
            raise NetworkError('Loop indefinitely must be true or false.')
        deep_profiles.validate(compiler_profiles)
        from .deep_parallel import validate_threads
        validate_threads(threads_per_function)
        if compiler_profiles > 1 and self.net.fork_slug(slug):
            raise NetworkError('Multiple compiler profiles require a ROM project; imported builds own their compiler settings.')
        if (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120 or
                isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000):
            raise NetworkError('Choose 1–120 minutes and 1–1,000,000 variants per function.')
        if isinstance(threads, bool) or not isinstance(threads, int) or not threads >= 0:
            raise NetworkError('Choose a number of threads, or leave it empty for automatic.')
        self.net.project_row(slug)
        with self.lock:
            previous = self.active.get(slug, {})
            if self.jobs.jobs.get(previous.get('job'), {}).get('state') == 'running':
                return self.control(slug)
            if repeat:
                self.queue_cache.clear()
                with self._store(slug).tx() as db:
                    db.execute("DELETE FROM deep_permutation_passes WHERE function_id IN "
                               "(SELECT id FROM functions WHERE project_id=? AND status='open')",
                               (self.net.project_row(slug)['id'],))
            self.save(slug, {'enabled': True, 'minutes': minutes, 'max_variants': max_variants, 'threads': threads, 'threads_per_function': threads_per_function, 'compiler_profiles': compiler_profiles, 'metrics_started': True, 'loop': loop})
            job = self.jobs.start('deep-permutation-pass', lambda log: self.run(slug, minutes, max_variants, log, threads, loop),
                                  cancel_message='Deep pass stopped. Verified improvements and completed passes are saved.')
            self.active[slug] = {'job': job}
        return self.control(slug)

    def stop(self, slug):
        with self.lock:
            cfg = self.config(slug)
            self.save(slug, cfg | {'enabled': False})
            job = self.active.get(slug, {}).get('job')
            if job:
                self.jobs.cancel(job)
        return self.control(slug)

    def resume(self):
        """Leave interrupted passes stopped; the user starts them again when ready."""
        for store, _ in self.net.project_stores():
            with store.tx() as db:
                rows = db.execute("SELECT key,value FROM settings WHERE key LIKE 'deep-sweep:%'").fetchall()
                for row in rows:
                    cfg = json.loads(row['value'])
                    if cfg.get('enabled'):
                        cfg['enabled'] = False
                        db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(cfg), row['key']))

    def shutdown(self):
        from . import release_repairs
        release_repairs.shutdown(self.net,self.jobs)
        # Stop current searches; their completed records remain available for a manual restart.
        with self.lock:
            for current in self.active.values():
                self.jobs.cancel(current['job'])

    def completed(self, slug, before, minutes, variants, result, build_scope):
        self.improvement(before, result.get('best_score'))
        fresh = self.net._function(before['name'], slug)
        proj = self.net.project_row(slug)
        stable = build_scope == candidate_scope.key(self.net, proj, before)
        self.record(fresh, signature(self.net, proj, fresh, minutes, variants, result.get('compiler_profiles')),
                    'completed' if stable else 'pending', minutes, variants, result)

    def record(self, fn, scope, state, minutes, variants, result=None, error=''):
        with self.lock:
            self.queue_cache.clear()
        with self._store(fn['project_id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO deep_permutation_passes '
                       '(function_id,scope,state,minutes,max_variants,completed_at,result,error) VALUES(?,?,?,?,?,?,?,?)',
                       (fn['id'], scope, state, minutes, variants, time.time() if state == 'completed' else None,
                        json.dumps(result or {}), error))

    def run(self, slug, minutes, variants, log, threads=1, loop=False):
        """Keep a bounded queue moving; optionally repeat eligible turns until stopped.

        Blocked functions never occupy a worker. Sleep only when no useful turn is available.
        """
        self.recover_room_transport(slug)
        count = workers.clamp(threads) if threads != 1 else 1
        stats = {'visited': 0, 'tested': 0, 'matched': 0, 'rotated':0, 'activity': Activity(log)}
        visited_turns = set()
        lock = threading.Lock()  # (the counters are shared by every thread's function)
        cycle = 1
        order = []
        futures = {}
        pool = ThreadPoolExecutor(count, thread_name_prefix='deep-pass', initializer=workers.lower_priority)
        stats['activity'].publish({'cycle': cycle, 'loop': loop,
            'preparation': {'checked': 0, 'total': 0, 'eligible': 0, 'waiting': 0, 'done': False}}, force=True)
        try:
            while True:
                log.check_cancel()
                proj = self.net.project_row(slug)
                pending = waiting = 0
                # Shuffle lightweight identities first. Never fingerprint the entire project before
                # dispatching work; keep at most two turns per worker queued ahead of execution.
                identities = [dict(r) for r in self._store(slug).all(f"SELECT id,name,size FROM functions WHERE project_id=? AND status='open' AND {IN_BUILD}", proj['id'])]
                from .boundary_repairs import blocked as boundary_blocked
                held=boundary_blocked(self.net,proj)
                identities=[r for r in identities if r["id"] not in held]
                if not order:
                    random.shuffle(identities)
                    order.extend(r["id"] for r in identities)
                ranks = {fid: i for i, fid in enumerate(order)}
                identities.sort(key=lambda r: ranks.get(r["id"], len(ranks)))
                total = len(identities)
                scanned = submitted = 0
                shared = {'contexts': {}}
                profiles = self.config(slug).get('compiler_profiles', 1)
                def preparing(checked, eligible, blocked, done=False, total=total):
                    stats['activity'].publish({'preparation': {'checked': checked, 'total': total,
                        'eligible': eligible, 'waiting': blocked, 'done': done}}, force=done)
                preparing(scanned, pending, waiting)

                failed = threading.Event()
                def execute(item, blocked, proj=proj, total=total):
                    log.check_cancel()
                    fn = self._store(slug).one('SELECT * FROM functions WHERE id=? AND project_id=?', item['id'], proj['id'])
                    if not fn or fn['status'] != 'open' or fn['gone_mark'] is not None:
                        return
                    # Dispatch always uses fresh inputs, never the producer's shared snapshot.
                    scope = signature(self.net, proj, fn, minutes, variants)
                    previous = self._store(slug).one('SELECT scope,state FROM deep_permutation_passes WHERE function_id=?', fn['id'])
                    if previous and previous['scope'] == scope and (previous['state'] == 'error' or (not loop and previous['state'] == 'completed')):
                        return
                    self.search_one(slug, proj, fn, scope, minutes, variants, log, stats, lock, total, blocked)
                    previous = self._store(slug).one('SELECT state FROM deep_permutation_passes WHERE function_id=?', fn['id'])
                    if previous and (previous['state'] == 'rotated' or (loop and previous['state'] == 'completed')):
                        with lock:
                            visited_turns.add(fn['id'])
                            stats['rotated'] += int(previous['state'] == 'rotated')

                def search(item, blocked, execute=execute, failed=failed):
                    if failed.is_set():
                        return
                    try:
                        return execute(item, blocked)
                    except BaseException:
                        failed.set()
                        raise

                chunk = min(128, max(8, count * 2))
                for offset in range(0, total, chunk):
                    log.check_cancel()
                    ids = [r['id'] for r in identities[offset:offset + chunk]]
                    rows = {r['id']: r for r in self._store(slug).all(f"""SELECT f.id,f.project_id,f.name,f.size,f.vram,f.unit,f.status,f.best_score,
                        f.best_source,f.target,f.relocs,f.asm,f.seed,d.scope AS pass_scope,d.state AS pass_state,
                        c.expires_at AS claim_expires FROM functions f
                        LEFT JOIN deep_permutation_passes d ON d.function_id=f.id
                        LEFT JOIN claims c ON c.function_id=f.id
                        WHERE f.project_id=? AND f.status='open' AND {IN_BUILD}
                        AND f.id IN (SELECT value FROM json_each(?))""", proj['id'], json.dumps(ids))}
                    for fid in ids:
                        log.check_cancel()
                        preparing(scanned, pending, waiting)
                        scanned += 1
                        fn = rows.get(fid)
                        if fn is None or fid in visited_turns or fid in futures.values():
                            continue
                        if not fn['best_source'] or fn['best_score'] is None:
                            waiting += 1
                            continue
                        # Never-tried/pending candidates need no costly historical-scope comparison.
                        if (fn['pass_state'] == 'error' or (not loop and fn['pass_state'] == 'completed')) and fn['pass_scope'] == signature(
                                self.net, proj, fn, minutes, variants, profiles, shared):
                            continue
                        pending += 1
                        if slug in self.net.links and (fn['claim_expires'] or 0) > time.time():
                            waiting += 1
                            continue
                        while len(futures) >= count * 2:
                            log.check_cancel()
                            finished, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                            for future in finished:
                                futures.pop(future)
                                future.result()
                        futures[pool.submit(search, {'id': fid}, waiting)] = fid
                        if fid in order:
                            order.remove(fid)
                        order.append(fid)
                        submitted += 1
                preparing(scanned, pending, waiting, done=True)
                if loop and visited_turns and not submitted:
                    cycle += 1
                    visited_turns.clear()
                    stats['activity'].publish({'cycle': cycle}, force=True)
                    log(f'Starting deep pass {cycle} with the same settings.')
                    continue
                if futures:
                    # Recheck blocked inputs periodically even while a slow turn is running.
                    finished, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                    for future in finished:
                        futures.pop(future)
                        future.result()
                    continue  # Refill free workers without waiting for the longest turn.
                if not pending and not waiting:
                    self.save(slug, self.config(slug) | {'enabled': False})
                    log('Deep pass finished. Completed bounded passes are saved; this is not an exhaustive search of all C.')
                    if stats['rotated']:
                        log(f"{stats['rotated']} functions rotated early and remain eligible for the next run.")
                    return {'functions_tested': stats['visited'], 'tested': stats['tested'], 'matched': stats['matched'], 'rotated':stats['rotated']}
                stats['activity'].publish({'phase': 'waiting for host claims or scored C', 'functions_tested': stats['visited'],
                              'matches': stats['matched'], 'waiting': waiting, 'total_unmatched': total})
                # Cancellation remains responsive while another worker owns the remaining functions.
                for _ in range(POLL_SECONDS):
                    log.check_cancel()
                    time.sleep(1)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def search_one(self, slug, proj, fn, scope, minutes, variants, log, stats, lock, total, blocked):
        """One function's bounded turn, on whichever thread is free."""
        log.check_cancel()
        activity = stats["activity"]
        activity.update(fn["name"])
        build_scope = candidate_scope.key(self.net, proj, fn)
        self.record(fn, scope, 'running', minutes, variants)
        log(f'Deep pass: {fn["name"]} · up to {minutes} minutes / {variants:,} variants')

        def progress(value):
            activity.update(fn["name"], value)
            activity.publish({'function': fn['name'], 'functions_tested': stats['visited'], 'matches': stats['matched'],
                          'total_unmatched': total, 'waiting': blocked} | value, force=False)

        def function_log(line):
            activity.update(fn["name"], line=line)
            log(f'{fn["name"]}: {line}')
        function_log.check_cancel = log.check_cancel
        function_log.progress = progress
        try:
            count = self.config(slug).get('compiler_profiles', 1)
            per_function = self.config(slug).get('threads_per_function', 1)
            if per_function > 1:
                result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log,
                                           compiler_profiles=count, threads_per_function=per_function)
            elif count == 1:
                result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log)
            else:
                result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log, compiler_profiles=count)
            # Also records test backends and older search providers that do not publish history.
            # The actual single-function search uses the same completion gate.
            self.completed(slug, fn, minutes, variants, result, build_scope)
            activity.update(fn['name'], {'phase': 'matched' if result['matched'] else 'finished', **result}, finished=True)
            with lock:
                stats['visited'] += 1
                stats['tested'] += result['tested']
                stats['matched'] += int(result['matched'])
        except NetworkError as error:
            # Claim races are temporary. A bad unchanged candidate stays visible as an error,
            # not completed, and a changed source/header or an explicit repeat retries it.
            busy = self._store(slug).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time())
            from .deep_room import unavailable
            claim_wait = unavailable(error) or bool((busy and slug in self.net.links) or 'claim' in str(error).lower() or str(error).startswith('This function is working'))
            self.record(fn, scope, 'pending' if claim_wait else 'error', minutes, variants, error=str(error))
            activity.update(fn['name'], {'phase': 'waiting' if claim_wait else 'error', 'error': str(error)}, line=str(error), finished=True)
            log(f'{fn["name"]}: {error}')
        except BaseException:
            activity.update(fn['name'], {'phase': 'interrupted'}, finished=True)
            self.record(fn, scope, 'pending', minutes, variants)
            raise
        activity.publish({'phase': 'moving to next function', 'tested': stats['tested'], 'functions_tested': stats['visited'],
                  'matches': stats['matched']})
