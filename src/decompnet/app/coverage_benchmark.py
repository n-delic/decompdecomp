"""Measure a ROM code audit against symbols from an owned complete demo ELF.

Ground truth is used only to construct corruptions and score results. The audit
receives original bytes and deliberately damaged detected extents, never source
or the withheld symbol list. This measures discovery, not C matching.
"""
import argparse
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile

from ..arch.mips_gaps import audit


def truth(image,elf_path,rom_start,vram):
    with elf_path.open('rb') as stream:
        elf=ELFFile(stream);section=elf.get_section_by_name('.text');symbols=elf.get_section_by_name('.symtab')
        if section is None or symbols is None or elf.little_endian or elf['e_machine']!='EM_MIPS':raise ValueError('Use a big-endian MIPS demo ELF with .text and function symbols.')
        code=section.data();start=rom_start+section['sh_addr']-vram
        if start<0 or image[start:start+len(code)]!=code:raise ValueError('ELF .text does not agree with the ROM mapping.')
        rows={}
        for sym in symbols.iter_symbols():
            if sym['st_info']['type']!='STT_FUNC' or not sym['st_size'] or not isinstance(sym['st_shndx'],int):continue
            if elf.get_section(sym['st_shndx']).name!='.text':continue
            at=sym['st_value'];size=sym['st_size'];off=rom_start+at-vram
            if size%4 or off<start or off+size>start+len(code):raise ValueError('A ground-truth function exceeds the verified text region.')
            rows.setdefault(at,dict(name=sym.name,vram=at,rom=off,size=size))
        return sorted(rows.values(),key=lambda f:f['vram']),[dict(rom_start=start,rom_end=start+len(code),vram=section['sh_addr'])]


def run(image,functions,segments,limit=24):
    if not 2<=limit<=128:raise ValueError('Use a sample of 2 to 128 functions.')
    # Deterministic size-spread sampling includes both small leaf helpers and large
    # functions. Never silently report this bounded sample as all-function recall.
    eligible=sorted([f for f in functions if f['size']>=16],key=lambda f:(f['size'],f['vram']))
    chosen=eligible if len(eligible)<=limit else [eligible[round(i*(len(eligible)-1)/(limit-1))] for i in range(limit)]
    cases=[]
    for target in chosen:
        for mode in ('hidden','truncated','misclassified'):
            rows=[dict(f) for f in functions if f['vram']!=target['vram']]
            if mode!='hidden':rows.append({**target,'size':4,'status':'asm' if mode=='misclassified' else 'open'})
            result=audit(image,segments,rows)
            expected=target['rom'] if mode=='hidden' else target['rom']+4
            candidate=next((r for r in result['candidates'] if r['rom_offset']==expected),None)
            warnings=[r for r in result['boundary_candidates'] if r['function']==target['name']]
            # Count candidate starts outside any true body, without confusing an
            # internal-label lead with a false claim of a distinct new function.
            outside=sum(not any(f['rom']<=r['rom_offset']<f['rom']+f['size'] for f in functions) for r in result['candidates'])
            cases.append({'function':target['name'],'bytes':target['size'],'mode':mode,'detected':bool(candidate or warnings),
                          'continuation_candidate':candidate is not None,'boundary_warning':bool(warnings),
                          'candidate_starts_outside_code_bodies':outside,
                          'search_limited':any(result.get(k) for k in ('candidate_limit','mapping_limit','pointer_probe_limit','callback_scan_limit','hint_scan_limit'))})
    metrics={mode:{'detected':sum(r['detected'] for r in cases if r['mode']==mode),
                   'tested':sum(r['mode']==mode for r in cases)} for mode in ('hidden','truncated','misclassified')}
    return {'ground_truth_functions':len(functions),'sampled_functions':len(chosen),'metrics':metrics,'cases':cases,
            'limitations':'Bounded size-spread sample from one demo, not a retail-game recall estimate. Valid code patterns can exist in data. Candidate detection is not function-boundary correctness or a C match.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True);parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--rom-start',type=lambda x:int(x,0),default=0x1000);parser.add_argument('--vram',type=lambda x:int(x,0),default=0x80000400)
    parser.add_argument('--sample',type=int,default=24);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 2<=args.sample<=128:parser.error('Use a sample of 2 to 128 functions.')
    image=args.image.read_bytes();functions,segments=truth(image,args.elf,args.rom_start,args.vram)
    result=run(image,functions,segments,args.sample);args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['metrics']))


if __name__=='__main__':main()
