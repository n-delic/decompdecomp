"""Durable project-wide deep passes; each candidate gets a bounded turn through the normal judges."""
import hashlib
import json
import logging
import math
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, wait
from contextlib import nullcontext

from .. import candidate_scope, claim_stalls, scoring
from ..network import IN_BUILD, NetworkError
from . import deep_permuter, deep_source_recovery, deep_throughput, targeted_retry, workers
from .deep_activity import Activity
from .purpose_work import Executor

POLL_SECONDS = 15
QUEUE_CACHE_SECONDS = 10
# Scope checks need candidate inputs, never archived matched source or unrelated function metadata.
SCOPE_COLUMNS = ",".join("f."+name for name in ("id","project_id","name","status","best_source","best_score",
    "unit","vram","rom_offset","asm","seed","relocs","target"))
SCHEMA = '''CREATE TABLE IF NOT EXISTS deep_permutation_passes(
 function_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
 minutes INTEGER NOT NULL, max_variants INTEGER NOT NULL, completed_at REAL,
 result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')'''


def looking(net):
    """The pass's own bookkeeping reads the database as it stands, without first syncing the saved folder.

    A pass on a 3,000-function game waited 25,000 s in all (up to 327 s at a time) for saved-folder verification
    before each function's turn, with its worker processes idle, and one such check that failed ("you hold another
    claim") ended the whole pass. The app's autosave and folder watch keep syncing the folder, and the lanes
    already search without it (Progress.passive); what a turn changes is marked for that sync by the progress_dirty
    triggers."""
    progress = getattr(net, 'progress', None)
    return progress.view() if progress is not None else nullcontext()


SIZE_CLASSES = (20, 60, 200)  # instructions: a function this small is a quick pass, and the small ones are where deep passes match


DEFAULT_VARIANTS = 2500  # candidates per function a pass starts with (a function that is going to match usually does within a few thousand)
# Which functions a pass searches first (the setting `function_order`): name -> what the form says.
FUNCTION_ORDERS = {
    'smart': 'Near misses first, then small functions',
    'closest': 'Closest to matching (lowest score for its size)',
    'lowest_score': 'Lowest score first',
    'highest_score': 'Highest score first',
    'smallest': 'Smallest first',
    'largest': 'Largest first',
    'address': 'In address order',
    'random': 'In random order',
}


def size_class(instructions):
    """0..3 for up to 20, 21-60, 61-200 and over 200 instructions; a function of unknown size counts as middling."""
    if not instructions:
        return 2
    return sum(instructions > limit for limit in SIZE_CLASSES)


def closeness_order(rows, details):
    """`rows` (already shuffled; `details` maps an id to its best_score and n_insns) in the order a sweep should spend its
    time: near misses first, then the small functions before the large ones.

    A function a few instructions from matching is much cheaper to finish than one that differs everywhere. Past those,
    size predicts a match better than the starting score: on a PS1 game's 214 completed passes the deep search matched
    13 of 19 functions up to 20 instructions in 2 s each, 16 of 62 up to 60 in 12 s, 7 of 75 up to 200 in 27 s, and none of
    the 58 larger ones in 104 s each (a hundred minutes). So: near misses (a score under an eighth of an instruction's
    worth per instruction, or none yet: the baselines come first anyway), then by size class, then by how close within a
    class. Closeness is a coarse bucket of score per instruction (a factor of two apart) so functions about as close keep
    their shuffled order."""
    return sorted(rows, key=lambda row: order_key(row, details))


def order_key(row, details):
    """(0, 0) for a near miss; otherwise (1 + size class, closeness bucket): what closeness_order sorts by."""
    info = details.get(row['id']) or {}
    score, size = info.get('best_score'), info.get('n_insns') or 0
    closeness = int(math.log2(1 + 8 * score / max(8, size))) if score else 0
    return (0, 0) if closeness == 0 else (1 + size_class(info.get('n_insns')), closeness)


def order_functions(rows, details, how):
    """`rows` (already shuffled, so ties stay random) in the order `how` names (FUNCTION_ORDERS); a function whose score,
    size or address is not known yet goes last in every order but the random one."""
    if how == 'smart':
        return closeness_order(rows, details)
    if how == 'random':
        return list(rows)

    def info(row):
        return details.get(row['id']) or {}

    def size(row):
        return info(row).get('n_insns') or (row.get('size') or 0) // 4 or None
    keys = {
        'lowest_score': lambda row: (info(row).get('best_score') is None, info(row).get('best_score') or 0),
        'highest_score': lambda row: (info(row).get('best_score') is None, -(info(row).get('best_score') or 0)),
        'closest': lambda row: (info(row).get('best_score') is None or not size(row),
                                (info(row).get('best_score') or 0) / max(8, size(row) or 8)),
        'smallest': lambda row: (size(row) is None, size(row) or 0),
        'largest': lambda row: (size(row) is None, -(size(row) or 0)),
        'address': lambda row: (info(row).get('vram') is None, info(row).get('vram') or 0),
    }
    return sorted(rows, key=keys[how])


