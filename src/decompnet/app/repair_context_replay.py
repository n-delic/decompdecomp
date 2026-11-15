"""Isolate captured compiler and header changes without adopting either or attributing causation."""
import time
from pathlib import Path

from .. import scoring, toolchains
from ..compiler import compile_source
from ..elfobj import extract_function
from ..project import compiler_config


def replay(evidence, *, seconds=20, measure=None, root=None, check_cancel=lambda:None):
    from .repair_replay import digest
    stages=[evidence.get(key) or {} for key in ('before','after')]
    report={'trials':[], 'verdict':'compiler-output regression only; no linked/objdiff match adoption'}
    for stage in stages:
        if not stage.get('source') or stage.get('header') is None or digest(stage['source'])!=stage.get('source_sha256'):
            return report | {'skipped':'Missing or changed source/header evidence.'}
        expected=(stage.get('settings') or {}).get('context_sha256')
        if expected and digest(stage['header'])!=expected:return report | {'skipped':'Captured header hash does not match.'}
    before,after=stages
    if before['source']!=after['source']:return report | {'skipped':'Context isolation requires unchanged C.'}
    profiles=[(s.get('settings') or {}).get('compiler') or {} for s in stages]
    if measure is None:
        if any((s.get('settings') or {}).get('mode')!='rom' for s in stages):
            return report | {'skipped':'Imported projects require a copied build for context isolation.'}
        if any(not isinstance(p,dict) or not p.get('toolchain') or p.get('cmd') or not toolchains.installed(p['toolchain']) for p in profiles):
            return report | {'skipped':'Both captured pinned compilers must already be installed.'}
        def measure(source,header,profile):
            cfg=compiler_config(Path(root or '.'),{'compiler':profile})
            result=compile_source(cfg,header,source)
            if not result.ok:return {'error':result.log[:500]}
            from .compiler_diagnostics import allocated_output
            obj=extract_function(result.obj,evidence['function'])
            return {'object':obj,'data':allocated_output(result.obj)} if obj else {'error':'Captured function missing from object.'}
    started=time.monotonic();outputs={}
    # Controls first, then one-factor combinations. At most four compiles, no source search.
    for label,pi,hi in (('before',0,0),('after',1,1),('compiler only',1,0),('header only',0,1)):
        if time.monotonic()-started>=seconds:
            report['exhausted']=True;break
        check_cancel()
        value=measure(before['source'],stages[hi]['header'],profiles[pi]);check_cancel()
        outputs[label]=value
        report['trials'].append({'combination':label,'error':value.get('error')})
    control=outputs.get('after',{})
    if not control.get('object'):return report | {'skipped':'Successful control did not compile within budget.'}
    for row in report['trials']:
        value=outputs[row['combination']]
        row['control_output_recovered']=bool(value.get('object') and value['object'].fingerprint()==control['object'].fingerprint() and value.get('data')==control.get('data'))
        if value.get('object'):row['score']=scoring.diff(control['object'],value['object']).score
    report['context_trials']=report['trials']
    report['summary']={'context_output_recovered':sum(r['control_output_recovered'] for r in report['trials'] if r['combination'] not in ('before','after'))}
    report['note']='Compiler-only and header-only results isolate captured inputs; output recovery does not establish a game match or prove a general compiler rule.'
    return report
