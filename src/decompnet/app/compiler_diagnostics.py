"""Optional installed-GCC phase summaries, isolated from match adoption and compiler settings."""
import hashlib
import json
import re
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from .. import toolchains
from ..compiler import capture_artifacts, compile_source
from ..elfobj import extract_function
from ..project import compiler_config

# Legacy GCC runs its second CSE after loop optimization, before flow/allocation.
PHASE_ORDER = ('rtl','jump','cse','loop','cse2','flow','combine','sched','lreg','greg','sched2','jump2','dbr')
PHASES = set(PHASE_ORDER)


def output_fingerprint(raw):
    """Unknown object formats carry no equivalence evidence; judging is unchanged."""
    try:
        return allocated_output(raw)
    except (ELFError, ValueError, TypeError, IndexError, KeyError):
        return None


def allocated_output(raw):
    """Include static helper code, initialized data, BSS sizes and named relocations."""
    elf = ELFFile(BytesIO(raw))
    sections = []; relocations = []
    for section in elf.iter_sections():
        if section['sh_flags'] & 2:
            sections.append((section.name,section['sh_size'],section['sh_flags'],
                             section.data().hex() if section['sh_type']!='SHT_NOBITS' else None))
        if isinstance(section,RelocationSection):
            symbols = elf.get_section(section['sh_link'])
            destination = elf.get_section(section['sh_info'])
            if not destination['sh_flags'] & 2:
                continue
            for relocation in section.iter_relocations():
                relocations.append((destination.name,relocation['r_offset'],relocation['r_info_type'],
                                    symbols.get_symbol(relocation['r_info_sym']).name,relocation.entry.get('r_addend')))
    return hashlib.sha256(repr((sections,relocations)).encode()).hexdigest()


def phase_summary(work, name=None, arch=None):
    texts = {}
    result = {}; remaining = 4*1024*1024
    for path in sorted(Path(work).glob('func.*')):
        phase = path.suffix[1:]
        if phase not in PHASES or path.is_symlink() or remaining<=0:
            continue
        limit = min(1024*1024,remaining)
        with path.open('rb') as stream:
            raw = stream.read(limit)
        remaining -= len(raw)
        text = raw.decode(errors='replace')
        if name and path.stat().st_size<=len(raw):texts[phase]=text
        registers = sorted(set(map(int,re.findall(r'\(reg(?:/[^:\s()]*)?:\w+\s+(\d+)',text))))
        lifetimes = []
        for match in re.finditer(r'^Register (\d+) used (\d+) times across (\d+) insns([^\n]*)',text,re.M):
            crossed = re.search(r'crosses (\d+) calls?',match[4])
            lifetimes.append({'register':int(match[1]),'references':int(match[2]),'live_insns':int(match[3]),
                              'calls_crossed':int(crossed[1]) if crossed else 0,'pointer':'pointer' in match[4]})
        lifetimes.sort(key=lambda row:(-row['calls_crossed'],-row['references']))
        priority = sorted((dict(row, legacy_weight=(row['references'].bit_length()-1)*row['references']/row['live_insns'])
                           for row in lifetimes if row['references']>0 and row['live_insns']>0),
                          key=lambda row:-row['legacy_weight'])[:16]
        result[phase] = {'allocation_priority':priority,
                         'loop_begin_notes':len(re.findall(r'NOTE_INSN_LOOP_BEG',text)),
                         'lifetimes':lifetimes[:32],'file':path.name,'bytes':path.stat().st_size,'truncated':path.stat().st_size>len(raw),
                         'captured_sha256':hashlib.sha256(raw).hexdigest(),'registers':registers,
                         'empty_conflicts':list(map(int,re.findall(r'^;; (\d+) conflicts:\s*$',text,re.M)))[:16],
                         'assignments':re.findall(r'^;; Register \d+ in \d+\.',text,re.M)[:24]}
    retired = sorted(set(result.get('flow',{}).get('registers',[]))-set(result.get('combine',{}).get('registers',[])))
    for row in result.values():
        row['register_count'] = len(row.pop('registers'))
    from . import gcc_allocation, rtl_value_trace
    return {'phases':result,'removed_between_flow_and_combine':retired[:24],
            **({'value_trace':rtl_value_trace.summarize(texts,name,arch),
                'allocation':gcc_allocation.summarize(texts,name,arch)} if name else {})}


