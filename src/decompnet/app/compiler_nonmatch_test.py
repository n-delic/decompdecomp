"""Start read-only compiler checks without materializing every function's saved text."""
import json

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for
from . import compiler_match_batch as batch
from .compiler_cleanup import profile


def run(net, slug, selected, log=print):
    cancel=getattr(log,'check_cancel',lambda:None)
    report=getattr(log,'progress',lambda value:None)
    cancel()
    report({'phase':'Reading project metadata'})
    if (not isinstance(selected,dict) or not isinstance(selected.get('toolchain'),str)
            or not isinstance(selected.get('flags'),list) or not all(isinstance(f,str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    selected={'toolchain':selected['toolchain'],'flags':list(selected['flags'])}
    # A diagnostic uses current saved database inputs. Disk reconciliation is a
    # separate service; it must not export/rebuild the project before this trial.
    with net.progress.view():
        net._rom_only(slug,'testing a compiler profile as default')
        proj=net._project(slug)
    cfg=json.loads(proj['config'])
    target=profile(selected)
    rows=net.project_store(proj['id']).all('SELECT id,name,vram,rom_offset,status FROM functions WHERE project_id=? ORDER BY id',proj['id'])
    if target==profile(cfg['compiler']) or not any(profile(compiler_for(cfg,f['vram'],f))==target for f in rows):
        raise NetworkError('Choose a non-default compiler profile currently used in this project.')
    cancel()
    if toolchains.installed(target[0]) is None:
        raise NetworkError('Install the selected compiler before testing it as default.')
    candidates={f['id']:f['name'] for f in rows if f['status']=='open'}
    state={'total':len(candidates),'checked':0,'passed':0,'failed':0,'profile':selected,
           'default':cfg['compiler'],'failures':[],'matches':[],'stale':False,'nonmatches':True}
    def publish(phase='Preparing and testing non-matches'):
        report({**state,'matches':list(state['matches']),'failures':list(state['failures']),'phase':phase})
    publish('Preparing compiler context')
    env=batch.environment(net,proj,selected) if candidates else None
    cancel()
    log(f"Testing {len(candidates)} non-matching functions against {target[0]} {' '.join(target[1])}. No settings will change.")
    publish()
    proofs={};tested_names={}
    def check(identity):
        cancel()
        name=candidates[identity]
        try:
            # Only the bounded in-flight workers fetch/decompress full function
            # records. Slow preparation of one candidate cannot hold up its peers.
            fn=net.project_store(proj['id']).one('SELECT * FROM functions WHERE project_id=? AND id=?',proj['id'],identity)
            cancel()
            if fn is None or fn['status']!='open':
                return identity,name,False,'No longer unmatched; skipped.',None
            name=fn['name']
            source=net._source_of(fn)
            if not source:return identity,name,False,'No saved C is available.',None
            proof=batch.fingerprint(net,proj,fn,source,env)
            cancel()
            ok,diff,_=net._judge_with(proj,fn,target[0],list(target[1]),source)
            reason='' if ok else 'Does not compile or compare.' if diff is None else f'Different bytes (score {diff.score}).'
            return identity,name,ok,reason,proof
        except (NetworkError,toolchains.ToolchainError,OSError) as error:
            return identity,name,False,str(error)[:300],None
    for identity,name,ok,reason,proof in batch.parallel(candidates,check,cancel,thread_name='profile-test'):
        state['function']=name
        state['checked']+=1
        state['passed' if ok else 'failed']+=1
        if ok:
            state['matches'].append(name)
            proofs[identity]=proof
            tested_names[identity]=name
        else:state['failures'].append({'function':name,'reason':reason})
        publish()
        if state['checked']%25==0 or state['checked']==state['total']:
            log(f"{state['checked']}/{state['total']} checked: {state['passed']} passed, {state['failed']} did not pass.")
    cancel()
    # Passing receipts keep the same exact input checks as application. Re-read
    # one passing candidate at a time rather than inflating all successful sources.
    publish('Confirming successful inputs')
    current=net.project_store(proj['id']).one('SELECT * FROM projects WHERE id=?',proj['id'])
    current_env=batch.environment(net,current,selected) if current and proofs else None
    changed=[]
    for identity,proof in proofs.items():
        cancel()
        fn=net.project_store(proj['id']).one('SELECT * FROM functions WHERE project_id=? AND id=?',proj['id'],identity)
        try:
            same=(current_env is not None and fn is not None and fn['status']=='open'
                  and batch.fingerprint(net,current,fn,net._source_of(fn),current_env)==proof)
        except (NetworkError,toolchains.ToolchainError,OSError):same=False
        if not same:changed.append(tested_names[identity])
    state['receipt']=batch.remember(net,slug,selected,proofs)
    state['changed_matches']=changed
    state['stale']=bool(changed)
    state['compatible']=bool(candidates) and state['failed']==0 and not state['stale']
    log('Test finished. Settings, matches and saved scores are unchanged.'+
        (' Inputs changed during the test; passing results can be freshly rechecked and applied.' if changed else ''))
    return state
