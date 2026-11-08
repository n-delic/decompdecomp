"""Opt-in pinned-compiler macro evidence. Expanded text is private analysis, never a repair."""
import hashlib
import re
import time
from dataclasses import replace
from pathlib import Path

from .. import compiler, toolchains
from ..forks.csource import find
from ..project import compiler_config


def inspect(profile, header, source, name, *, root='.'):
    tid = profile.get('toolchain')
    if not tid or 'cmd' in profile or not toolchains.installed(tid):
        return {'available':False,'reason':'Use an already installed pinned compiler; analysis downloads nothing.'}
    tc = toolchains.get(tid)
    if not (tid.startswith('gcc-') or tc.family == 'gcc') or tc.driver:
        return {'available':False,'reason':'This compiler driver has no verified analysis preprocessing adapter.'}
    cfg = compiler_config(Path(root), {'compiler':{k:profile[k] for k in ('toolchain','flags','timeout') if k in profile}})
    if cfg.cmd.count('-c') != 1:
        return {'available':False,'reason':'The pinned compiler command has no unambiguous preprocessing mode.'}
    # Includes must be captured in the compiler sandbox; do not read arbitrary local includes.
    if re.search(r'(?m)^\s*#\s*(?:include|include_next|import)\b',header+'\n'+source):
        return {'available':False,'reason':'Capture included declarations in the frozen header before macro analysis.'}
    try:
        for text in (source,header):
            compiler.check_source(text)
    except compiler.SourceRejected:
        return {'available':False,'reason':'Frozen source/header failed ordinary source checks; preprocessing was not run.'}
    started = time.monotonic()
    result = compiler.compile_source(replace(cfg,cmd=['-E' if t=='-c' else t for t in cfg.cmd],
                                              cplusplus=False,identity=None),header,source)
    raw = result.obj or b''
    if not result.ok or not raw or len(raw)>4*1024*1024:
        return {'available':False,'reason':'Pinned preprocessing failed or exceeded the 4 MB analysis limit.'}
    text = raw.decode('utf-8',errors='strict')
    # Only line markers are removed for parsing; the output is never compiled as a replacement.
    parsed = re.sub(r'(?m)^\s*#[^\n]*','',text)
    span = find(parsed,name)
    if not span:
        return {'available':False,'reason':'The preprocessed translation unit does not contain the focal function.'}
    return dict(available=True,elapsed_seconds=time.monotonic()-started,source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                header_sha256=hashlib.sha256(header.encode()).hexdigest(),
                expanded_sha256=hashlib.sha256(raw).hexdigest(),expanded_bytes=len(raw),
                analysis=parsed[span[0]:span[1]],analysis_header=parsed[:span[0]]+'\n'+parsed[span[1]:], note='Pinned compiler expansion only. No alias proof, source adoption or match verdict.')


def summary(report, name, arch=None):
    """Only structured eligibility evidence leaves the private analysis buffer."""
    result = {k:v for k,v in report.items() if k not in {'analysis','analysis_header'}}
    if report.get('available'):
        from .call_preparation_repairs import explain
        result['expanded_call_eligibility'] = explain(report['analysis'],name,report.get('analysis_header',''),arch)
        result['note'] += ' Eligible expanded shapes are leads; edits still require mapping to original source and normal judging.'
    return result
