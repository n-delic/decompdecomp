"""Rejudge historical exact C across project renames/units; never infer progress from an old score."""
import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from .. import candidate_scope, compiler, match_bundle
from ..network import NetworkError
from . import function_names, source_preparation

MAX_BANK = 4096
SCHEMA = "CREATE TABLE IF NOT EXISTS reconcile_visits(function_id INTEGER PRIMARY KEY,scope TEXT,cursor INTEGER,visited REAL)"


def bank(net, proj):
    """Recorded exact source/header inputs in this project, including now-open donors."""
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):return []
    rows=net.store.all('SELECT v.details,v.source,v.header,f.name,f.meta,f.vram FROM verified_matches v '
                       'JOIN functions f ON f.id=v.function_id WHERE f.project_id=? ORDER BY v.attempt_id DESC LIMIT ?',proj['id'],MAX_BANK)
    out={}
    for row in rows:
        try:proof=json.loads(row['details'])
        except (ValueError,TypeError):continue
        if (proof.get('verdict')!='exact' or proof.get('source_sha256')!=match_bundle.digest(row['source'])
                or len(row['source'])>128000):continue
        address = proof.get('vram')
        if isinstance(address, int):out.setdefault(address, []).append((row,proof))
    return out


def candidates(net, proj, fn, records):
    current=net._context(proj['id'],fn['unit'])['header']
    out=[]
    def add(source, header, origin):
        if source and len(source)<=128000 and len(header)<=1024*1024:
            item=(source,header,origin)
            if not any(x[:2]==item[:2] for x in out):out.append(item)
    if fn['matched_source']:
        from .repository_candidates import contexts
        for header in contexts(net,proj,fn):
            add(function_names.canonical_source(fn,fn['matched_source']),header,'saved exact source')
    from .repository_sync import source_text
    abi=match_bundle.abi(proj,json.loads(proj['config']))
    target = match_bundle.digest(fn['target'] or b'')
    relocations = match_bundle.digest(fn['relocs'] or '')
    # Overlay-heavy games have thousands of records; inspect only this address's
    # donors, preserving their original order and the complete identity checks.
    donors = records.get(fn['vram'], ()) if isinstance(records, dict) else records
    for row,proof in donors:
        old_abi=proof.get('abi') or {}
        if (proof.get('target_sha256')!=target or proof.get('vram')!=fn['vram']
                or proof.get('relocations_sha256')!=relocations
                or any(old_abi.get(k)!=abi.get(k) for k in ('rom_sha1','arch','big_endian'))):continue
        source=function_names.canonical_source(row,row['source'])
        source=source_text(source,row['name'],fn['name'])
        add(source,current,'recorded exact source, current header')
        if proof.get('header_sha256')==match_bundle.digest(row['header']):
            add(source,row['header'],'recorded exact source/header')
        if len(out)>=6:break
    # Legacy same-function attempts have no frozen target hash. They are hypotheses only;
    # the current byte/data judge must independently establish a new exact verdict.
    rows=net.store.all('SELECT a.source,c.header FROM attempts a JOIN context_versions c '
                       'ON c.project_id=? AND c.version=a.context_version '
                       'WHERE a.function_id=? AND a.score=0 AND a.compiled=1 ORDER BY a.id DESC LIMIT 3',proj['id'],fn['id'])
    for row in rows:
        add(function_names.canonical_source(fn,row['source']),row['header'],'legacy exact attempt, freshly rejudged')
    return out[:8]


def validate(net, slug, seconds, attempts):
    proj=net.project_row(slug)
    if not net.is_rom(proj) or net.fork_slug(slug) or slug in net.links:
        raise NetworkError('Saved-match recovery currently needs a local ROM project. Imported builds own their source/header history.')
    if type(seconds)!=int or not 1<=seconds<=600 or type(attempts)!=int or not 1<=attempts<=1024:
        raise ValueError('Choose 1–600 seconds and 1–1024 compiler attempts.')
    return proj


class _Stopped(NetworkError):
    """Cooperative cancellation/deadline while waiting for a writer or compiling a candidate."""