def diagnose(profile, header, source, name, *, root='.', baseline_obj=None, on_compile=lambda: None):
    tid = profile.get('toolchain','')
    if not tid or 'cmd' in profile or not toolchains.installed(tid):
        return {'skipped':'Diagnostics require an already installed pinned compiler; nothing is downloaded.'}
    tc = toolchains.get(tid)
    if not (tid.startswith('gcc-') or tc.family=='gcc') or tc.driver:
        return {'skipped':'Phase capture currently supports native GCC drivers only.'}
    cfg = compiler_config(Path(root),{'compiler':{'toolchain':tid,'flags':profile.get('flags',[])}})
    if cfg.wsl or cfg.linux_vm or 'gcc' not in Path(cfg.cmd[0]).name.lower():
        return {'skipped':'This compiler adapter/runtime does not expose native GCC dump files.'}
    if baseline_obj is None:
        on_compile()
        baseline = compile_source(cfg,header,source)
        if not baseline.ok:
            return {'skipped':'Normal diagnostic baseline did not compile.','error':baseline.log[:500]}
        baseline_obj = baseline.obj
    captured = {}
    def collect(work):
        captured.update(phase_summary(work,name,tc.arch))
    with capture_artifacts(collect):
        on_compile()
        diagnostic = compile_source(replace(cfg,cmd=[*cfg.cmd,'-da']),header,source)
    if not diagnostic.ok:
        return {'skipped':'This GCC build did not accept phase capture.','error':diagnostic.log[:500]}
    try:
        before, after = extract_function(baseline_obj,name),extract_function(diagnostic.obj,name)
        equal = bool(before and after and before.fingerprint()==after.fingerprint() and
                     allocated_output(baseline_obj)==allocated_output(diagnostic.obj))
    except (ValueError,KeyError,ELFError) as error:
        return {'skipped':'Unsupported diagnostic object format: '+str(error)[:150]}
    if not captured.get('phases'):
        return {'skipped':'No supported legacy GCC phase dumps were produced.','output_unchanged':equal}
    if 'value_trace' in captured:
        captured['value_trace']['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
        captured['value_trace']['header_sha256']=hashlib.sha256(header.encode()).hexdigest()
        from .rtl_value_trace import anchors
        anchors(captured['value_trace'],source)
        captured['value_trace']['profile_sha256']=hashlib.sha256(json.dumps(profile,sort_keys=True,default=str).encode()).hexdigest()
    return {'toolchain':tid,'original_flags':profile.get('flags',[]),'diagnostic_flags':['-da'],
            'output_unchanged':equal,**captured,
            'note':'Isolated diagnostic compilation; no settings or progress changed. Removed pseudos and empty conflict lists are investigation leads, not proof of a spill cause. Phase evidence is comparable only when output_unchanged is true.'}


def compare(before,after):
    """Compare frozen GCC phases only when diagnostic flags preserved both normal outputs."""
    if not before.get('output_unchanged') or not after.get('output_unchanged'):
        return {'comparable':False,'note':'Diagnostic output changed or is unavailable; do not attribute a cause.'}
    if before.get('toolchain')!=after.get('toolchain') or before.get('original_flags')!=after.get('original_flags'):
        return {'comparable':False,'note':'Different compiler profiles cannot establish a source-only comparison.'}
    from .rtl_value_trace import compare as compare_values
    values=compare_values(before.get('value_trace'),after.get('value_trace'))
    phases = []
    for phase in PHASE_ORDER:
        a,b = before.get('phases',{}).get(phase),after.get('phases',{}).get(phase)
        if not a or not b:continue
        if a.get('truncated') or b.get('truncated'):continue
        if a.get('captured_sha256')==b.get('captured_sha256'):continue
        phases.append({'phase':phase,'register_count_before':a.get('register_count'),
                       'register_count_after':b.get('register_count'),
                       'assignments_changed':a.get('assignments')!=b.get('assignments'),
                       'lifetimes_changed':a.get('lifetimes')!=b.get('lifetimes'),
                       'loop_weights_changed':a.get('loop_begin_notes')!=b.get('loop_begin_notes'),
                       'empty_conflicts_changed':a.get('empty_conflicts')!=b.get('empty_conflicts')})
    changed = {p['phase'] for p in phases}
    allocation=any(p['phase'] in {'lreg','greg'} and (p['assignments_changed'] or p['empty_conflicts_changed']) for p in phases)
    lifetime=any(p['lifetimes_changed'] for p in phases)
    scheduling=bool(changed & {'sched','sched2','dbr'})
    lead = ('allocation/lifetime' if allocation else 'lifetime and scheduling' if lifetime and scheduling
            else 'lifetime' if lifetime else 'scheduling' if scheduling else 'earlier optimization' if changed else 'no comparable phase changes')
    from .gcc_allocation import priority_changes
    priority = priority_changes(before.get('allocation'), after.get('allocation'))
    return {'comparable':True,'changed':phases,'lead':lead,'value_changes':values,
            'allocation_priority_changes':priority,
            'note':'Different input source can renumber pseudos and dumps. Phase changes are investigation leads, not causal proof. allocation_priority uses the legacy GCC floor(log2(references))*references/live_insns heuristic; it is advisory, not a claim about every compiler build.'}