def order_counts(rows, details):
    """How many of `rows` are near misses, then up to 20, 60, 200 instructions and larger (for the log)."""
    counts = [0] * (len(SIZE_CLASSES) + 2)
    for row in rows:
        counts[order_key(row, details)[0]] += 1
    return counts


def signature(net, proj, fn, minutes, variants, compiler_profiles=None, shared=None, residual=None, *, rank_candidates=None):
    sweeps = getattr(net, 'deep_sweeps', None)
    cfg = sweeps.config(proj['slug']) if sweeps and (compiler_profiles is None or rank_candidates is None) else {}
    count = compiler_profiles if compiler_profiles is not None else cfg.get('compiler_profiles', 1)
    if rank_candidates is None:
        rank_candidates = cfg.get('rank_candidates', False)
    rules = shared.get('deep_rules') if shared is not None else None
    if rules is None:
        rules = claim_stalls.repair_rules()
        if shared is not None:
            shared['deep_rules'] = rules
    target = hashlib.sha256(repr(('', candidate_scope.key(net, proj, fn, shared),
                                  targeted_retry.general_rules(rules))).encode()).hexdigest()
    inputs = ('deep-pass-profiles-v1', count, target, fn['best_source'], minutes, variants)
    if rank_candidates:
        inputs += ('cpu-constant-order-v1',)
    fork = shared['fork'] if shared is not None and 'fork' in shared else net.fork_slug(proj['slug'])
    if not fork and (stamp := targeted_retry.stamp(proj, fn, residual, rules)):
        inputs += (stamp,)
    # Retry the formerly blocked states once under their recovery rules.
    if fn['status'] == 'open' and fn['best_score'] == 0:
        inputs += ('reverify-zero-v1',)
    elif fn['best_source'] and not fork:
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
        self.started_at = {}
        self.queue_cache = {}
        self.lock = threading.RLock()
        self.ready = set()
        self._prepare(net.store)

    def _prepare(self, store):
        if store in self.ready:return store
        with store.tx():
            if store in self.ready:return store
            self._install(store)
            store.after_commit(lambda: self.ready.add(store))
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
        rows = self._store(project_id).all("""SELECT m.family,
            CASE WHEN m.phase='compiler preflight' AND
                 (m.family='random_rewrite_unattributed' OR m.family GLOB 'perm_*')
                 THEN 'random rewrites' ELSE m.phase END AS phase,
            SUM(m.attempted) attempted,
            SUM(m.compile_failed) compile_failed,SUM(m.unscored) unscored,SUM(m.unchanged) unchanged,
            SUM(m.worse) worse,SUM(m.improved) improved,SUM(m.matched) matched,SUM(m.elapsed_ms) elapsed_ms,
            SUM(m.score_gain) score_gain
            FROM deep_permutation_measurements m JOIN functions f ON f.id=m.function_id
            WHERE f.project_id=? GROUP BY 1,2 ORDER BY improved DESC,attempted DESC""", project_id)
        return [dict(row) for row in rows]

    def recipe_wins(self, project_id):
        rows = self._store(project_id).all("""SELECT w.family,w.previous_score,w.new_score,w.provenance,w.created_at,
            f.name AS function FROM deep_permutation_recipe_wins w JOIN functions f ON f.id=w.function_id
            WHERE f.project_id=? AND w.id>COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key=?),0)
            ORDER BY w.id DESC LIMIT 50""", project_id, f'deep-metrics-reset:{project_id}')
        wins = []
        for row in rows:
            provenance = json.loads(row['provenance'])
            if provenance.get('kind') == 'random rewrite':
                provenance['phase'] = 'random rewrites'
            wins.append(dict(row) | {'provenance': provenance})
        return wins

    def config(self, slug):
        with looking(self.net):  # (its settings row: no saved files needed)
            proj = self.net.project_row(slug)
        row = self._store(slug).one('SELECT value FROM settings WHERE key=?', f'deep-sweep:{proj["id"]}')
        return {'threads': 0, 'threads_per_function': 1, 'compiler_profiles': 1, 'loop': False, 'rank_candidates': False, 'function_order': 'smart'} | (json.loads(row['value']) if row else {'enabled': False, 'minutes': 5, 'max_variants': DEFAULT_VARIANTS})

    def save(self, slug, cfg):
        with looking(self.net):
            proj = self.net.project_row(slug)
        with self._store(slug).tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (f'deep-sweep:{proj["id"]}', json.dumps(cfg)))

    def stalled(self, slug, fn):
        """Passes in a row that finished this function with no improvement (0 when it improved, or is new)."""
        row = self._store(slug).one('SELECT result FROM deep_permutation_passes WHERE function_id=?', fn['id'])
        try:
            return max(0, int(json.loads(row['result']).get('stalled_passes', 0))) if row else 0
        except (ValueError, TypeError):
            return 0

    def history(self, slug, fn):
        row = self._store(slug).one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
        if not row:
            return {'state': 'not tried', 'completed': False}
        result = dict(row)
        result['result'] = json.loads(row['result'])
        result['stale'] = row['scope'] != signature(self.net, self.net.project_row(slug), fn,
                                                   row['minutes'], row['max_variants'], None, None,
                                                   result['result'].get('targeted_retry'))
        result['completed'] = row['state'] == 'completed' and not result['stale']
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
        cfg = self.config(proj['slug'])
        return self._store(proj['id']).read_cache.get(('deep-attention', proj['id']),
            (cfg['compiler_profiles'], cfg['rank_candidates'], self._store(proj['id']).read_version('inputs', proj['id']), self._store(proj['id']).read_version('passes', proj['id']), claim_stalls.repair_rules()),
            lambda: self._attention(proj), ttl=QUEUE_CACHE_SECONDS if self.net.fork_slug(proj['slug']) else None)

    def _attention(self, proj):
        """Only failures that still block this build's unchanged, eligible candidates."""
        rows = self._store(proj['id']).all(
            f'SELECT {SCOPE_COLUMNS},d.scope AS pass_scope,d.error AS pass_error,d.minutes AS pass_minutes, '
            "d.max_variants AS pass_variants,json_extract(d.result,'$.targeted_retry') AS pass_residual FROM functions f "
            'JOIN deep_permutation_passes d ON d.function_id=f.id '
            f"WHERE f.project_id=? AND f.status='open' AND f.{IN_BUILD} AND d.state='error'",
            proj['id'])
        from .deep_room import unavailable
        issues = []
        shared = candidate_scope.batch_context(self.net, proj)
        cfg = self.config(proj['slug'])
        profiles = cfg['compiler_profiles']
        for fn in rows:
            if unavailable(fn['pass_error']):
                continue
            if not fn['best_source'] or fn['best_score'] is None:
                continue
            if fn['pass_scope'] != signature(self.net, proj, fn, fn['pass_minutes'], fn['pass_variants'], profiles, shared, fn['pass_residual'], rank_candidates=cfg['rank_candidates']):
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
            issues.append({'function': fn['name'], 'error': error, 'action': action, 'kind': 'search'})
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

    def runtime(self, slug, cfg):
        started = self.started_at.get(slug)
        return cfg.get('runtime_seconds', 0) + (time.monotonic() - started if started is not None else 0)

    def finish_runtime(self, slug, cfg):
        if slug in self.started_at:
            cfg = cfg | {'runtime_seconds': self.runtime(slug, cfg)}
            self.save(slug, cfg)
            del self.started_at[slug]

    def timed_run(self, slug, minutes, variants, log, threads, loop):
        with self.lock:
            log.check_cancel()
            self.started_at[slug] = time.monotonic()
        try:
            return self.run(slug, minutes, variants, log, threads, loop)
        finally:
            with self.lock:
                if slug in self.started_at:
                    self.finish_runtime(slug, self.config(slug))

    def control(self, slug):
        """Discover work and controls without scanning saved searches or build inputs."""
        with self.lock:
            cfg = self.config(slug)
            runtime = self.runtime(slug, cfg)
            current = dict(self.active.get(slug, {}))
        with self.jobs._lock:
            job = self.jobs.jobs.get(current.get('job'), {})
            live = {key:job[key] for key in ('state', 'progress', 'error') if key in job}
        return cfg | {'control_only':True, 'threads_default':workers.default_workers(), 'threads_default_maximum':deep_throughput.lanes(True),
                      'runtime_seconds':runtime, 'has_activity':bool(job or cfg.get('metrics_started')), 'state':'idle', **current, **live}

    def status(self, slug):
        # A status poll must never queue behind a writer to repair old pass records.
        # The search worker performs recovery; until then transport errors and rotations are retryable.
        proj = self.net.project_row(slug)
        # Imported projects keep their C in source files until a search retains a baseline.
        source_condition = '1' if self.net.fork_slug(slug) else 'best_source IS NOT NULL'
        counts = self._store(slug).one(f"SELECT COUNT(*) total, SUM(({source_condition}) AND best_score IS NOT NULL) ready "
                                    f"FROM functions WHERE project_id=? AND status='open' AND {IN_BUILD}", proj['id'])
        completed = self._store(slug).one("SELECT COUNT(*) n FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id "
                                      "WHERE f.project_id=? AND d.state='completed'", proj['id'])['n']

        improvements = self.improvements(proj['id'])
        attention = self.attention(proj) + deep_source_recovery.issues(self._store(slug), proj['id'])
        errors = len(attention)
        # Matching the current build can inspect project files. Cache the result briefly so opening
        # details or polling a running pass does not repeatedly hash every completed function.
        ready = counts['ready'] or 0
        with self.lock:
            cfg = self.config(slug)
            runtime = self.runtime(slug, cfg)
            current = dict(self.active.get(slug, {}))
        def count_queue():
            shared = candidate_scope.batch_context(self.net, proj)
            done_open = done_matched = rotated = blocked = 0
            rows = self._store(slug).all(f"""SELECT {SCOPE_COLUMNS},d.error AS pass_error,d.scope AS pass_scope,d.minutes AS pass_minutes,
                d.max_variants AS pass_variants,d.state AS pass_state,
                json_extract(d.result,'$.targeted_retry') AS pass_residual FROM functions f JOIN deep_permutation_passes d ON d.function_id=f.id
                WHERE f.project_id=? AND f.gone_mark IS NULL AND f.status IN ('open','matched')
                AND d.state IN ('completed','error') AND f.best_source IS NOT NULL AND f.best_score IS NOT NULL
                AND d.minutes=? AND d.max_variants=?""", proj['id'], cfg['minutes'], cfg['max_variants'])
            from .deep_room import unavailable
            for row in rows:
                if row['pass_state'] == 'error' and unavailable(row['pass_error']):
                    continue
                if (row['best_source'] and row['best_score'] is not None
                        and row['pass_minutes'] == cfg['minutes'] and row['pass_variants'] == cfg['max_variants']
                        and row['pass_scope'] == signature(self.net, proj, row, cfg['minutes'], cfg['max_variants'], cfg['compiler_profiles'], shared, row['pass_residual'], rank_candidates=cfg['rank_candidates'])):
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
            (cfg['minutes'], cfg['max_variants'], cfg['compiler_profiles'], cfg['rank_candidates'], self._store(slug).read_version('inputs', proj['id']), self._store(slug).read_version('passes', proj['id']), claim_stalls.repair_rules()),
            count_queue, ttl=QUEUE_CACHE_SECONDS if self.net.fork_slug(slug) else None)
        job = self.jobs.jobs.get(current.get('job'), {})
        recipes, measurements, wins = self.recipes(proj['id']), self.measurements(proj['id']), self.recipe_wins(proj['id'])
        reset = self._store(slug).one('SELECT value FROM settings WHERE key=?', f'deep-metrics-reset:{proj["id"]}')
        has_activity = bool(job or improvements or recipes or measurements or wins
                            or cfg.get('metrics_started', bool((completed or errors) and not reset)))
        return cfg | {'runtime_seconds': runtime, 'has_activity': has_activity, 'threads_default': workers.default_workers(), 'threads_default_maximum': deep_throughput.lanes(True),
                      'total': counts['total'], 'ready': ready, 'queue_done': done_open + done_matched,
                      'queue_left': max(0, ready - done_open - blocked), 'queue_rotated': rotated, 'queue_blocked': blocked, 'awaiting_source': max(0, counts['total'] - ready),
                                   'improved': len(improvements), 'improvements': improvements, 'recipes': recipes, 'recipe_wins': wins,
                                   'measurements': measurements,
                                   'completed': completed, 'errors': errors, 'attention': attention, 'state': job.get('state', 'idle'), **current,
                                   'progress': job.get('progress', {}), 'error': job.get('error')}

    def start(self, slug, minutes=5, max_variants=DEFAULT_VARIANTS, repeat=False, threads=0, compiler_profiles=1, loop=False, threads_per_function=1, maximum_throughput=False, compile_slots=0, rank_candidates=False, function_order='smart'):
        throughput = deep_throughput.options(maximum_throughput, compile_slots)
        from . import deep_profiles
        if not isinstance(loop, bool):
            raise NetworkError('Loop indefinitely must be true or false.')
        if not isinstance(rank_candidates, bool):
            raise NetworkError('CPU constant ranking must be true or false.')
        if function_order not in FUNCTION_ORDERS:
            raise NetworkError('Choose which functions to search first: ' + ', '.join(FUNCTION_ORDERS) + '.')
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
                    pid = self.net.project_row(slug)['id']
                    db.execute("DELETE FROM deep_permutation_passes WHERE function_id IN "
                               "(SELECT id FROM functions WHERE project_id=? AND status='open')", (pid,))
                    deep_source_recovery.reset(db, pid)
            self.save(slug, {'runtime_seconds': self.config(slug).get('runtime_seconds', 0),
                             'enabled': True, 'minutes': minutes, 'max_variants': max_variants, 'threads': threads, 'threads_per_function': threads_per_function, 'compiler_profiles': compiler_profiles, 'metrics_started': True, 'loop': loop, 'rank_candidates': rank_candidates, 'function_order': function_order, **throughput})
            job = self.jobs.start('deep-permutation-pass', lambda log: self.timed_run(slug, minutes, max_variants, log, threads, loop),
                                  project=slug, cancel_message='Deep pass stopped. Verified improvements and completed passes are saved.')
            self.active[slug] = {'job': job}
        return self.control(slug)

    def stop(self, slug):
        with self.lock:
            cfg = self.config(slug)
            cfg = cfg | {'enabled': False}
            if slug in self.started_at:
                self.finish_runtime(slug, cfg)
            else:
                self.save(slug, cfg)
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
            for slug, current in self.active.items():
                self.jobs.cancel(current['job'])
                if slug in self.started_at:
                    self.finish_runtime(slug, self.config(slug))

    def completed(self, slug, before, minutes, variants, result, build_scope):
        self.improvement(before, result.get('best_score'))
        fresh = self.net._function(before['name'], slug)
        start, end = before['best_score'], fresh['best_score']
        improved = fresh['status'] == 'matched' or (start is not None and end is not None and end < start)
        result['stalled_passes'] = 0 if improved else self.stalled(slug, before) + 1
        proj = self.net.project_row(slug)
        stable = build_scope == candidate_scope.key(self.net, proj, before)
        self.record(fresh, signature(self.net, proj, fresh, minutes, variants, result.get('compiler_profiles'),
                                     None, result.get('targeted_retry'), rank_candidates=result.get('rank_candidates')),
                    'completed' if stable else 'pending', minutes, variants, result)

    def reopen_without_randomizer(self, slug) -> int:
        """Passes that finished with only the simple rewrites go back to pending once decomp-permuter is here:
        those that say so in their result, and older ones (no such record) that finished before it was installed."""
        from . import permute
        installed = permute.installed_at()
        if installed is None:
            return 0
        with self._store(slug).tx() as db:
            cur = db.execute("UPDATE deep_permutation_passes SET state='pending',completed_at=NULL WHERE state='completed' "
                             "AND (json_extract(result,'$.randomizer')=0 OR (json_extract(result,'$.randomizer') IS NULL "
                             "AND completed_at<?))", (installed,))
            count = cur.rowcount if cur is not None and cur.rowcount and cur.rowcount > 0 else 0
        if count:
            with self.lock:
                self.queue_cache.clear()
        return count

    def record(self, fn, scope, state, minutes, variants, result=None, error=''):
        with self.lock:
            self.queue_cache.clear()
        result = dict(result or {})
        if 'stalled_passes' not in result and (kept := self.stalled(fn['project_id'], fn)):
            result['stalled_passes'] = kept  # an error or a reopened pass keeps the count it had
        with self._store(fn['project_id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO deep_permutation_passes '
                       '(function_id,scope,state,minutes,max_variants,completed_at,result,error) VALUES(?,?,?,?,?,?,?,?)',
                       (fn['id'], scope, state, minutes, variants, time.time() if state == 'completed' else None,
                        json.dumps(result), error))

    @deep_throughput.sweep
    def run(self, slug, minutes, variants, log, threads=1, loop=False):
        """Keep a bounded queue moving; optionally repeat eligible turns until stopped.

        Blocked functions never occupy a worker. Sleep only when no useful turn is available.
        """
        self.recover_room_transport(slug)
        from . import permute
        if permute.ensure_available(log):
            reopened = self.reopen_without_randomizer(slug)
            if reopened:
                log(f'Repeating {reopened} deep searches that finished before decomp-permuter was installed.')
        else:
            log(permute.MISSING + ' Install it from Compilers & tools: searches made without it are repeated afterwards.')
        from .sdk_assembly import discover_known
        log.check_cancel()
        with looking(self.net):
            sdk_set_aside = discover_known(self.net, self.net.project_row(slug), check_cancel=log.check_cancel)
        if sdk_set_aside:
            log(f'Identified {sdk_set_aside} SDK library functions before preparing C candidates.')
        twin_trials = twin_matches = recovered_matches = recovery_trials = 0
        unscored = set()
        if slug not in self.net.links:
            from .. import compiler
            from .boundary_repairs import blocked

            with looking(self.net):
                proj = self.net.project_row(slug)
                if self.net.is_rom(proj) and not self.net.fork_slug(slug):
                    from . import match_reconciliation
                    recovered = match_reconciliation.run(self.net, slug, None, None, log, automatic=True)
                    log.check_cancel()
                    recovered_matches, recovery_trials = recovered['matched'], recovered['attempts']
                unscored = {row['id'] for row in self._store(slug).all(
                    "SELECT id FROM functions WHERE project_id=? AND status='open' "
                    "AND (best_source IS NULL OR best_score IS NULL) AND " + IN_BUILD, proj['id'])}
                if unscored:
                    eligible = unscored - blocked(self.net, proj)
                    agent, _ = self.net.local_agent('autopilot')
                    for donor, target in self.net.twins.pairs(proj):
                        log.check_cancel()
                        if target in eligible:
                            with compiler.cancellable(log.check_cancel):
                                outcome = self.net.twins.try_pair(agent, proj, donor, target)
                            twin_trials += int(outcome in ('match', 'miss'))
                            twin_matches += int(outcome == 'match')
                    if twin_trials:
                        log(f'Tried matched twins for {twin_trials} functions without scored C; {twin_matches} matched.')
        maximum = getattr(log.compile_pool, 'maximum', False)
        count = deep_throughput.lanes(True) if maximum and not threads else workers.clamp(threads) if threads != 1 else 1
        stats = {'visited': twin_trials + recovered_matches, 'tested': twin_trials + recovery_trials, 'matched': twin_matches + recovered_matches, 'rotated':0, 'activity': Activity(log)}
        visited_turns = set()
        lock = threading.Lock()  # (the counters are shared by every thread's function)
        cycle = 1
        order = []
        futures = {}
        pool = Executor(max_workers=count, purpose='Deep search functions', project=slug, thread_name_prefix='deep-pass', initializer=None if maximum else workers.started_priority)
        # Several functions at once each run in a child process of their own (deep_lanes.py): one interpreter
        # judging every candidate capped a pass near a hundred a second. A room's project stays in the app (its
        # host confirms matches through this engine's room connection).
        from . import deep_lanes
        compile_pool = getattr(log, 'compile_pool', None)
        # A maximum pass with no slot count set sizes each lane itself (deep_lanes.Lanes); a number the user chose is split.
        lane_slots = int(self.config(slug).get('compile_slots', 0) or 0) if maximum else getattr(compile_pool, 'count', 0)
        log.deep_lanes = (deep_lanes.Lanes(self.net, count, lane_slots, maximum=maximum)
                          if count > 1 and slug not in self.net.links and deep_lanes.enabled() else None)
        if log.deep_lanes is not None:
            compile_pool.metrics.remote_slots = log.deep_lanes.count * log.deep_lanes.slots
        stats['activity'].publish({'cycle': cycle, 'loop': loop,
            'preparation': {'checked': 0, 'total': 0, 'eligible': 0, 'waiting': 0, 'done': False}}, force=True)
        try:
            while True:
                log.check_cancel()
                with looking(self.net):
                    proj = self.net.project_row(slug)
                candidates = []
                waiting = 0
                # Shuffle lightweight identities first. Never fingerprint the entire project before
                # dispatching work; keep at most two turns per worker queued ahead of execution.
                identities = [dict(r) for r in self._store(slug).all(f"SELECT id,name,size FROM functions WHERE project_id=? AND status='open' AND {IN_BUILD}", proj['id'])]
                from .boundary_repairs import blocked as boundary_blocked
                held=boundary_blocked(self.net,proj)
                if cycle == 1 and not order:
                    boundary_count = sum(row['id'] in held for row in identities)
                    if boundary_count:
                        log(f'{boundary_count} functions need boundary review. See Settings → Code coverage → Review & repair boundaries.')
                identities=[r for r in identities if r["id"] not in held]
                if not order:
                    random.shuffle(identities)
                    details = {r['id']: dict(r) for r in self._store(slug).all(
                        f"SELECT id,best_score,n_insns,vram FROM functions WHERE project_id=? AND status='open' AND {IN_BUILD}", proj['id'])}
                    how = self.config(slug).get('function_order', 'smart')
                    how = how if how in FUNCTION_ORDERS else 'smart'
                    identities = order_functions(identities, details, how)
                    if cycle == 1 and how == 'smart':
                        near, *sizes = order_counts(identities, details)
                        log(f'Searching near misses first ({near}), then smaller functions: {sizes[0]} of up to {SIZE_CLASSES[0]} '
                            f'instructions, {sizes[1]} of up to {SIZE_CLASSES[1]}, {sizes[2]} of up to {SIZE_CLASSES[2]}, {sizes[3]} larger.')
                    elif cycle == 1:
                        log(f'Searching functions {FUNCTION_ORDERS[how][0].lower() + FUNCTION_ORDERS[how][1:]}.')
                    # Restore missing baselines before the bounded search queue fills.
                    identities.sort(key=lambda row: row['id'] not in unscored)
                    order.extend(r["id"] for r in identities)
                ranks = {fid: i for i, fid in enumerate(order)}
                identities.sort(key=lambda r: ranks.get(r["id"], len(ranks)))
                total = len(identities)
                scanned = submitted = 0
                shared = {'contexts': {}}
                cfg = self.config(slug)
                profiles = cfg['compiler_profiles']
                activity = stats['activity']
                activity.prepare(scanned, total, candidates, waiting)

                failed = threading.Event()
                def execute(item, blocked, pid=proj['id'], total=total, minutes=minutes, variants=variants,
                            expanded_budget=cycle > 1):
                    log.check_cancel()
                    store = self._store(slug)
                    proj = store.one('SELECT * FROM projects WHERE id=?', pid)
                    fn = store.one('SELECT * FROM functions WHERE id=? AND project_id=?', item['id'], pid)
                    if not proj or not fn or fn['status'] != 'open' or fn['gone_mark'] is not None:
                        return
                    # Dispatch always uses fresh inputs, never the producer's shared snapshot.
                    previous = store.one(
                        "SELECT scope,state,json_extract(result,'$.targeted_retry') AS residual "
                        "FROM deep_permutation_passes WHERE function_id=?", fn['id'])
                    with looking(self.net):
                        scope = signature(self.net, proj, fn, minutes, variants, None, None,
                                          previous['residual'] if previous else None)
                    if previous and previous['scope'] == scope and (previous['state'] == 'error' or (not loop and previous['state'] == 'completed')):
                        return
                    self.search_one(slug, proj, fn, scope, minutes, variants, log, stats, lock, total, blocked,
                                    expanded_budget=expanded_budget)
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
                        json_extract(d.result,'$.targeted_retry') AS pass_residual,c.expires_at AS claim_expires FROM functions f
                        LEFT JOIN deep_permutation_passes d ON d.function_id=f.id
                        LEFT JOIN claims c ON c.function_id=f.id
                        WHERE f.project_id=? AND f.status='open' AND {IN_BUILD}
                        AND f.id IN (SELECT value FROM json_each(?))""", proj['id'], json.dumps(ids))}
                    for fid in ids:
                        log.check_cancel()
                        activity.prepare(scanned, total, candidates, waiting)
                        scanned += 1
                        fn = rows.get(fid)
                        if fn is None or fid in visited_turns or fid in futures.values():
                            continue
                        with looking(self.net):
                            fn = deep_permuter.prepare_source(self.net, proj, fn)
                            if not fn['best_source'] or fn['best_score'] is None:
                                fn = {**dict(fn), **dict(deep_source_recovery.prepare(self.net, proj, fn, log))}
                                if fn['status'] == 'matched':
                                    with lock:
                                        stats['matched'] += 1
                            if fn['status'] != 'open' or dict(fn).get('gone_mark') is not None:
                                continue
                            if not fn['best_source'] or fn['best_score'] is None:
                                waiting += 1
                                continue
                            # Never-tried/pending candidates need no costly historical-scope comparison.
                            if (fn['pass_state'] == 'error' or (not loop and fn['pass_state'] == 'completed')) and fn['pass_scope'] == signature(
                                    self.net, proj, fn, minutes, variants, profiles, shared, fn['pass_residual'], rank_candidates=cfg['rank_candidates']):
                                continue
                        candidates.append(fn['name'])
                        if slug in self.net.links and (fn['claim_expires'] or 0) > time.time():
                            waiting += 1
                            continue
                        activity.prepare(scanned, total, candidates, waiting)
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
                activity.prepare(scanned, total, candidates, waiting, done=True)
                if loop and visited_turns and not submitted and not futures:
                    cycle += 1
                    minutes *= 2
                    variants *= 2
                    visited_turns.clear()
                    stats['activity'].publish({'cycle': cycle}, force=True)
                    log(f'Starting deep pass {cycle} with up to {minutes} minutes and {variants:,} variants per function.')
                    continue
                if getattr(log, 'deep_lanes', None) is not None:
                    # (lanes left without a turn hold processes: close the ones idle for a while, all when waiting)
                    log.deep_lanes.trim(deep_lanes.IDLE_SECONDS if futures else 0)
                if futures:
                    # Recheck blocked inputs periodically even while a slow turn is running.
                    finished, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                    for future in finished:
                        futures.pop(future)
                        future.result()
                    continue  # Refill free workers without waiting for the longest turn.
                if not candidates and not waiting:
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
            if getattr(log, 'deep_lanes', None) is not None:
                log.deep_lanes.close()
                log.deep_lanes = None

    def search_one(self, slug, proj, fn, scope, minutes, variants, log, stats, lock, total, blocked, *, expanded_budget=False):
        """One function's bounded turn, on whichever thread is free."""
        log.check_cancel()
        activity = stats["activity"]
        activity.update(fn["name"], {"n_insns": fn["n_insns"]})
        with looking(self.net):
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
        function_log.compile_pool = getattr(log, 'compile_pool', None)
        function_log.check_cancel = log.check_cancel
        function_log.progress = progress
        function_log.refresh_metrics = activity.refresh_metrics
        try:
            cfg = self.config(slug)
            count = cfg.get('compiler_profiles', 1)
            per_function = cfg.get('threads_per_function', 1)
            options = ({'compiler_profiles': count, 'threads_per_function': per_function} if per_function > 1 else
                       {} if count == 1 else {'compiler_profiles': count})
            if cfg.get('rank_candidates', False):
                options['rank_candidates'] = True
            if expanded_budget:
                options['expanded_budget'] = True
            lanes = getattr(log, 'deep_lanes', None)
            result = None
            if lanes is not None:
                from .deep_lanes import Unavailable
                try:
                    result = lanes.search(slug, fn['name'], minutes, variants, function_log, **options)
                except Unavailable:
                    log(f'{fn["name"]}: no worker process could take this turn; it runs in the app.')
            if result is None:
                result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log, **options)
            # Also records test backends and older search providers that do not publish history.
            # The actual single-function search uses the same completion gate.
            with looking(self.net):
                self.completed(slug, fn, minutes, variants, result, build_scope)
            activity.update(fn['name'], {'phase': 'matched' if result['matched'] else 'finished', **result}, finished=True)
            with lock:
                stats['visited'] += 1
                stats['tested'] += result['tested']
                stats['matched'] += int(result['matched'])
        except NetworkError as error:
            # Claim races and input resets are temporary. A bad unchanged candidate stays visible as an error,
            # not completed, and a changed source/header or an explicit repeat retries it.
            busy = self._store(slug).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time())
            from .deep_room import unavailable
            from .progress import ProgressMoved
            claim_wait = isinstance(error, ProgressMoved) or unavailable(error) or bool((busy and slug in self.net.links) or 'claim' in str(error).lower() or str(error).startswith('This function is working'))
            self.record(fn, scope, 'pending' if claim_wait else 'error', minutes, variants, error=str(error))
            activity.update(fn['name'], {'phase': 'waiting' if claim_wait else 'error', 'error': str(error)}, line=str(error), finished=True)
            log(f'{fn["name"]}: {error}')
        except Exception as error:
            # Cancellation still stops the pass; an individual search bug must not.
            try:
                log.check_cancel()
            except BaseException:
                activity.update(fn['name'], {'phase': 'interrupted'}, finished=True)
                self.record(fn, scope, 'pending', minutes, variants)
                raise
            logging.getLogger('decompdecomp').exception('Deep search failed for %s; continuing with other functions', fn['name'])
            message = f'{type(error).__name__}: {error}'
            self.record(fn, scope, 'error', minutes, variants, error=message)
            activity.update(fn['name'], {'phase': 'error', 'error': message}, line=message, finished=True)
            log(f'{fn["name"]}: {message}; continuing with other functions.')
        except BaseException:
            activity.update(fn['name'], {'phase': 'interrupted'}, finished=True)
            self.record(fn, scope, 'pending', minutes, variants)
            raise
        activity.publish({'phase': 'moving to next function', 'tested': stats['tested'], 'functions_tested': stats['visited'],
                  'matches': stats['matched']})
