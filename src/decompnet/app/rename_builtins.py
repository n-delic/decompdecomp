"""Bounded GNU builtin guards for renames, accepted only by the existing byte judge."""
import copy
import json

from .. import toolchains
from ..project import compiler_for, function_compiler_key
from .function_names import BUILTIN_NAMES


def protect(net, proj, pairs, rows, sources, header_for, broken, progress=None):
    """Retry failed callers only; never change a project default or an unrelated profile.

    Old GCC releases lack per-name suppression, so try it first, then -fno-builtin
    for that one translation unit. Even the broad fallback must preserve exact bytes.
    Other compiler families retain the normal rejected rename until they have adapters.
    """
    names=sorted({new for old,new in pairs if old!=new and new in BUILTIN_NAMES})
    if not names or not broken:return proj,broken,{}
    cfg=copy.deepcopy(json.loads(proj['config']));guards={};remaining=[]
    by_name={r['name']:r for r in rows}
    for name,why in broken:
        fn=by_name.get(name)
        if fn is None:remaining.append((name,why));continue
        own=compiler_for(cfg,fn['vram'],fn)
        if own.get('cmd') or not own.get('toolchain','').startswith('gcc-'):
            remaining.append((name,why));continue
        base_flags=own.get('flags',toolchains.get(own['toolchain']).flags) if toolchains.known(own['toolchain']) else own.get('flags',[])
        key=function_compiler_key(fn) or next((k for k,p in cfg.get('function_compilers',{}).items()
            if p.get('function')==fn['name'] and p.get('vram')==fn['vram']), 'rename-builtin:'+str(fn['id']))
        for flags in ([*base_flags,*['-fno-builtin-'+n for n in names]],
                      [*base_flags,'-fno-builtin']):
            trial=copy.deepcopy(cfg)
            profile={**trial.get('function_compilers',{}).get(key,{}),
                     'function':fn['name'],'vram':fn['vram'],'toolchain':own['toolchain'],
                     'flags':flags, 'reason':'Preserve calls after builtin symbol rename: '+', '.join(names)}
            trial.setdefault('function_compilers',{})[key]=profile
            candidate=dict(proj) | {'config':json.dumps(trial)}
            if progress:progress({'phase':'Preserving builtin calls','message':name+': checking '+flags[-1]})
            failed=net.regression_test(candidate,None,sources=sources,only=[fn],header_for=header_for)
            if not failed:
                cfg=trial;guards[name]=profile;break
        else:remaining.append((name,why))
    return dict(proj) | {'config':json.dumps(cfg)},remaining,guards
