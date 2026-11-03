"""Durable project-wide deep passes; each candidate gets a bounded turn through the normal judges."""
import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import candidate_scope, scoring
from ..network import IN_BUILD, NetworkError
from . import agent_tokens, deep_permuter, workers
from .deep_activity import Activity

POLL_SECONDS = 15
QUEUE_CACHE_SECONDS = 10
SCHEMA = '''CREATE TABLE IF NOT EXISTS deep_permutation_passes(
 function_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
 minutes INTEGER NOT NULL, max_variants INTEGER NOT NULL, completed_at REAL,
 result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')'''


def signature(net, proj, fn, minutes, variants):
    inputs = ('deep-pass-v1', agent_tokens.target_scope(net, fn, ''), fn['best_source'], minutes, variants)
    # Retry the two formerly blocked states once under the new recovery rules. Keep unrelated
    # completed passes resumable; users can explicitly repeat them to try the new search recipes.
    if fn['status'] == 'open' and fn['best_score'] == 0:
        inputs += ('reverify-zero-v1',)
    elif fn['best_source'] and not net.fork_slug(proj['slug']):
        from .prototype_alignment import variants as align
        ctx = net._context(proj['id'], fn['unit'])
        if ctx and align(fn['best_source'], fn['name'], ctx['header']):
            inputs += ('return-alignment-v1',)
    return hashlib.sha256(repr(inputs).encode()).hexdigest()


