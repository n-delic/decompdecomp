"""Server-owned, input-bound exact verdicts and atomic bulk compiler-profile publication."""
import copy
import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, wait

from .. import match_bundle, scoring, toolchains
from ..network import NetworkError
from ..project import compiler_for, function_compiler_key
from .compiler_cleanup import replace_range
from .purpose_work import Executor

_LOCK=threading.RLock()
MAX_RECEIPTS=8
TTL=600


def environment(net,proj,selected):
    cfg=json.loads(proj['config'])
    for key in ('compiler_ranges','function_compilers','boundaries','suggestions','knowledge_auto','range_checks','compiler_survey'):
        cfg.pop(key,None)
    cfg['compiler']={**cfg['compiler'],**selected,'timeout':20}
    # Hash common symbol/config/compiler input once, rather than reformatting the
    # full project symbol inventory for each candidate fingerprint.
    return hashlib.sha256(repr((cfg,proj['root'],proj['arch'],proj['big_endian'],
            [tuple(r) for r in net.project_store(proj['id']).all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])],
            match_bundle.compiler_identity(selected))).encode()).hexdigest()


def fingerprint(net,proj,fn,source,env):
    context=net._context(proj['id'],fn['unit'])
    if context is None:raise NetworkError('No compiler header context is available. Reopen the project.')
    fields={k:fn[k] for k in ('id','name','unit','vram','rom_offset','size','n_insns','target','relocs','asm','meta','status','best_source','seed','gone_mark','aside_mark')}
    return hashlib.sha256(repr((env,fields,source,context['version'],context['header'])).encode()).hexdigest()


def remember(net,slug,selected,checks):
    token=secrets.token_urlsafe(24)
    with _LOCK:
        cache=getattr(net,'_compiler_match_receipts',None)
        if cache is None:cache=net._compiler_match_receipts=OrderedDict()
        cache[token]=(time.monotonic(),slug,copy.deepcopy(selected),dict(list(checks.items())[:5000]))
        while len(cache)>MAX_RECEIPTS:cache.popitem(last=False)
    return token


def remembered(net,slug,selected,token):
    with _LOCK:
        item=getattr(net,'_compiler_match_receipts',{}).get(token) if isinstance(token,str) else None
        if not item or time.monotonic()-item[0]>TTL or item[1]!=slug or item[2]!=selected:return {}
        # External includes may change without a database revision; always compile those again.
        if any(f.startswith(('-I','-include','-imacros','@')) for f in selected['flags']):return {}
        return dict(item[3])


def parallel(items,check,cancel,*,thread_name='profile-apply'):
    """Only eight candidates in flight; cancellation never queues the whole project."""
    iterator=iter(items)
    with Executor(purpose='Compiler match verification', max_workers=8,thread_name_prefix=thread_name) as pool:
        pending=set()
        try:
            while True:
                cancel()
                while len(pending)<8:
                    item=next(iterator,None)
                    if item is None:break
                    pending.add(pool.submit(check,item))
                if not pending:break
                done,pending=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                for future in done:
                    cancel()
                    yield future.result()
        finally:
            for future in pending:future.cancel()


def eligible(net,proj,fn,identities):
    if fn['status']!='open':raise NetworkError('No longer unmatched; skipped.')
    if fn['vram'] is None:raise NetworkError('Review this function address before applying a profile.')
    siblings=[row for row in identities if row['vram']==fn['vram']]
    if len(siblings)!=1 and (fn['rom_offset'] is None or any(r['id']!=fn['id'] and r['rom_offset']==fn['rom_offset'] for r in siblings)):
        raise NetworkError('Shares an overlay address without a unique ROM location; identify its original overlay before applying.')
    if not fn['size'] or fn['size']<=0:raise NetworkError('Review this function boundary before applying a profile.')


