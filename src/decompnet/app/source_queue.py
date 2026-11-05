"""Durable, explicit CPU source recovery. Restarted work is rejudged, never trusted from a checkpoint."""
import json
import threading
import time

from ..network import NetworkError
from . import prepared_evidence
from . import source_preparation as prep
from .permute import State

PREFIX = 'source-recovery-queue:'


class Queue:
    def __init__(self, net, jobs):
        self.net, self.jobs = net, jobs
        self.lock = threading.RLock()
        self.active = {}

    def _load(self, project_id):
        row = self.net.store.one('SELECT value FROM settings WHERE key=?', PREFIX+str(project_id))
        return json.loads(row['value']) if row else {}

    def _save(self, project_id, state):
        state['updated_at'] = time.time()
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (PREFIX+str(project_id), json.dumps(state)))

    def status(self, slug):
        proj = self.net.project_row(slug)
        with self.lock:
            state = self._load(proj['id'])
            return {**state, 'job':self.active.get(slug), 'coverage_note':'Function checks do not verify a complete cartridge. Run repository sync for the full rebuild gate.'}

    def bundles(self, proj, name):
        rows = self.net.store.all('SELECT r.signature,r.feedback FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE f.project_id=? AND f.name=? AND r.signature LIKE ? ORDER BY r.rowid DESC', proj['id'], name, 'local-source-%')
        seen = set();result = []
        for row in rows:
            item = json.loads(row['feedback'] or '{}')
            if not item.get('exact') or not item.get('inputs') or not all(k in item for k in ('source','header','toolchain','flags')):continue
            pair = (item['source'], item['header'], item['toolchain'], tuple(item['flags']))
            if pair in seen:continue
            seen.add(pair);result.append(row['signature'])
            if len(result) >= 4:break
        return result

    def start(self, slug, include_assembly=False, *, foreground=True):
        if type(include_assembly) is not bool:raise NetworkError('Choose assembly reconsideration with its checkbox.')
        proj = self.net.project_row(slug)
        if not self.net.is_rom(proj) or self.net.fork_slug(slug) or slug in self.net.links:
            raise NetworkError('This queue requires a local ROM project. Imported builds use their own source/header and compiler workflow.')
        with self.lock:
            if slug in self.active:return self.status(slug)
            state = self._load(proj['id'])
            if state.get('state') not in ('running','paused','interrupted'):
                cfg = prep.settings(self.net, proj)
                mapped = {m[0] for m in (prep.mappings(cfg.get('mappings',''))+prep.mappings(cfg.get('assembly_mappings','')))} if cfg.get('enabled') else set()
                names = []
                for fn in self.net.store.all("SELECT name,status FROM functions WHERE project_id=? AND status IN ('open','asm') ORDER BY vram", proj['id']):
                    if fn['status']=='asm' and not include_assembly:continue
                    if fn['name'] in mapped or self.bundles(proj, fn['name']):names.append(fn['name'])
                state = {'state':'running','include_assembly':include_assembly,'items':[{'function':n,'state':'pending'} for n in names]}
            else:
                # Resume exactly the original approval, including its assembly choice.
                for item in state['items']:
                    if item['state']=='running':item['state']='pending'
                state['state']='running'
            self._save(proj['id'], state)
            agent, _ = self.net.local_agent('source-queue-'+str(proj['id']))
            state['agent_id'] = agent.id
            self._save(proj['id'], state)
            # Reservation precedes job start; the worker cannot outrun publication of its handle.
            self.active[slug] = None
            try:
                job = self.jobs.start('source-recovery-queue', lambda log:self._run(slug, agent, log),foreground=foreground)
            except BaseException:
                self.active.pop(slug,None);state['state']='interrupted';self._save(proj['id'],state)
                raise
            self.active[slug] = job
            return self.status(slug)

    def stop(self, slug):
        proj = self.net.project_row(slug)
        with self.lock:
            state = self._load(proj['id'])
            if state.get('state')=='running':
                state['state']='paused';self._save(proj['id'], state)
            return self.status(slug)

    def resume(self):
        """Startup only: no old worker survives the app process. Release only this queue's own claim."""
        rows = self.net.store.all('SELECT key,value FROM settings WHERE key LIKE ?', PREFIX+'%')
        for row in rows:
            state = json.loads(row['value'])
            if state.get('state') not in ('running','paused'):continue
            project_id = int(row['key'][len(PREFIX):])
            proj = self.net.store.one('SELECT * FROM projects WHERE id=?', project_id)
            if proj is None:continue
            with self.net.store.tx() as db:
                # The dedicated identity is never used by paid helpers or ordinary autopilot.
                agent = db.execute('SELECT id FROM agents WHERE id=? AND name=?', (state.get('agent_id'), 'source-queue-'+str(project_id))).fetchone()
                if agent:
                    for item in state.get('items', []):
                        if item['state']=='running':
                            db.execute('DELETE FROM claims WHERE agent_id=? AND function_id IN (SELECT id FROM functions WHERE project_id=? AND name=?)', (agent['id'], project_id, item['function']))
                            item['state']='pending'
            self._save(project_id, state)
            if state['state']=='running':self.start(proj['slug'],foreground=False)

    def _run(self, slug, agent, log):
        proj = self.net.project_row(slug)
        try:
            with self.lock:items = self._load(proj['id'])['items']
            for index, item in enumerate(items):
                if item['state'] not in ('pending','running'):continue
                with self.lock:
                    state = self._load(proj['id'])
                    if state['state']!='running':break
                    item['state']='running';state['items']=items;self._save(proj['id'], state)
                log.progress({'phase':'Recovering local source','finished':index,'total':len(items)})
                name = item['function'];log(name+': preparing and freshly judging source bundles…')
                try:
                    result = self._one(slug, name, agent, state['include_assembly'], log)
                    item.update(state='pending' if result.get('paused') else 'deferred' if result.get('deferred') else 'matched' if result['matched'] else 'verified_assembly' if result.get('assembly_verified') else 'checked', **result)
                except (NetworkError, ValueError, OSError) as error:
                    item.update(state='error', error=str(error));log(name+': '+str(error))
                with self.lock:
                    state = self._load(proj['id']);state['items']=items;self._save(proj['id'], state)
            with self.lock:
                state = self._load(proj['id'])
                if state['state']=='running':state['state']='done'
                self._save(proj['id'], state)
            log.progress({'phase':'Source queue stopped' if state['state']=='paused' else 'Source queue complete','finished':sum(i['state'] not in ('pending','running') for i in items),'total':len(items)})
            return state
        except BaseException:
            with self.lock:
                state = self._load(proj['id'])
                if state.get('state')=='running':state['state']='interrupted'
                self._save(proj['id'], state)
            raise
        finally:
            with self.lock:self.active.pop(slug, None)

    def _one(self, slug, name, agent, assembly, log):
        before = self.net._function(name, slug)
        if before['status']=='matched':return {'matched':True,'already_matched':True}
        if before['status']=='asm' and not assembly:return {'matched':False,'skipped':'Assembly reconsideration was not approved.'}
        claim=self.net.store.one('SELECT agent_id,expires_at FROM claims WHERE function_id=?',before['id'])
        if claim and claim['expires_at']>time.time() and claim['agent_id']!=agent.id:
            return {'matched':False,'deferred':True,'error':'Another worker holds this function. Start a new queue after it is released.'}
        for round_number in range(3):
            proj = self.net.project_row(slug)
            for key in self.bundles(proj, name):
                try:
                    prepared_evidence.apply(self.net, proj, key, log, refresh=True, include_assembly=assembly, agent=agent)
                    return {'matched':True,'starting_score':before['best_score'],'new_score':0}
                except NetworkError as error:log(name+': bundle retained, recheck did not promote: '+str(error))
            cfg = prep.settings(self.net, proj)
            if not cfg.get('enabled') or name not in {m[0] for m in (prep.mappings(cfg.get('mappings',''))+prep.mappings(cfg.get('assembly_mappings','')))}:break
            st = State()
            # Pause requests do not interrupt atomic publication; stop at the next bounded round.
            with self.lock:
                if self._load(proj['id'])['state']!='running':return {'matched':False,'paused':True}
            report = prep.run(self.net.permuter, proj, st, log, budget=32, seconds=180, only={name}, include_assembly=assembly, agent=agent)
            current = self.net._function(name, slug)
            if current['status']=='matched':return {'matched':True,'starting_score':before['best_score'],'new_score':0}
            if report.get('assembly_verified'):return {'matched':False,'assembly_verified':True}
            if not report['tested']:
                if report['errors']:raise NetworkError('; '.join(report['errors'])[:2000])
                break
        # Final round may have produced a new exact profile bundle.
        for key in self.bundles(self.net.project_row(slug), name):
            try:
                prepared_evidence.apply(self.net, self.net.project_row(slug), key, log, refresh=True, include_assembly=assembly, agent=agent)
                return {'matched':True,'starting_score':before['best_score'],'new_score':0}
            except NetworkError as error:log(name+': '+str(error))
        current = self.net._function(name, slug)
        return {'matched':False,'starting_score':before['best_score'],'new_score':current['best_score']}
