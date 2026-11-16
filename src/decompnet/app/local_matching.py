"""Explicit project-wide CPU searches on upstream's store, worker pool and unchanged judges.

Reading status never imports a saved project or starts maintenance. Completed ROM turns are
reused only while their compiler, header, target and source inputs still agree. Imported builds
are rejudged on each explicit pass because their external build dependencies cannot be inferred
from the database. Cross-game searches reuse the existing twins engine and keep only result
metadata here; source and game bytes stay in their projects.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from ..network import IN_BUILD, NetworkError
from ..project import compiler_for
from . import deep_permuter
from .twins import Job

DEFAULTS = {'minutes':5, 'max_variants':10000, 'threads':0, 'repeat':False, 'loop':False}
POLL_SECONDS = 0.1
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS local_search_turns(
       function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE,
       signature TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '')''',
    '''CREATE TABLE IF NOT EXISTS local_search_improvements(
       function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE,
       starting_score INTEGER NOT NULL, new_score INTEGER NOT NULL, updated_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS local_cross_results(
       id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
       target TEXT NOT NULL, source TEXT NOT NULL, source_slug TEXT NOT NULL, game TEXT NOT NULL,
       outcome TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS local_cross_project ON local_cross_results(project_id,id)',
)


def options(values):
    """Validate only the controls upstream's shared workers can actually honor."""
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in values:
            result[key] = values[key]
    for key, low, high in [('minutes',1,120), ('max_variants',1,1000000),
                           ('threads',0,deep_permuter.max_searches())]:
        value = result[key]
        if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
            raise NetworkError(f'Choose {key.replace("_", " ")} from {low} to {high}.')
    for key in ('repeat','loop'):
        if not isinstance(result[key],bool):
            raise NetworkError(f'{key.capitalize()} must be on or off.')
    return result


