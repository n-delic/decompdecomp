"""Recover an entire quiet binary gap only with a call, known mapping and balanced frame.

Unlike the review-only gap audit, this importer rule accepts only a complete gap
bounded on both sides by already accepted code. It never promotes pointer hints.
"""
from ...arch.mips import JR_RA, is_frame
from ...arch.mips_gaps import extent


def complete(rom, segments):
    spans=sorted(segments);mappings={v-s for s,_,v in spans};gaps=[]
    for (_,end,_),(start,_,_) in zip(spans,spans[1:]):
        if end<start:gaps.append((end,start))
    calls=set()
    for start,end,vram in spans:
        for at in range(start,end-3,4):
            w=int.from_bytes(rom[at:at+4],'big')
            if w>>26==3:calls.add(((vram+at-start+4)&0xf0000000)|((w&0x3ffffff)<<2))
    result=[]
    for start,end in gaps:
        choices=[start+m for m in mappings if start+m in calls]
        if len(choices)!=1 or end-start>0x10000:continue
        vram=choices[0];frame=int.from_bytes(rom[start:start+4],'big')
        if not is_frame(frame):continue
        amount=0x10000-(frame&0xffff)
        if rom[end-8:end]!=JR_RA.to_bytes(4,'big')+(0x27bd0000|amount).to_bytes(4,'big'):continue
        info=extent(rom,start,end,vram,'big')
        if not info['direct_flow_closed'] or info['extent_bytes']!=end-start:continue
        if any(is_frame(int.from_bytes(rom[at:at+4],'big')) for at in range(start+4,end,4)):continue
        if any(int.from_bytes(rom[at:at+4],'big')==JR_RA and
               int.from_bytes(rom[at+4:at+8],'big')!=(0x27bd0000|amount) for at in range(start,end-4,4)):continue
        if any(b<vram+end-start and vram<b+e-s for s,e,b in spans):continue
        result.append((start,end,vram))
    return result
