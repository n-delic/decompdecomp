"""Bounded evidence for unanalysed MIPS ROM code; reports never alter targets or prove code."""
from collections import Counter

from .mips import JR_RA, is_frame, rabbitizer_category

MAX_STEPS = 4096
MAX_CANDIDATES = 128


def extent(image, start, end, vram, order, cpu=""):
    """Follow direct control flow inside a gap; indirect branches remain unresolved."""
    import rabbitizer

    pending=[start];seen=set();returns=0;unknown=False;highest=start
    while pending and len(seen)<MAX_STEPS:
        at=pending.pop()
        if at in seen:continue
        if at<start or at+4>end:
            unknown=True;continue
        w=int.from_bytes(image[at:at+4],order)
        if not rabbitizer.Instruction(w,0,rabbitizer_category(cpu)).isValid():
            unknown=True;continue
        seen.add(at);highest=max(highest,at+4);op=w>>26
        transfer=op in (1,2,3,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8) or (op==0 and w&63 in (8,9))
        if not transfer:
            pending.append(at+4);continue
        if at+8>end or not rabbitizer.Instruction(int.from_bytes(image[at+4:at+8],order),0,rabbitizer_category(cpu)).isValid():
            unknown=True;continue
        seen.add(at+4);highest=max(highest,at+8)
        if w==JR_RA:
            returns+=1;continue
        if op==3 or (op==0 and w&63==9):  # calls return; the callee is a separate audit seed
            pending.append(at+8);continue
        if op==0:
            unknown=True;continue
        if op==2:
            pc=vram+at-start
            target=start+((((pc+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)
        else:
            delta=w&0xffff;delta=delta-0x10000 if delta&0x8000 else delta
            target=at+4+delta*4
            pending.append(at+8)
        pending.append(target)
    return {'decoded_instructions':len(seen),'return_sites':returns,'direct_flow_closed':bool(returns and not unknown and not pending),
            'extent_bytes':highest-start,'step_limit':bool(pending)}


def audit(image, segments, functions, *, big_endian=True, cpu="", progress=lambda _:None):
    """Frozen image + known code ranges. Even closed direct flow remains review-only."""
    order='big' if big_endian else 'little'
    spans=sorted((s['rom_start'],s['rom_end'],s['vram']) for s in segments)
    if any(not 0<=lo<=hi<=len(image) or lo%4 or hi%4 for lo,hi,_ in spans):
        raise ValueError('Code segments must be word-aligned ranges inside the image.')
    if any(f['rom']<0 or f['rom']%4 or f['size']%4 or f['rom']+f['size']>len(image) for f in functions):
        raise ValueError('Function ranges must stay inside the image.')
    mappings=Counter(v-s for s,_,v in spans)
    end=0x1000;gaps=[]
    for lo,hi,_ in spans:
        if lo>end:gaps.append((end,lo))
        end=max(end,hi)
    if end<len(image):gaps.append((end,len(image)))
    # Do not infer a mapping from data. Reuse only mappings established by code segments.
    mapping_choices=[m for m,_ in mappings.most_common(8)]
    candidates={};references=0;overflow=False
    def gap_at(offset):return next(((a,b) for a,b in gaps if a<=offset<b),None)
    for fn in functions:
        if fn.get('handwritten'):continue  # microcode and kernel register conventions are not CPU-call evidence
        for at in range(fn['rom'],fn['rom']+fn['size']-3,4):
            w=int.from_bytes(image[at:at+4],order);op=w>>26
            pc=fn['vram']+at-fn['rom']
            if op in (2,3):target=((pc+4)&0xf0000000)|((w&0x3ffffff)<<2)
            elif op in (1,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8):
                d=w&0xffff;target=pc+4+(d-0x10000 if d&0x8000 else d)*4
            else:continue
            for mapping in mapping_choices:
                offset=target-mapping;gap=gap_at(offset)
                if not gap or offset%4:continue
                key=(offset,target)
                if key not in candidates and len(candidates)>=MAX_CANDIDATES:
                    overflow=True;continue
                row=candidates.setdefault(key,dict(rom_offset=offset,vram=target,gap_start=gap[0],gap_end=gap[1],
                                                  evidence='incoming direct transfer',incoming=[]))
                references+=1
                if len(row['incoming'])<8:row['incoming'].append({'function':fn['name'],'address':pc,'kind':'call' if op==3 else 'branch'})
    # Quiet framed bodies with no incoming transfer are weaker hints; cap them separately.
    hints=0
    for lo,hi in gaps:
        progress({'phase':'Auditing binary gaps','start':lo,'end':hi})
        for at in range((lo+3)&~3,hi-3,4):
            if hints>=32:break
            w=int.from_bytes(image[at:at+4],order)
            if not is_frame(w):continue
            for mapping in mapping_choices[:1]:
                key=(at,at+mapping)
                if key in candidates or len(candidates)>=MAX_CANDIDATES:continue
                info=extent(image,at,min(hi,at+0x10000),at+mapping,order,cpu)
                if info['return_sites'] and info['decoded_instructions']>=4:
                    candidates[key]=dict(rom_offset=at,vram=at+mapping,gap_start=lo,gap_end=hi,
                                         evidence='frame/return pattern only',incoming=[],**info);hints+=1
    rows=[]
    for row in candidates.values():
        info=extent(image,row['rom_offset'],min(row['gap_end'],row['rom_offset']+0x10000),row['vram'],order,cpu)
        rows.append({**row,**info,'review_only':True})
    rows.sort(key=lambda r:(not bool(r['incoming']),not r['direct_flow_closed'],r['rom_offset'],r['vram']))
    return {'gaps':len(gaps),'gap_bytes':sum(b-a for a,b in gaps),'candidates':rows,'incoming_references':references,
            'candidate_limit':overflow,'mapping_limit':len(mappings)>8,
            'note':'Review evidence only. Valid instructions, calls and returns can occur in data or microcode. No targets, statuses, source or compiler settings changed.'}
