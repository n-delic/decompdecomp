"""Rejudge historical exact C across project renames/units; never infer progress from an old score."""
import hashlib
import json
import threading
import time

from .. import candidate_scope, match_bundle
from ..network import NetworkError
from . import function_names, source_preparation

MAX_BANK = 4096
SCHEMA = "CREATE TABLE IF NOT EXISTS reconcile_visits(function_id INTEGER PRIMARY KEY,scope TEXT,cursor INTEGER,visited REAL)"


def bank(net, proj):
    """Recorded exact source/header inputs in this project, including now-open donors."""
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):return []
    rows=net.store.all('SELECT v.details,v.source,v.header,f.name,f.meta,f.vram FROM verified_matches v '
                       'JOIN functions f ON f.id=v.function_id WHERE f.project_id=? ORDER BY v.attempt_id DESC LIMIT ?',proj['id'],MAX_BANK)
    out=[]
    for row in rows:
        try:proof=json.loads(row['details'])
        except (ValueError,TypeError):continue
        if (proof.get('verdict')!='exact' or proof.get('source_sha256')!=match_bundle.digest(row['source'])
                or len(row['source'])>128000):continue
        out.append((row,proof))
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
    for row,proof in records:
        old_abi=proof.get('abi') or {}
        if (proof.get('target_sha256')!=match_bundle.digest(fn['target'] or b'') or proof.get('vram')!=fn['vram']
                or proof.get('relocations_sha256')!=match_bundle.digest(fn['relocs'] or '')
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


def run(net, slug, seconds, attempts, log):
    proj=validate(net,slug,seconds,attempts)
    stop=getattr(log,'cancel_event',threading.Event());deadline=time.monotonic()+seconds
    result=dict(matched=0,attempts=0,skipped=0,resumable=True)
    records=bank(net,proj);agent,_=net.local_agent('autopilot')
    with net.store.tx() as db:db.execute(SCHEMA)
    visits={r['function_id']:r for r in net.store.all('SELECT * FROM reconcile_visits')}
    rows=net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY id",proj['id'])
    rows.sort(key=lambda f:visits[f['id']]['visited'] if f['id'] in visits else 0)
    def stopped():return stop.is_set() or time.monotonic()>=deadline or result['attempts']>=attempts
    for index,row in enumerate(rows):
        if stopped():break
        if hasattr(log,'progress'):log.progress(dict(result,phase='Rechecking saved matches',completed=index,total=len(rows)))
        pool=candidates(net,proj,row,records)
        pool_scope=hashlib.sha256(repr((candidate_scope.key(net,proj,row),pool)).encode()).hexdigest()
        prior=visits.get(row['id']);cursor=prior['cursor'] if prior and prior['scope']==pool_scope else 0
        ordered=list(enumerate(pool));ordered=ordered[cursor:]+ordered[:cursor]
        for candidate_index,(source,header,origin) in ordered:
            if stopped():break
            proj=net.project_row(slug);fn=net.store.one('SELECT * FROM functions WHERE id=?',row['id'])
            if not fn or fn['status']!='open':break
            scope=candidate_scope.key(net,proj,fn)
            key='reconcile-v1:'+hashlib.sha256(repr((scope,source,header)).encode()).hexdigest()
            if net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key):continue
            if not net.permuter._hold(agent,fn,count_turn=False,seconds=60):result['skipped']+=1;break
            try:
                expected=source_preparation.inputs(net,proj,fn)
                result['attempts']+=1
                built=net._compile(proj,header,source,fn)
                with net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO reconcile_visits VALUES(?,?,?,?)',(fn['id'],pool_scope,(candidate_index+1)%max(1,len(pool)),time.time()))
                verdict=False
                if built.ok:
                    candidate,exact,_=net._evaluate(fn,proj,built.obj)
                    verdict=candidate is not None and exact is True
                if verdict:
                    if stop.is_set() or time.monotonic()>=deadline:break  # no adoption after cancellation/deadline
                    # Record under the same claim and atomically validate the complete frozen state.
                    net._record(fn,agent,0,source,0,matched=True,via='saved-match-reconciliation',
                                effective_settings=getattr(built,'effective_settings',None),
                                prepared_header=header,prepared_inputs=expected,restoring_exact=True)
                    result['matched']+=1;log('Recovered '+fn['name']+' from '+origin+'.');break
                # Never cache tool/resource failures as permanent evidence.
                if built.ok and source_preparation.inputs(net,net.project_row(slug),net.store.one('SELECT * FROM functions WHERE id=?',fn['id']))==expected:
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                   (fn['id'],key,'miss',origin,json.dumps({'matched':False})))
            except NetworkError as error:
                result['skipped']+=1;log(fn['name']+': '+str(error))
            finally:
                with net.store.tx() as db:db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(fn['id'],agent.id))
    result['seconds']=max(0,seconds-(deadline-time.monotonic()))
    result['matches_per_minute']=result['matched']*60/max(.001,result['seconds'])
    if hasattr(log,'progress'):log.progress(dict(result,completed=index+1 if rows else 0,total=len(rows)))
    log(f"Saved-match recovery: {result['matched']} matches from {result['attempts']} compiles.")
    return result