def apply(net,slug,selected,names,log,receipt=None):
    from .boundary_repairs import blocked
    state={'total':len(names),'checked':0,'passed':0,'failed':0,'matches':[],'failures':[],'applied':True,'reused':0,'verified':0}
    report=getattr(log,'progress',lambda value:None);cancel=getattr(log,'check_cancel',lambda:None)
    def publish(phase):report({**state,'matches':list(state['matches']),'failures':list(state['failures']),'phase':phase})
    def failed(name,error):
        reason=str(error)
        state['failed']+=1
        state['failures'].append({'function':name,'reason':reason,'blockers':[],
            'category':'busy' if 'agent is working' in reason else 'overlay' if 'overlay address' in reason else 'changed' if 'changed' in reason or 'no longer' in reason.lower() else 'verification'})
    publish('Preparing selected matches')
    with net.progress.judging(slug):
        proj=net._project(slug)
        identities=net.project_store(proj['id']).all('SELECT id,vram,rom_offset,size,name FROM functions WHERE project_id=?',proj['id'])
        env=environment(net,proj,selected);cache=remembered(net,slug,selected,receipt)
        boundaries=blocked(net,proj)
        custom=bool(set(json.loads(proj['config'])['compiler'])-{'toolchain','flags','timeout'})
        def check(name):
            cancel()
            try:
                fn=net._function(name,slug)
                eligible(net,proj,fn,identities)
                if fn['id'] in boundaries:raise NetworkError('Review this function boundary before applying a profile.')
                source=net._comparison_source(fn)
                if not source:raise NetworkError('No saved C is available.')
                signature=fingerprint(net,proj,fn,source,env)
                reuse=not custom and cache.get(fn['id'])==signature
                if custom:
                    # The diagnostic judge uses the pinned tool's normal command. Custom
                    # commands/includes must instead verify the actual proposed invocation.
                    from .prepared_evidence import scoped_config
                    private={**dict(proj),'config':json.dumps(scoped_config(proj,fn,selected))}
                    result=net._compile(private,net._context(proj['id'],fn['unit'])['header'],source,fn)
                    candidate,verdict,_=net._evaluate(fn,private,result.obj) if result.ok else (None,False,[])
                    exact=candidate is not None and (verdict if verdict is not None else scoring.diff(net._target_obj(fn,private),candidate).bytes_match)
                else:exact=reuse or net._judge_with(proj,fn,selected['toolchain'],selected['flags'],source)[0]
                if not exact:
                    raise NetworkError('Saved C no longer matches this profile; rerun the test.')
                return name,(fn,source,signature,reuse),None
            except (NetworkError,toolchains.ToolchainError,OSError) as error:return name,None,error
        def check_current(name):
            # The coordinator synchronized once. Workers read current SQLite rows; they
            # must not repeat filesystem progress synchronization for every candidate.
            with net.progress.view():return check(name)
        verified=[]
        for name,result,error in parallel(names,check_current,cancel):
            state['checked']+=1
            if error:failed(name,error)
            else:
                verified.append(result);state['verified']+=1
            publish('Verifying selected matches')
        cancel()
        agent,_=net.local_agent('autopilot')
        # Match the usual record lock order: project lock before SQLite writer. All candidates
        # are revalidated before publishing settings; exact publication releases displaced claims through the normal recorder.
        with net.progress._project_lock(slug),net.progress.view(),net.project_store(proj['id']).tx() as db:
            net.progress.local.judging=(slug,net.progress.revisions.get(slug,0))
            current=net._project(slug);current_env=environment(net,current,selected)
            latest=net.project_store(proj['id']).all('SELECT id,vram,rom_offset,size,name FROM functions WHERE project_id=?',proj['id'])
            current_boundaries=blocked(net,current)
            candidates=[]
            for fn,source,signature,reuse in verified:
                cancel()
                try:
                    fresh=net._function(fn['name'],slug)
                    eligible(net,current,fresh,latest)
                    if fresh['id'] in current_boundaries:raise NetworkError('Review this function boundary before applying a profile.')
                    if fingerprint(net,current,fresh,net._comparison_source(fresh),current_env)!=signature:
                        raise NetworkError('This function or its build inputs changed during verification; retry it.')
                    candidates.append((fresh,source,reuse))
                except NetworkError as error:
                    failed(fn['name'],error);state['verified']-=1
            cfg=json.loads(current['config']);before=copy.deepcopy(cfg)
            starts=sorted({r['vram'] for r in latest if r['vram'] is not None})
            for fn,_,_ in candidates:
                if fn['rom_offset'] is not None:
                    cfg.setdefault('function_compilers',{})[function_compiler_key(fn)]={'vram':fn['vram'],'function':fn['name'],**selected}
                else:
                    end=min([fn['vram']+fn['size']]+[v for v in starts if v>fn['vram']])
                    replace_range(cfg,fn['vram'],end,(selected['toolchain'],tuple(selected['flags'])))
            selected_ids={fn['id'] for fn,_,_ in candidates}
            # No untested neighbour or existing match may adopt the new profile.
            for row in latest:
                if row['id'] not in selected_ids and compiler_for(before,row['vram'],row)!=compiler_for(cfg,row['vram'],row):
                    raise NetworkError('The batch would change an untested neighbour. Review its compiler range.')
            for fn,_,_ in candidates:
                if any(compiler_for(cfg,fn['vram'],fn).get(k)!=selected[k] for k in ('toolchain','flags')):
                    raise NetworkError('A function-specific override conflicts with the selected profile.')
            if candidates:
                db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
                net._event(db,proj['id'],agent.id,'compiler',f'Applied verified compiler profile to {len(candidates)} functions')
            publish('Saving verified matches')
            net.project_store(proj['id']).after_commit(lambda:publish('Matches committed · updating match indexes'))
            for fn,source,reuse in candidates:
                cancel()
                context=net._context(proj['id'],fn['unit'])
                net._record(fn,agent,0,source,context['version'],matched=True,via='compiler-profile',effective_settings={'compiler':compiler_for(cfg,fn['vram'],fn)})
                state['passed']+=1;state['reused']+=int(reuse);state['matches'].append(fn['name'])
        publish('Verified matches saved')
    log(f"Saved {state['passed']} matches; reused {state['reused']} unchanged exact checks; {state['failed']} skipped.")
    return state