class Sweeps:
    def __init__(self, net, jobs):
        self.net, self.jobs = net, jobs
        self.active = {}
        self.queue_cache = {}
        self.lock = threading.RLock()
        with net.store.tx() as db:
            db.execute(SCHEMA)
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

    def record_recipes(self, function_id, totals, wins, measurements=None):
        """Save counts and individual verified improvements, including searches stopped part way through."""
        with self.net.store.tx() as db:
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
        with self.net.store.tx() as db:
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
        rows = self.net.store.all("""SELECT r.family, SUM(r.tested) tested, SUM(r.skipped) skipped,
            SUM(r.rejected) rejected, SUM(r.improvements) improvements, SUM(r.matches) matches,
            SUM(r.score_gain) score_gain, COUNT(DISTINCT r.function_id) functions
            FROM deep_permutation_recipes r JOIN functions f ON f.id=r.function_id
            WHERE f.project_id=? GROUP BY r.family ORDER BY improvements DESC, tested DESC, r.family""", project_id)
        return [dict(row) for row in rows]

    def measurements(self, project_id):
        rows = self.net.store.all("""SELECT m.family,m.phase,SUM(m.attempted) attempted,
            SUM(m.compile_failed) compile_failed,SUM(m.unscored) unscored,SUM(m.unchanged) unchanged,
            SUM(m.worse) worse,SUM(m.improved) improved,SUM(m.matched) matched,SUM(m.elapsed_ms) elapsed_ms,
            SUM(m.score_gain) score_gain
            FROM deep_permutation_measurements m JOIN functions f ON f.id=m.function_id
            WHERE f.project_id=? GROUP BY m.family,m.phase ORDER BY improved DESC,attempted DESC""", project_id)
        return [dict(row) for row in rows]

    def recipe_wins(self, project_id):
        rows = self.net.store.all("""SELECT w.family,w.previous_score,w.new_score,w.provenance,w.created_at,
            f.name AS function FROM deep_permutation_recipe_wins w JOIN functions f ON f.id=w.function_id
            WHERE f.project_id=? AND w.id>COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key=?),0)
            ORDER BY w.id DESC LIMIT 50""", project_id, f'deep-metrics-reset:{project_id}')
        return [dict(row) | {'provenance': json.loads(row['provenance'])} for row in rows]

    def config(self, slug):
        proj = self.net.project_row(slug)
        row = self.net.store.one('SELECT value FROM settings WHERE key=?', f'deep-sweep:{proj["id"]}')
        return {'threads': 0} | (json.loads(row['value']) if row else {'enabled': False, 'minutes': 5, 'max_variants': 10000})

    def save(self, slug, cfg):
        proj = self.net.project_row(slug)
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (f'deep-sweep:{proj["id"]}', json.dumps(cfg)))

    def history(self, slug, fn):
        row = self.net.store.one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
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
        with self.net.store.tx() as db:
            db.execute('''INSERT INTO deep_permutation_improvements
                (function_id,starting_score,starting_percent,new_score,new_percent,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(function_id) DO UPDATE SET
                new_score=excluded.new_score,new_percent=excluded.new_percent,updated_at=excluded.updated_at
                WHERE excluded.new_score < deep_permutation_improvements.new_score''',
                (before['id'], start, percent(start), score, percent(score), time.time()))

    def improvements(self, project_id):
        rows = self.net.store.all('''SELECT i.*, f.name AS function FROM deep_permutation_improvements i
            JOIN functions f ON f.id=i.function_id WHERE f.project_id=? ORDER BY i.updated_at DESC,f.id''', project_id)
        return [dict(row) | {'starting_percent_text': scoring.percent_text(row['starting_percent']),
                             'new_percent_text': scoring.percent_text(row['new_percent'])} for row in rows]

    def attention(self, proj):
        """Only failures that still block this build's unchanged, eligible candidates."""
        rows = self.net.store.all(
            'SELECT f.*,d.scope AS pass_scope,d.error AS pass_error,d.minutes AS pass_minutes, '
            'd.max_variants AS pass_variants FROM functions f '
            'JOIN deep_permutation_passes d ON d.function_id=f.id '
            f"WHERE f.project_id=? AND f.status='open' AND f.{IN_BUILD} AND d.state='error'",
            proj['id'])
        issues = []
        for fn in rows:
            if not fn['best_source'] or fn['best_score'] is None:
                continue
            if fn['pass_scope'] != signature(self.net, proj, fn, fn['pass_minutes'], fn['pass_variants']):
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

    def status(self, slug):
        proj = self.net.project_row(slug)
        counts = self.net.store.one("SELECT COUNT(*) total, SUM(best_source IS NOT NULL AND best_score IS NOT NULL) ready "
                                    "FROM functions WHERE project_id=? AND status='open'", proj['id'])
        completed = self.net.store.one("SELECT COUNT(*) n FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id "
                                      "WHERE f.project_id=? AND d.state='completed'", proj['id'])['n']

        improvements = self.improvements(proj['id'])
        cfg = self.config(slug)
        attention = self.attention(proj)
        errors = len(attention)
        # Matching the current build can inspect project files. Cache the result briefly so opening
        # details or polling a running pass does not repeatedly hash every completed function.
        ready = counts['ready'] or 0
        cache_key = (proj['id'], cfg['minutes'], cfg['max_variants'])
        with self.lock:
            cached = self.queue_cache.get(cache_key)
            current = dict(self.active.get(slug, {}))
        if cached and cached[0] > time.monotonic():
            done_open, done_matched = cached[1:]
        else:
            done_open = done_matched = 0
            rows = self.net.store.all("""SELECT f.*,d.scope AS pass_scope,d.minutes AS pass_minutes,
                d.max_variants AS pass_variants FROM functions f JOIN deep_permutation_passes d ON d.function_id=f.id
                WHERE f.project_id=? AND d.state='completed' AND f.status IN ('open','matched')""", proj['id'])
            for row in rows:
                if (row['best_source'] and row['best_score'] is not None
                        and row['pass_minutes'] == cfg['minutes'] and row['pass_variants'] == cfg['max_variants']
                        and row['pass_scope'] == signature(self.net, proj, row, cfg['minutes'], cfg['max_variants'])):
                    if row['status'] == 'open':
                        done_open += 1
                    else:
                        done_matched += 1
            with self.lock:
                self.queue_cache[cache_key] = (time.monotonic() + QUEUE_CACHE_SECONDS, done_open, done_matched)
        job = self.jobs.jobs.get(current.get('job'), {})
        recipes, measurements, wins = self.recipes(proj['id']), self.measurements(proj['id']), self.recipe_wins(proj['id'])
        reset = self.net.store.one('SELECT value FROM settings WHERE key=?', f'deep-metrics-reset:{proj["id"]}')
        has_activity = bool(job or improvements or recipes or measurements or wins
                            or cfg.get('metrics_started', bool((completed or errors) and not reset)))
        return cfg | {'has_activity': has_activity, 'threads_default': workers.default_workers(),
                      'total': counts['total'], 'ready': ready, 'queue_done': done_open + done_matched,
                      'queue_left': max(0, ready - done_open), 'awaiting_source': max(0, counts['total'] - ready),
                                   'improved': len(improvements), 'improvements': improvements, 'recipes': recipes, 'recipe_wins': wins,
                                   'measurements': measurements,
                                   'completed': completed, 'errors': errors, 'attention': attention, 'state': job.get('state', 'idle'), **current,
                                   'progress': job.get('progress', {}), 'error': job.get('error')}

    def start(self, slug, minutes=5, max_variants=10000, repeat=False, threads=0):
        if (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120 or
                isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000):
            raise NetworkError('Choose 1–120 minutes and 1–1,000,000 variants per function.')
        if isinstance(threads, bool) or not isinstance(threads, int) or not threads >= 0:
            raise NetworkError('Choose a number of threads, or leave it empty for automatic.')
        self.net.project_row(slug)
        with self.lock:
            previous = self.active.get(slug, {})
            if self.jobs.jobs.get(previous.get('job'), {}).get('state') == 'running':
                raise NetworkError('A deep pass is already running for this project. Stop it before starting another.')
            if repeat:
                self.queue_cache.clear()
                with self.net.store.tx() as db:
                    db.execute("DELETE FROM deep_permutation_passes WHERE function_id IN "
                               "(SELECT id FROM functions WHERE project_id=? AND status='open')",
                               (self.net.project_row(slug)['id'],))
            self.save(slug, {'enabled': True, 'minutes': minutes, 'max_variants': max_variants, 'threads': threads, 'metrics_started': True})
            job = self.jobs.start('deep-permutation-pass', lambda log: self.run(slug, minutes, max_variants, log, threads),
                                  cancel_message='Deep pass stopped. Verified improvements and completed passes are saved.')
            self.active[slug] = {'job': job}
        return self.status(slug)

    def stop(self, slug):
        with self.lock:
            cfg = self.config(slug)
            self.save(slug, cfg | {'enabled': False})
            job = self.active.get(slug, {}).get('job')
            if job:
                self.jobs.cancel(job)
        return self.status(slug)

    def resume(self):
        """Leave interrupted passes stopped; the user starts them again when ready."""
        with self.net.store.tx() as db:
            rows = db.execute("SELECT key,value FROM settings WHERE key LIKE 'deep-sweep:%'").fetchall()
            for row in rows:
                cfg = json.loads(row['value'])
                if cfg.get('enabled'):
                    cfg['enabled'] = False
                    db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(cfg), row['key']))

    def shutdown(self):
        # Stop current searches; their completed records remain available for a manual restart.
        with self.lock:
            for current in self.active.values():
                self.jobs.cancel(current['job'])

    def completed(self, slug, before, minutes, variants, result, build_scope):
        self.improvement(before, result.get('best_score'))
        fresh = self.net._function(before['name'], slug)
        proj = self.net.project_row(slug)
        stable = build_scope == candidate_scope.key(self.net, proj, before)
        self.record(fresh, signature(self.net, proj, fresh, minutes, variants),
                    ('rotated' if str(result.get('reason','')).startswith('Budget rotation:') else 'completed') if stable else 'pending', minutes, variants, result)

    def record(self, fn, scope, state, minutes, variants, result=None, error=''):
        with self.lock:
            self.queue_cache.clear()
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO deep_permutation_passes '
                       '(function_id,scope,state,minutes,max_variants,completed_at,result,error) VALUES(?,?,?,?,?,?,?,?)',
                       (fn['id'], scope, state, minutes, variants, time.time() if state == 'completed' else None,
                        json.dumps(result or {}), error))

    def run(self, slug, minutes, variants, log, threads=1):
        """Finish one turn per unchanged candidate; wait for claims or usable C without marking them done. `threads`
        functions are searched at once (0: what the computer can spare, workers.py); each is its own bounded turn."""
        count = workers.clamp(threads) if threads != 1 else 1
        stats = {'visited': 0, 'tested': 0, 'matched': 0, 'rotated':0, 'activity': Activity(log)}
        visited_turns = set()
        lock = threading.Lock()  # (the counters are shared by every thread's function)
        while True:
            log.check_cancel()
            proj = self.net.project_row(slug)
            pending = waiting = 0
            batch = []
            rows = self.net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY size,id", proj['id'])
            for row in rows:
                log.check_cancel()
                fn = self.net._function(row['name'], slug)
                if fn['status'] != 'open' or fn['id'] in visited_turns:
                    continue
                if not fn['best_source'] or fn['best_score'] is None:
                    waiting += 1
                    continue
                scope = signature(self.net, proj, fn, minutes, variants)
                previous = self.net.store.one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
                if previous and previous['scope'] == scope and previous['state'] in ('completed', 'error'):
                    continue
                pending += 1
                if slug in self.net.links and self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
                    waiting += 1
                    continue
                batch.append((fn, scope))
            # Random order without replacement: interrupted restarts do not always favor small functions.
            random.shuffle(batch)
            total, blocked = len(rows), waiting

            def search(item, proj=proj, total=total, blocked=blocked):
                self.search_one(slug, proj, item[0], item[1], minutes, variants, log, stats, lock, total, blocked)
                previous = self.net.store.one('SELECT state FROM deep_permutation_passes WHERE function_id=?',item[0]['id'])
                if previous and previous['state'] == 'rotated':
                    with lock:
                        visited_turns.add(item[0]['id'])
                        stats['rotated'] += 1
            if count == 1:
                for item in batch:
                    search(item)
            elif batch:
                pool = ThreadPoolExecutor(count, thread_name_prefix='deep-pass', initializer=workers.lower_priority)
                try:
                    list(pool.map(search, batch))
                finally:  # (what has not started is dropped: on a stop, or when one search raised)
                    pool.shutdown(wait=True, cancel_futures=True)
            if not pending and not waiting:
                self.save(slug, self.config(slug) | {'enabled': False})
                log('Deep pass finished. Completed bounded passes are saved; this is not an exhaustive search of all C.')
                if stats['rotated']:
                    log(f"{stats['rotated']} functions rotated early and remain eligible for the next run.")
                return {'functions_tested': stats['visited'], 'tested': stats['tested'], 'matched': stats['matched'], 'rotated':stats['rotated']}
            if batch:
                continue  # Refill immediately; only genuinely blocked queues need a timed wait.
            stats['activity'].publish({'phase': 'waiting for host claims or scored C', 'functions_tested': stats['visited'],
                          'matches': stats['matched'], 'waiting': waiting, 'total_unmatched': len(rows)})
            # Cancellation remains responsive while another worker owns the remaining functions.
            for _ in range(POLL_SECONDS):
                log.check_cancel()
                time.sleep(1)

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
                          'total_unmatched': total, 'waiting': blocked} | value)

        def function_log(line):
            activity.update(fn["name"], line=line)
            log(f'{fn["name"]}: {line}')
        function_log.check_cancel = log.check_cancel
        function_log.progress = progress
        function_log.adaptive_budget = True
        try:
            result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log)
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
            busy = self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time())
            claim_wait = bool((busy and slug in self.net.links) or 'claim' in str(error).lower() or str(error).startswith('This function is working'))
            self.record(fn, scope, 'pending' if claim_wait else 'error', minutes, variants, error=str(error))
            activity.update(fn['name'], {'phase': 'waiting' if claim_wait else 'error', 'error': str(error)}, line=str(error), finished=True)
            log(f'{fn["name"]}: {error}')
        except BaseException:
            activity.update(fn['name'], {'phase': 'interrupted'}, finished=True)
            self.record(fn, scope, 'pending', minutes, variants)
            raise
        activity.publish({'phase': 'moving to next function', 'tested': stats['tested'], 'functions_tested': stats['visited'],
                  'matches': stats['matched']})
