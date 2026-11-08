"""Read-only, ROM-backed local-repair benchmark for an existing exported build.

No project code, database or match record is written. The normal linker/data judge
supplies exact verdicts; displayed linked rows only guide the bounded search.
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from .. import compile_timing, consoles, scoring, toolchains
from ..compiler import compile_source
from ..elfobj import FunctionObj
from ..match_bundle import compiler_identity
from ..project import compiler_config, compiler_for
from ..rom import match, split
from ..rom.symbols import Symbol, SymbolTable
from . import draft_repairs, frozen_repair_cases, region_repairs, repair_benchmark


def evaluator(root, name, header):
    manifest_text = (root/'build.json').read_text()
    manifest = json.loads(manifest_text)
    console = consoles.get(manifest['platform'])
    if console.arch!='mips':
        raise ValueError('This exported-ROM adapter needs MIPS. Other builds can use repair_replay with a copied evaluator.')
    fn = next((f for f in manifest['functions'] if f['name']==name),None)
    if fn is None:
        raise ValueError('The function is absent from build.json.')
    exported_profile = compiler_for(manifest,fn['vram'], fn)
    profile = {k:exported_profile[k] for k in ('toolchain','flags','timeout') if k in exported_profile}
    if not toolchains.installed(profile['toolchain']):
        raise ValueError('Install the recorded pinned compiler first; benchmarks do not download tools.')
    image = (root/manifest['image']).read_bytes()
    if hashlib.sha1(image).hexdigest()!=manifest['sha1']:
        raise ValueError('The build image does not match its recorded SHA1.')
    symbols = [Symbol(key,value,0,'data') for key,value in manifest['symbols'].items()]
    symbols += [Symbol(f['name'],f['vram'],0,'func') for f in manifest['functions']]
    for path in (root/'asm/data').glob('**/*.s'):
        for label in re.finditer(r'dlabel (\w+)\s*\n\s*/\*\s*(?:[0-9A-F]+\s+)?([0-9A-F]{8})',path.read_text()):
            symbols.append(Symbol(label[1],int(label[2],16),0,'data'))
    table = SymbolTable(symbols)
    asm = root/fn['asm']
    target = match.target_from_split(split.parse_function(asm,fn['seg'],manifest['big_endian']),
                                    table.resolve,manifest['big_endian'],manifest.get('gp') or 0)
    def read(address,size):
        for segment in manifest['segments']:
            if segment['kind']!='code':
                continue
            end = segment.get('end') if segment.get('end') is not None else len(image)
            if segment['vram']<=address and address+size<=segment['vram']+end-segment['start']:
                offset = segment['start']+address-segment['vram']
                return image[offset:offset+size]
        return None
    if read(target.vram,len(target.code))!=target.code:
        raise ValueError('Assembly target bytes differ from the frozen build image.')
    # No exported commands are executed: construct only an already installed registry compiler.
    cfg = compiler_config(root,{'compiler':profile})
    canonical = match.canonical_target(target,table)
    linked_target = FunctionObj(name,console.arch,target.big_endian,target.code,cpu=console.cpu_variant)
    cache = {}
    def evaluate(source):
        if source in cache:
            return dict(cache[source],compiler_invocations=0)
        counts=compile_timing.Counts()
        with compile_timing.collect(counts,append=True):built = compile_source(cfg,header,source)
        if not built.ok:
            result = dict(score=None,matched=False,outcome='compile_error')
        else:
            linked = match.link(built.obj,name,target,table,read)
            if linked.candidate is None or linked.linked_code is None:
                result = dict(score=None,matched=False,outcome='link_error')
            else:
                raw = FunctionObj(name,console.arch,target.big_endian,linked.linked_code,cpu=console.cpu_variant)
                score = 0 if linked.exact else max(1,scoring.diff(canonical,linked.candidate).score)
                from ..source_lines import mismatches
                from .compiler_diagnostics import output_fingerprint
                from .repair_localisation import bind
                rows=scoring.diff(linked_target,raw).rows
                result = dict(score=score,rows=rows,matched=linked.exact,
                              output_fingerprint=output_fingerprint(built.obj),
                              repair_locations=bind(source,mismatches(built.obj,name,rows,source,arch=console.arch)),
                              outcome='exact_linked' if linked.exact else 'scored',
                              candidate_target=hashlib.sha256(linked.linked_code).hexdigest(),
                              candidate_relocs=repr(linked.problems))
        result['compiler_invocations']=counts.values.get('invocations',0)
        cache[source] = result
        return result
    scope = dict(manifest_sha256=hashlib.sha256(manifest_text.encode()).hexdigest(),
                 header_sha256=hashlib.sha256(header.encode()).hexdigest(),
                 target_sha256=hashlib.sha256(target.code).hexdigest(),compiler=profile,
                 compiler_identity=compiler_identity(profile))
    return evaluate,scope,console.arch,asm.read_text(),cache


def compiler_cost(cache):
    """A cache entry is a fresh candidate check, not necessarily one compiler process."""
    values=[r.get('compiler_invocations') for r in cache.values()]
    known=[n for n in values if type(n) is int and n>=0]
    return dict(compiler_invocations=sum(known) if len(known)==len(values) else None,
                fresh_compile_checks=len(values),invocation_measured_checks=len(known))


def benchmark(root, name, source, header, *, trials=128, seconds=120, families=()):
    if not 1<=trials<=4096 or not 1<=seconds<=1800:
        raise ValueError('Use 1–4096 trials and 1–1800 seconds.')
    evaluate,scope,arch,asm,cache = evaluator(root,name,header)
    baseline = evaluate(source)
    if baseline.get('score') is None or baseline['matched']:
        raise ValueError('Use a compiling, unresolved starting source.')
    def generate(parent):
        labels = {}
        candidates = draft_repairs.variants(parent,name,arch,asm,scope['compiler'].get('flags',[]),labels,
                                            limit=None,header=header,entry_scope=draft_repairs.promoted_entry_repairs.profile_scope(arch,scope['compiler']))
        order = region_repairs.order(list(dict.fromkeys(labels[c] for c in candidates)),cache[parent].get('rows') or [])
        return sorted([(labels[c],c) for c in candidates if not families or labels[c] in families],
                      key=lambda item:order.index(item[0]))
    result = repair_benchmark.run(source,baseline['score'],evaluate,generate,trials=trials,
                                  deadline=time.monotonic()+seconds,arch=arch,header=header)
    return {**result,'scope':scope,'starting_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            **compiler_cost(cache),'verdict':'Normal linked-byte and ROM-data judge; read-only, no adoption.'}


def suite(root, cases, *, trials=16, seconds=30, focused=True, omit_families=()):
    """Equal budgets for a frozen list of saved sources; never write or adopt a candidate."""
    from . import iterative_repairs
    if isinstance(trials,bool) or not isinstance(trials,int) or not 1<=trials<=4096:
        raise ValueError('Use 1–4096 trials per function.')
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=1800:
        raise ValueError('Use 1–1800 seconds per function.')
    prepared=[]
    applicability=[]
    for item in cases:
        evaluate,scope,arch,asm,compiled=evaluator(root,item['name'],item['header'])
        baseline=evaluate(item['source'])
        from .scratch_role_repairs import explain
        applicability.append(explain(item['source'],item['name'],item['header'],arch))
        if baseline.get('score') is None or baseline.get('matched'):
            raise ValueError('Every frozen case must compile and be unresolved under the copied judge.')
        prepared.append(dict(source=item['source'],baseline_score=baseline['score'],arch=arch,
            scope=hashlib.sha256(repr((scope,item['id']) if 'id' in item else scope).encode()).hexdigest(),
            name=item['name'],header=item['header'],asm=asm,flags=scope['compiler'].get('flags',[]),
            entry_scope=draft_repairs.promoted_entry_repairs.profile_scope(arch,scope['compiler']),
            evaluate=evaluate,compiled=compiled,selection={},pool_cache={}))
    def generate(case,source,report):
        return iterative_repairs.targeted_variants(source,report,{'name':case['name'],'asm':case['asm']},
            case['arch'],case['flags'],case['header'],tagged=True,selection=case['selection'],
            pool_cache=case['pool_cache'],focused=focused,entry_scope=case['entry_scope'],omit_families=omit_families)
    result=repair_benchmark.batch(prepared,lambda c,s:c['evaluate'](s),generate,
        trials=trials,seconds=seconds,generate_report=True)
    # Case scope includes precursor ids; count actual functions independently of those ids.
    distinct=len({c['name'] for c in prepared})
    from collections import Counter
    declines=Counter();reason_cases=Counter()
    for item in applicability:
        declines.update(item['declined']);reason_cases.update(item['declined'].keys())
    return result | {'scratch_role_coverage':dict(eligible_cases=sum(i['candidates']>0 for i in applicability),
                      declined_cases=sum(i['candidates']==0 for i in applicability),decline_reasons=dict(declines),decline_reason_cases=dict(reason_cases)),
                     'coverage':dict(cases=len(prepared),distinct_functions=distinct,repeated_precursors=len(prepared)-distinct),
                     **compiler_cost({(i,key):r for i,c in enumerate(prepared) for key,r in c['compiled'].items()}),
                     'focused':focused,'omitted_families':list(omit_families),
                     'verdict':'Copied ROM-backed linked judge; no adoption or live project writes.'}


def audit(root,name,stages,header,*,proposals=256,probes=8,seconds=120):
    from . import iterative_repairs, repair_sequence_audit
    if not 1<=seconds<=1800:raise ValueError('Use 1–1800 seconds.')
    evaluate,scope,arch,asm,cache=evaluator(root,name,header)
    entry=draft_repairs.promoted_entry_repairs.profile_scope(arch,scope['compiler'])
    def generate(source,report):
        return iterative_repairs.targeted_pool(source,report,{'name':name,'asm':asm},arch,
            scope['compiler'].get('flags',[]),header,entry_scope=entry)
    def retains(baseline,control,family):
        node=dict(path=[family],depth=1,ceiling=baseline.get('score') or 0,
                  parent_score=baseline.get('score'),baseline_rows=baseline.get('rows'),
                  region_baseline=region_repairs.snapshot(baseline.get('rows'),arch))
        return iterative_repairs.retention_evidence(node,control,arch)['eligible']
    deadline=time.monotonic()+seconds
    result=repair_sequence_audit.audit(stages,evaluate,generate,retains=retains,proposals=proposals,probes=probes,
                                     stopped=lambda:time.monotonic()>=deadline)
    return {**result,'scope':scope,**compiler_cost(cache)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--function')
    parser.add_argument('--audit-path',type=Path,help='Private manifest of intermediate path/source_sha256 stages; no adoption.')
    parser.add_argument('--cases',type=Path,help='Private JSON list of name/before/header paths, frozen before replay.')
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--holdouts',type=int,help='Select 2–16 distinct functions by stable hash while freezing, before outcomes are measured.')
    parser.add_argument('--development-functions',default='',help='Comma-separated development examples to exclude from holdout selection.')
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--before',type=Path)
    parser.add_argument('--header',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=128)
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--families',default='')
    parser.add_argument('--omit-families',default='',help='Case-suite ablation: omit named families before paging, with unchanged budgets.')
    args = parser.parse_args()
    try:
        if (args.holdouts is not None or args.development_functions) and not (args.cases and args.freeze):
            raise ValueError('Holdout selection requires --cases and --freeze.')
        if args.development_functions and args.holdouts is None:raise ValueError('Use --development-functions with --holdouts.')
        if args.omit_families and (not args.cases or args.freeze):
            raise ValueError('Use --omit-families only when replaying a frozen case suite.')
        if args.audit_path:
            if args.cases or args.before or args.freeze or args.reference or args.families or not args.function or not args.header:
                raise ValueError('Use --audit-path with --function and --header only.')
            stages=[]
            for item in json.loads(args.audit_path.read_text())['stages']:
                source=(args.audit_path.parent/item['path']).read_text()
                if hashlib.sha256(source.encode()).hexdigest()!=item['source_sha256']:
                    raise ValueError('An audit source changed; freeze a new manifest.')
                stages.append({'source':source})
            report=audit(args.project,args.function,stages,args.header.read_text(),probes=min(args.trials,128),seconds=args.seconds)
        elif args.cases:
            if args.function or args.before or args.header or args.families:
                raise ValueError('Choose either frozen cases or single-function arguments.')
            cases,frozen=frozen_repair_cases.load(args.cases,args.project,evaluator,freeze=args.freeze,holdouts=args.holdouts,
                development_functions=tuple(filter(None,args.development_functions.split(','))))
            report=frozen if args.freeze else suite(args.project,cases,trials=args.trials,seconds=args.seconds,
                                                   omit_families=tuple(filter(None,args.omit_families.split(','))))
            if args.reference:
                if args.freeze:raise ValueError('Compare replay reports, not freeze manifests.')
                report['comparison']=repair_benchmark.compare(json.loads(args.reference.read_text()),report)
        else:
            if args.freeze or args.reference or not all((args.function,args.before,args.header)):
                raise ValueError('Supply frozen --cases, or --function, --before and --header.')
            report = benchmark(args.project,args.function,args.before.read_text(),args.header.read_text(),
                               trials=args.trials,seconds=args.seconds,families=tuple(filter(None,args.families.split(','))))
    except (ValueError,KeyError,OSError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('trials','scope')},indent=2))


if __name__=='__main__':
    main()