class Matching:
    """One cancellable job per project/tool, with durable turns and independently resettable totals."""

    def __init__(self, net, jobs):
        self.net, self.jobs = net, jobs
        self.lock = threading.RLock()
        self.active = {}
        self.progress = {}
        with net.store.tx() as db:
            for sql in SCHEMA:
                db.execute(sql)

    def project(self, slug):
        row = self.net.store.one('SELECT * FROM projects WHERE slug=?',slug)
        if row is None:
            raise NetworkError('Choose a project that is still in the project list.')
        return row

    def config(self, slug):
        self.project(slug)
        row = self.net.store.one('SELECT value FROM settings WHERE key=?','local-search:'+slug)
        return DEFAULTS | (json.loads(row['value']) if row else {})

    def _save(self, slug, values):
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('local-search:'+slug,json.dumps(values)))

    def _rows(self, project):
        return self.net.store.all(f"SELECT f.* FROM functions f WHERE f.project_id=? AND f.{IN_BUILD} "
                                  "AND f.status='open' AND f.best_source IS NOT NULL AND f.best_source!='' "
                                  'AND f.best_score IS NOT NULL ORDER BY f.id',project['id'])

    def _signature(self, project, fn, config, shared=None):
        cfg = self.net.cfg(project)
        profile = compiler_for(cfg,fn['vram']) if cfg.get('compiler') else {}
        key = (fn['unit'],json.dumps(profile,sort_keys=True))
        shared = {} if shared is None else shared
        if key not in shared:
            ctx = self.net._context(project['id'],fn['unit'])
            common = (key[1],cfg.get('gp'),cfg.get('big_endian'),cfg.get('platform'),
                      ctx['header'] if ctx else None,ctx['version'] if ctx else None)
            shared[key] = hashlib.sha256(repr(common).encode()).hexdigest()
        inputs = (shared[key],
                  tuple(fn[k] for k in ('id','best_source','target','relocs','unit','vram')),
                  config['minutes'],config['max_variants'])
        return hashlib.sha256(repr(inputs).encode()).hexdigest()

    def _current_job(self, slug, kind):
        job = self.jobs.jobs.get(self.active.get((slug,kind)))
        return job if job and job['state']=='running' else None

    def start(self, slug, kind, values=None):
        """Return an existing job on duplicate starts; resource contention waits inside the new job."""
        project = self.project(slug)
        if slug in self.net.links:
            raise NetworkError('Run local matching on the room host; the host must verify its results.')
        if kind not in ('deep','cross'):
            raise NetworkError('Choose Deep permuter or Matches from other games.')
        with self.lock:
            if job := self._current_job(slug,kind):
                return {'job':job['id']}
            config = options(self.config(slug) | (values or {})) if kind=='deep' else None
            if config is not None:
                self._save(slug,config)
            # A worker can finish immediately. Publish its ID while holding this lock; status waits its turn.
            work = (lambda log:self._deep(slug,project,config,log)) if kind=='deep' else (lambda log:self._cross(slug,project,log))
            jid = self.jobs.start('local-matching-'+kind,work,project=slug,
                                  cancel_message='Search stopped. Verified results and completed turns are saved.')
            self.active[(slug,kind)] = jid
            return {'job':jid}

    def stop(self, slug, kind):
        self.project(slug)
        with self.lock:
            job = self._current_job(slug,kind)
            if job:
                self.jobs.cancel(job['id'])
            return {'job':job['id'] if job else None}

    def _report(self, slug, log, **values):
        with self.lock:
            snapshot = self.progress.setdefault(slug,{})
            snapshot.update(values)
            log.progress(dict(snapshot))

    def _search(self, slug, project, fn, config, log):
        before = fn['best_score']
        signature = self._signature(project,fn,config)
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO local_search_turns VALUES(?,?,?,?)',
                       (fn['id'],signature,'running',''))
        parent = self

        class FunctionLog:
            def __call__(self, line):
                log(f"{fn['name']}: {line}")

            def check_cancel(self):
                log.check_cancel()

            def progress(self, value):
                with parent.lock:
                    workers = parent.progress.setdefault(slug,{}).setdefault('workers',{})
                    workers[fn['name']] = {'function':fn['name'],**value}
                    log.progress(dict(parent.progress[slug]))

        state, error = 'done',''
        try:
            deep_permuter.run(self.net,slug,fn['name'],config['minutes'],config['max_variants'],FunctionLog())
        except NetworkError as exc:
            # A claim acquired after queue admission is temporary, never a failed completed turn.
            if self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?',fn['id'],time.time()):
                state = 'pending'
            else:
                state,error = 'error',str(exc)
        except BaseException:
            state = 'pending'
            raise
        finally:
            current = self.net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
            with self.net.store.tx() as db:
                if current is not None:
                    # This search publishes its own winner. Resume against that winner, not its starting C.
                    latest_project = self.project(slug)
                    initial_source = dict(current) | {'best_source':fn['best_source']}
                    if self._signature(latest_project,initial_source,config) != signature or (error and 'changed' in error.lower()):
                        state = 'pending'
                    else:
                        signature = self._signature(latest_project,current,config)
                    db.execute('UPDATE local_search_turns SET signature=?,state=?,error=? WHERE function_id=?',
                               (signature,state,error,fn['id']))
                    if current['best_score'] is not None and current['best_score'] < before:
                        db.execute('''INSERT INTO local_search_improvements VALUES(?,?,?,?)
                            ON CONFLICT(function_id) DO UPDATE SET new_score=excluded.new_score,updated_at=excluded.updated_at''',
                                   (fn['id'],before,current['best_score'],time.time()))
            with self.lock:
                self.progress.get(slug,{}).get('workers',{}).pop(fn['name'],None)
        return state

    def _deep(self, slug, project, config, log):
        # Coordinators never run inside the shared compiler pool: nested submissions there can deadlock.
        limit = config['threads'] or deep_permuter.max_searches()
        cycle, completed = 1,set()
        self._report(slug,log,phase='Preparing search',workers={},functions_tested=0,cycle=cycle)
        with ThreadPoolExecutor(max_workers=limit,thread_name_prefix='local-search') as pool:
            pending = {}
            try:
                while True:
                    log.check_cancel()
                    project = self.project(slug)
                    fork = self.net.cfg(project).get('kind')=='fork'
                    waiting = False
                    for fn in self._rows(project):
                        if fn['id'] in completed or any(row['id']==fn['id'] for row in pending.values()):
                            continue
                        turn = self.net.store.one('SELECT * FROM local_search_turns WHERE function_id=?',fn['id'])
                        if (not config['repeat'] and not fork and turn and turn['state'] in ('done','error')
                                and turn['signature']==self._signature(project,fn,config)):
                            continue
                        if self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?',fn['id'],time.time()):
                            waiting = True
                            continue
                        if len(pending)>=limit:
                            break
                        pending[pool.submit(self._search,slug,project,fn,config,log)] = fn
                    if not pending:
                        if waiting:
                            self._report(slug,log,phase='Waiting for available functions')
                            time.sleep(POLL_SECONDS)
                            continue
                        if not config['loop']:
                            break
                        cycle += 1
                        completed.clear()
                        config = config | {'repeat':True}
                        self._report(slug,log,phase='Waiting for the next pass',cycle=cycle)
                        time.sleep(POLL_SECONDS)
                        continue
                    done,_ = wait(pending,timeout=POLL_SECONDS,return_when=FIRST_COMPLETED)
                    for future in done:
                        fn = pending.pop(future)
                        state = future.result()
                        if state!='pending':
                            completed.add(fn['id'])
                    self._report(slug,log,phase='Searching saved C',functions_tested=len(completed))
            finally:
                for future in pending:
                    future.cancel()
        self._report(slug,log,phase='Search complete',workers={})
        return {'completed':len(completed)}

    def _cross(self, slug, project, log):
        with self.lock:
            jid = self.active[(slug,'cross')]
        check = Job()
        while True:
            log.check_cancel()
            with self.net.twins._lock:
                if not self.net.twins._full_running(slug) and (self.net.autopilot_status(slug) or {}).get('state')!='running':
                    self.net.twins.jobs[slug] = check
                    break
            log.progress({'phase':'Waiting for the current free matching pass'})
            time.sleep(POLL_SECONDS)

        def result(row):
            log.check_cancel()
            with self.net.store.tx() as db:
                db.execute('''INSERT INTO local_cross_results
                    (project_id,target,source,source_slug,game,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                           (project['id'],row['target'],row['source'],row['source_slug'],row['game'],
                            row['outcome'],row['reason'],time.time()))
            log.progress({'phase':'Checking other games','attempts':check.tried,'confirmed':check.matched})

        check.report = result
        # The engine sees Stop even between donor attempts. Its fingerprint readers are unchanged.
        check.stop = self.jobs.cancel_events[jid]
        try:
            matched = self.net.twins.across_games(slug,check)
            log.check_cancel()
            return {'matched':matched,'tried':check.tried}
        finally:
            check.state = 'stopped' if check.stop.is_set() else 'done'

    def reset(self, slug):
        project = self.project(slug)
        with self.lock:
            if self._current_job(slug,'deep'):
                raise NetworkError('Stop this project’s Deep permuter search before resetting its totals.')
            with self.net.store.tx() as db:
                db.execute('DELETE FROM local_search_improvements WHERE function_id IN '
                           '(SELECT id FROM functions WHERE project_id=?)',(project['id'],))
        return self.status(slug)

    def control(self, slug):
        """Find running jobs before reading function statistics; Stop must not wait for a large game's totals."""
        config = self.config(slug)
        with self.lock:
            deep_job = self._current_job(slug,'deep')
            cross_job = self._current_job(slug,'cross')
            last = self.jobs.jobs.get(self.active.get((slug,'deep')), {})
            cross_last = self.jobs.jobs.get(self.active.get((slug,'cross')), {})
            progress = dict(self.progress.get(slug,{}))
            progress['workers'] = list(progress.get('workers',{}).values()) if deep_job else []
            return {'deep':config | {'job':deep_job['id'] if deep_job else None,
                'state':'running' if deep_job else 'idle','progress':progress,'statistics_pending':True,
                'threads_default':deep_permuter.max_searches(),
                'error':last.get('error') if last.get('state')=='error' else None},
                'cross':{'job':cross_job['id'] if cross_job else None,
                         'error':cross_last.get('error') if cross_last.get('state')=='error' else None}}

    def status(self, slug):
        """Saved metadata only; never invoke project import, build, or source synchronization."""
        project = self.project(slug)
        out = self.control(slug)
        config = self.config(slug)
        improvements = [dict(row) for row in self.net.store.all(
            'SELECT f.name AS function,r.starting_score,r.new_score FROM local_search_improvements r\n               JOIN functions f ON f.id=r.function_id WHERE f.project_id=? ORDER BY r.updated_at DESC',project['id'])]
        turns = self.net.store.all('SELECT f.name AS function,t.state,t.error FROM local_search_turns t '
                                  'JOIN functions f ON f.id=t.function_id WHERE f.project_id=?',project['id'])
        eligible = self._rows(project)
        fork = self.net.cfg(project).get('kind')=='fork'
        shared = {}
        left = sum(fork or not (turn := self.net.store.one('SELECT * FROM local_search_turns WHERE function_id=?',fn['id']))
                   or turn['state'] not in ('done','error') or turn['signature']!=self._signature(project,fn,config,shared)
                   for fn in eligible)
        out['deep'].update(statistics_pending=False,improvements=improvements,improved=len(improvements),
                           queue_done=sum(row['state']=='done' for row in turns),queue_left=left,
                           has_activity=bool(turns or improvements),
                           attention=[{'function':row['function'],'error':row['error']} for row in turns if row['state']=='error'])
        rows = self.net.store.all('SELECT outcome,COUNT(*) AS n FROM local_cross_results WHERE project_id=? GROUP BY outcome',project['id'])
        results = [dict(row) for row in self.net.store.all('SELECT * FROM local_cross_results WHERE project_id=? ORDER BY id DESC',project['id'])]
        out['cross'].update(counts={row['outcome']:row['n'] for row in rows},recent=results,
                            matches=[row for row in results if row['outcome']=='match'])
        return out
