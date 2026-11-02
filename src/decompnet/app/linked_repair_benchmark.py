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

from .. import consoles, scoring, toolchains
from ..compiler import compile_source
from ..elfobj import FunctionObj
from ..match_bundle import compiler_identity
from ..project import compiler_config, compiler_for
from ..rom import match, split
from ..rom.symbols import Symbol, SymbolTable
from . import draft_repairs, repair_benchmark, repair_ranking


def evaluator(root, name, header):
    manifest_text = (root/'build.json').read_text()
    manifest = json.loads(manifest_text)
    console = consoles.get(manifest['platform'])
    if console.arch!='mips':
        raise ValueError('This exported-ROM adapter needs MIPS. Other builds can use repair_replay with a copied evaluator.')
    fn = next((f for f in manifest['functions'] if f['name']==name),None)
    if fn is None:
        raise ValueError('The function is absent from build.json.')
    exported_profile = compiler_for(manifest,fn['vram'])
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
            return cache[source]
        built = compile_source(cfg,header,source)
        if not built.ok:
            result = dict(score=None,matched=False,outcome='compile_error')
        else:
            linked = match.link(built.obj,name,target,table,read)
            if linked.candidate is None or linked.linked_code is None:
                result = dict(score=None,matched=False,outcome='link_error')
            else:
                raw = FunctionObj(name,console.arch,target.big_endian,linked.linked_code,cpu=console.cpu_variant)
                score = 0 if linked.exact else max(1,scoring.diff(canonical,linked.candidate).score)
                result = dict(score=score,rows=scoring.diff(linked_target,raw).rows,matched=linked.exact,
                              outcome='exact_linked' if linked.exact else 'scored',
                              candidate_target=hashlib.sha256(linked.linked_code).hexdigest(),
                              candidate_relocs=repr(linked.problems))
        cache[source] = result
        return result
    scope = dict(manifest_sha256=hashlib.sha256(manifest_text.encode()).hexdigest(),
                 header_sha256=hashlib.sha256(header.encode()).hexdigest(),
                 target_sha256=hashlib.sha256(target.code).hexdigest(),compiler=profile,
                 compiler_identity=compiler_identity(profile))
    return evaluate,scope,console.arch,asm.read_text(),cache


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
                                            limit=None,header=header)
        order = repair_ranking.order(list(dict.fromkeys(labels[c] for c in candidates)),cache[parent].get('rows') or [])
        return sorted([(labels[c],c) for c in candidates if not families or labels[c] in families],
                      key=lambda item:order.index(item[0]))
    result = repair_benchmark.run(source,baseline['score'],evaluate,generate,trials=trials,
                                  deadline=time.monotonic()+seconds)
    return {**result,'scope':scope,'starting_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'compiler_invocations':len(cache),'verdict':'Normal linked-byte and ROM-data judge; read-only, no adoption.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--function',required=True)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--header',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=128)
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--families',default='')
    args = parser.parse_args()
    try:
        report = benchmark(args.project,args.function,args.before.read_text(),args.header.read_text(),
                           trials=args.trials,seconds=args.seconds,families=tuple(filter(None,args.families.split(','))))
    except (ValueError,KeyError,OSError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('trials','scope')},indent=2))


if __name__=='__main__':
    main()
