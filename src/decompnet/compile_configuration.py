"""Worker-local resolved native compiler commands; custom and remote runtimes stay dynamic."""
import copy
import json
import os
from pathlib import Path

from . import compile_sessions, compile_timing, toolchains


def resolve(root,cfg,build):
    session=compile_sessions.current()
    if session is None:return build(root,cfg)
    compiler=cfg['compiler'];tid=compiler.get('toolchain')
    # A source update can import this optional cache while the process still
    # has the older registry module loaded. Resolve normally until a full reload;
    # never turn an unavailable optimization into a compiler failure.
    registered=getattr(toolchains,'_registered',None)
    if registered is None:return build(root,cfg)
    tc=registered(tid)
    if tc is None or compiler.get('cmd') or tc.local or tc.via_vm or tc.via_wsl or tc.wine:
        return build(root,cfg)
    # Read small install manifests exactly. Reinstalls, dependency changes, profile
    # edits and environment changes must resolve a fresh invocation.
    pending=[tid];seen=set();inputs=[]
    while pending:
        dep=pending.pop()
        if dep in seen:continue
        seen.add(dep);metadata=toolchains.get(dep)
        if metadata.via_vm or metadata.via_wsl or metadata.local or metadata.wine:return build(root,cfg)
        manifest=toolchains.path(dep)/toolchains.MANIFEST
        try:contents=manifest.read_bytes()
        except OSError:return build(root,cfg)
        inputs.append((dep,repr(metadata),str(manifest),contents))
        pending.extend((*metadata.requires,*metadata.headers))
        if why:=toolchains.runtime_error(dep):raise toolchains.ToolchainError(why)
    layout=(str(root.resolve()),tuple((kind,name,str((root/name).resolve()))
        for kind in ('include_dirs','ro_paths') for name in compiler.get(kind,[])))
    key=(layout,json.dumps(compiler,sort_keys=True),tuple(sorted(os.environ.items())),tuple(sorted(inputs)))
    cached=session.configurations.get(key)
    if cached is not None:
        invocation,paths,stamps=cached
        if _stamps(paths)==stamps:
            session.configurations.move_to_end(key)
            compile_timing.add('configuration_hits',1)
            return copy.deepcopy(invocation)
        del session.configurations[key]
    invocation=build(root,cfg)
    paths=tuple(sorted(set(invocation.ro_paths)|{
        arg for arg in (*invocation.cmd,*invocation.assembly_preprocess,*invocation.assembly_compile)
        if os.path.isabs(arg) and '{' not in arg}))
    session.configurations[key]=(copy.deepcopy(invocation),paths,_stamps(paths))
    while len(session.configurations)>16:session.configurations.popitem(last=False)
    return invocation


def _stamps(paths):
    stamps=[]
    for name in paths:
        try:
            stat=Path(name).stat()
            stamps.append((stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
        except OSError:stamps.append(None)
    return tuple(stamps)
