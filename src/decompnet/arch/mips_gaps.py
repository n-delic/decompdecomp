"""Bounded evidence for unanalysed MIPS ROM code; reports never alter targets or prove code."""
from bisect import bisect_right
from collections import Counter

from .mips import JR_RA, is_frame, rabbitizer_category

MAX_STEPS = 4096
MAX_CANDIDATES = 128


def extent(image, start, end, vram, order, cpu="", *, table_ranges=(), mapping_choices=()):
    """Follow direct flow and reviewed bounded table edges; other indirects remain unresolved."""
    import rabbitizer

    from .mips_indirect import jump_table

    pending=[start];seen=set();returns=0;unknown=False;highest=start;indirect=[];tables=[]
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
            table=jump_table(image,start,end,vram,at,order,table_ranges,mapping_choices) if len(tables)<8 else None
            if table:
                tables.append(table)
                if table['state']=='validated':
                    pending.extend(start+t-vram for t in table['targets'])
                    continue
            if len(indirect)<8:indirect.append({'address':vram+at-start,'register':(w>>21)&31})
            unknown=True;continue
        if op==2:
            pc=vram+at-start
            target=start+((((pc+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)
        else:
            delta=w&0xffff;delta=delta-0x10000 if delta&0x8000 else delta
            target=at+4+delta*4
            pending.append(at+8)
        pending.append(target)
    # A case from one resolved table must not enter another dispatch after its
    # bound check. Those indirect edges are absent from the static branch scan.
    validated=[t for t in tables if t['state']=='validated']
    for target_table in validated:
        if any(target_table['guard_address']<dest<=target_table['address']+4
               for source_table in validated for dest in source_table['targets']):
            unknown=True;target_table['state']='unresolved'
            target_table['reason']='a table edge bypasses this guard'
    return {'decoded_instructions':len(seen),'return_sites':returns,'direct_flow_closed':bool(returns and not unknown and not pending and not tables),
            'flow_closed':bool(returns and not unknown and not pending),'jump_tables':tables,
            'extent_bytes':highest-start,'step_limit':bool(pending),'unresolved_transfers':indirect}


def audit(image, segments, functions, *, big_endian=True, cpu="", progress=lambda _:None, data_mappings=()):
    """Frozen image + known code ranges. Even closed direct flow remains review-only."""
    order='big' if big_endian else 'little'
    spans=sorted((s['rom_start'],s['rom_end'],s['vram']) for s in segments)
    if any(not 0<=lo<=hi<=len(image) or lo%4 or hi%4 for lo,hi,_ in spans):
        raise ValueError('Code segments must be word-aligned ranges inside the image.')
    if any(f['rom']<0 or f['rom']%4 or f['size']<0 or f['size']%4 or f['rom']+f['size']>len(image) for f in functions):
        raise ValueError('Function ranges must stay inside the image.')
    if not isinstance(data_mappings,(list,tuple)) or any(not isinstance(s,dict) or not {'rom_start','rom_end','vram'}<=s.keys() or any(type(s[k]) is not int for k in ('rom_start','rom_end','vram')) for s in data_mappings):
        raise ValueError('Data mappings need integer rom_start, rom_end and vram fields.')
    table_ranges=[dict(rom_start=a,rom_end=b,vram=v) for a,b,v in spans]+list(data_mappings)
    if any(not 0<=s['rom_start']<s['rom_end']<=len(image) or s['rom_start']%4 or s['rom_end']%4 for s in table_ranges):
        raise ValueError('Data mappings must be aligned ranges inside the verified image.')
    mappings=Counter(v-s for s,_,v in spans)
    end=0x1000;gaps=[]
    for lo,hi,_ in spans:
        if lo>end:gaps.append((end,lo))
        end=max(end,hi)
    if end<len(image):gaps.append((end,len(image)))
    # Do not infer a mapping from data. Reuse only mappings established by code segments.
    mapping_choices=[m for m,_ in mappings.most_common(8)]
    # Unowned bytes inside code parents include splat's data/rodata children and
    # undisassembled text. They are review regions, never evidence of executable code.
    covered=sorted((f['rom'],f['rom']+f['size']) for f in functions)
    interiors=[]
    for lo,hi,_ in spans:
        pos=lo
        for a,b in covered:
            if b<=pos or a>=hi:continue
            if a>pos:interiors.append((pos,min(a,hi)))
            pos=max(pos,b)
        if pos<hi:interiors.append((pos,hi))
    regions=sorted([(a,b,'raw gap') for a,b in gaps]+[(a,b,'unowned segment interior') for a,b in interiors])
    starts=[a for a,_,_ in regions]
    candidates={};references=0;overflow=False
    def region_at(offset):
        index=bisect_right(starts,offset)-1
        if index>=0 and offset<regions[index][1]:return regions[index]
        return None
    def seed(offset,target,evidence):
        nonlocal overflow
        region=region_at(offset)
        if not region or offset%4:return None
        key=(offset,target)
        if key not in candidates and len(candidates)>=MAX_CANDIDATES:
            overflow=True;return None
        return candidates.setdefault(key,dict(rom_offset=offset,vram=target,gap_start=region[0],gap_end=region[1],
                                              region=region[2],evidence=evidence,incoming=[],pointer_references=[]))
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
                row=seed(target-mapping,target,'incoming direct transfer')
                if row is None:continue
                references+=1
                if len(row['incoming'])<8:row['incoming'].append({'function':fn['name'],'address':pc,'kind':'call' if op==3 else 'branch'})
    # Aligned words outside known function bodies can be callbacks or jump tables.
    # Test an apparent pointer's destination before consuming a candidate slot. A
    # value resembling an address is weaker evidence than an incoming CPU transfer.
    pointer_count=0;pointer_probes=0;pointer_limit=False
    for lo,hi,_ in regions:
        for at in range((lo+3)&~3,hi-3,4):
            value=int.from_bytes(image[at:at+4],order)
            if value%4:continue
            for mapping in mapping_choices:
                offset=value-mapping;region=region_at(offset)
                if not region:continue
                key=(offset,value)
                if key not in candidates:
                    if not is_frame(int.from_bytes(image[offset:offset+4],order)):continue
                    if pointer_probes>=512:
                        pointer_limit=True;continue
                    pointer_probes+=1
                    info=extent(image,offset,min(region[1],offset+0x10000),value,order,cpu)
                    if not info['direct_flow_closed'] or info['decoded_instructions']<4:continue
                row=seed(offset,value,'possible function-pointer reference')
                if row is None:continue
                pointer_count+=1
                if len(row['pointer_references'])<8:row['pointer_references'].append({'rom_offset':at,'value':value})
    # Quiet framed bodies with no incoming transfer are weaker hints; cap them separately.
    hints=0
    for lo,hi,region_name in regions:
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
                                         region=region_name,evidence='frame/return pattern only',incoming=[],pointer_references=[],**info);hints+=1
    rows=[]
    for row in candidates.values():
        info=extent(image,row['rom_offset'],min(row['gap_end'],row['rom_offset']+0x10000),row['vram'],order,cpu,table_ranges=table_ranges,mapping_choices=mapping_choices)
        rows.append({**row,**info,'review_only':True})
    from .mips_indirect import consumers
    unresolved_callbacks=[]
    consumer_limit=consumers(image,functions,rows,order,table_ranges,mapping_choices,unresolved_callbacks)
    rows.sort(key=lambda r:(not bool(r['incoming']),not r['direct_flow_closed'],r['rom_offset'],r['vram']))
    return {'gaps':len(gaps),'gap_bytes':sum(b-a for a,b in gaps),'candidates':rows,'incoming_references':references,
            'unresolved_callbacks':unresolved_callbacks,'callback_scan_limit':consumer_limit,'interior_regions':len(interiors),'pointer_references':pointer_count,'pointer_probe_limit':pointer_limit,
            'candidate_limit':overflow,'mapping_limit':len(mappings)>8,
            'note':'Review evidence only. Valid instructions, calls and returns can occur in data or microcode. No targets, statuses, source or compiler settings changed.'}