def _release(net, agent, functions, log):
    if not functions:return
    deadline = time.monotonic()+5
    def check():
        if time.monotonic() >= deadline:
            raise _Stopped('The database is still busy; this short recovery claim will expire normally.')
    try:
        with net.store.tx(wait=check) as db:
            db.executemany('DELETE FROM claims WHERE function_id=? AND agent_id=?',
                           [(fn['id'],agent.id) for fn in functions])
    except _Stopped as error:
        log(str(error))


def run(net, slug, seconds, attempts, log):
    """Overlap bounded compiles; synchronize and publish once per small recovery group."""
    from . import workers
    proj=validate(net,slug,seconds,attempts)
    stop=getattr(log,'cancel_event',threading.Event());started=time.monotonic();deadline=started+seconds
    abandoned=threading.Event()
    result=dict(matched=0,attempts=0,skipped=0,resumable=True)
    rows=[];completed=0;tried=set();done=set()
    def checkpoint():
        if stop.is_set() or abandoned.is_set():raise _Stopped('Saved-match recovery stopped; completed matches are retained.')
        if time.monotonic()>=deadline:raise _Stopped('Saved-match recovery reached its time limit; run it again to continue.')
    def publish():
        elapsed=time.monotonic()-started
        result['seconds']=elapsed
        result['matches_per_minute']=result['matched']*60/max(.001,elapsed)
        if hasattr(log,'progress'):log.progress(dict(result,phase='Rechecking saved matches',completed=completed,total=len(rows)))
    def judging():
        return net.progress.judging(slug) if hasattr(net,'progress') else nullcontext()
    def batch():
        method=getattr(getattr(net,'progress',None),'batch',None)
        return method(slug) if method else nullcontext()
    def compile_one(proj, header, source, fn):
        with compiler.cancellable(checkpoint):
            checkpoint()
            return net._compile(proj,header,source,fn)
    def remaining(ordered, fn, proj):
        scope=candidate_scope.key(net,proj,fn)
        for candidate_index,(source,header,origin) in ordered:
            key='reconcile-v1:'+hashlib.sha256(repr((scope,source,header)).encode()).hexdigest()
            if (fn['id'],key) not in tried and not net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key):
                yield candidate_index,source,header,origin,key
    # Use independent low-priority compiler threads: shared background workers can be
    # waiting for our project lock. The compiler's global slots still cap subprocesses.
    width=max(1,min(8,workers.WORKERS,attempts))
    executor=ThreadPoolExecutor(width,thread_name_prefix='saved-recovery',initializer=workers.lower_priority)
    agent=None
    try:
        with judging():
            checkpoint()
            proj=net.project_row(slug);records=bank(net,proj);agent,_=net.local_agent('autopilot')
            with net.store.tx(wait=checkpoint) as db:db.execute(SCHEMA)
            visits={r['function_id']:r for r in net.store.all('SELECT * FROM reconcile_visits')}
            rows=net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY id",proj['id'])
            rows.sort(key=lambda f:visits[f['id']]['visited'] if f['id'] in visits else 0)
        entries=deque(rows)
        while entries and result['attempts']<attempts:
            checkpoint()
            group=[]
            # Functions without history need neither compiler work nor an expensive
            # forced progress-folder synchronization. Rebuild selected pools after pulling.
            while len(group)<min(width,attempts-result['attempts']):
                if not entries:break
                row=entries.popleft()
                checkpoint()
                current_proj=net.project_row(slug)
                pool=candidates(net,current_proj,row,records)
                if pool and next(remaining(enumerate(pool),row,current_proj),None) is not None:group.append(row)
                else:done.add(row['id']);completed=len(done)
                if len(done)%64==0:publish()
            if not group:continue
            jobs=[]
            # This bounded group defers autosaves only until at most eight first
            # candidates finish, rather than holding a project lock for the whole pass.
            with batch(), judging():
                try:
                    for row in group:
                        checkpoint()
                        proj=net.project_row(slug);fn=net.store.one('SELECT * FROM functions WHERE id=?',row['id'])
                        if not fn or fn['status']!='open':
                            done.add(row['id']);completed=len(done);continue
                        pool=candidates(net,proj,fn,records)
                        pool_scope=hashlib.sha256(repr((candidate_scope.key(net,proj,fn),pool)).encode()).hexdigest()
                        prior=visits.get(fn['id']);cursor=prior['cursor'] if prior and prior['scope']==pool_scope else 0
                        ordered=list(enumerate(pool));ordered=ordered[cursor:]+ordered[:cursor]
                        choices=iter(remaining(ordered,fn,proj));first=next(choices,None)
                        if first is None:
                            done.add(fn['id']);completed=len(done);continue
                        with net.store.tx(wait=checkpoint):held=net.permuter._hold(agent,fn,count_turn=False,seconds=60)
                        if not held:
                            result['skipped']+=1;done.add(fn['id']);completed=len(done);continue
                        job=dict(fn=fn,proj=proj,first=first,pool_scope=pool_scope,pool_size=len(pool),future=None)
                        jobs.append(job)  # cleanup owns the claim even if snapshot/submission fails
                        job['expected']=source_preparation.inputs(net,proj,fn)
                        result['attempts']+=1
                        job['future']=executor.submit(compile_one,dict(proj),first[2],first[1],dict(fn))
                    publish()
                    for job in jobs:
                        fn=job['fn'];proj=job['proj'];choice=job['first'];future=job['future']
                        try:
                            checkpoint()
                            candidate_index,source,header,origin,key=choice
                            built=future.result()
                            checkpoint()
                            tried.add((fn['id'],key))
                            with net.store.tx(wait=checkpoint) as db:
                                db.execute('INSERT OR REPLACE INTO reconcile_visits VALUES(?,?,?,?)',
                                           (fn['id'],job['pool_scope'],(candidate_index+1)%max(1,job['pool_size']),time.time()))
                            verdict=False
                            if built.ok:
                                candidate,exact,_=net._evaluate(fn,proj,built.obj)
                                verdict=candidate is not None and exact is True
                            if verdict:
                                checkpoint()
                                net._record(fn,agent,0,source,0,matched=True,via='saved-match-reconciliation',
                                            effective_settings=getattr(built,'effective_settings',None),
                                            prepared_header=header,prepared_inputs=job['expected'],restoring_exact=True,
                                            before_record=checkpoint,write_wait=checkpoint)
                                result['matched']+=1;log('Recovered '+fn['name']+' from '+origin+'.')
                                done.add(fn['id'])
                            else:
                                current=net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
                                if (not current or current['status']!='open' or
                                        source_preparation.inputs(net,net.project_row(slug),current)!=job['expected']):
                                    done.add(fn['id'])
                                else:
                                    if built.ok:
                                        with net.store.tx(wait=checkpoint) as db:
                                            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                                       (fn['id'],key,'miss',origin,json.dumps({'matched':False})))
                                    # Rotate to the next coherent candidate on the next small
                                    # group, releasing the project lock and publishing first.
                                    entries.append(current)
                        except _Stopped:raise
                        except NetworkError as error:
                            result['skipped']+=1;done.add(fn['id']);log(fn['name']+': '+str(error))
                        except sqlite3.OperationalError as error:
                            if getattr(error,'sqlite_errorcode',0) & 255 not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):raise
                            result['skipped']+=1;done.add(fn['id'])
                            log(fn['name']+': database busy; saved evidence is retained for the next recovery pass.')
                        finally:
                            completed=len(done)
                            publish()
                finally:
                    for job in jobs:
                        if job['future'] is not None:job['future'].cancel()
                    _release(net,agent,[job['fn'] for job in jobs],log)
    except _Stopped as error:
        log(str(error))
    finally:
        abandoned.set()
        executor.shutdown(wait=False,cancel_futures=True)
    publish()
    log(f"Saved-match recovery: {result['matched']} matches from {result['attempts']} compiles.")
    return result
