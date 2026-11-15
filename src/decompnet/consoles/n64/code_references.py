"""Recover an entire quiet binary gap only with a reference, known mapping and balanced frame.

Unlike the review-only gap audit, this importer rule accepts only a complete gap
bounded on both sides by already accepted code. The reference is a call (jal) to
its start, or the start's address stored as a word in the game's data: functions
the game only calls through a table of pointers (a racing game's command handlers,
3.5 and 8 KB each) have no jal anywhere. A stored address is weaker evidence than a
call, so it counts only under the mapping the code on both sides of the gap shares.
"""
import struct

from ...arch.mips import JR_RA, is_frame
from ...arch.mips_gaps import extent


def _stored(rom, wanted, spans):
    """The addresses in `wanted` that the image holds as an aligned big-endian word outside the code."""
    if not wanted:return set()
    code=sorted((s,e) for s,e,_ in spans);found=set();i=0
    for n,(w,) in enumerate(struct.iter_unpack('>I',rom[:len(rom)//4*4])):
        if w not in wanted:continue
        at=n*4
        while i<len(code) and code[i][1]<=at:i+=1
        if i<len(code) and code[i][0]<=at:continue  # an instruction that happens to equal the address
        found.add(w)
    return found


def complete(rom, segments, declared=()):
    """(rom_start, rom_end, vram) of the gaps between `segments` (rom_start, rom_end, vram) that are one whole
    function. `declared`: segments as splat's config maps them, data included, where a switch's jump table may
    be read to follow its cases."""
    spans=sorted(segments);mappings={v-s for s,_,v in spans};gaps=[]
    for (s0,end,v0),(start,_,v1) in zip(spans,spans[1:]):
        if end<start:gaps.append((end,start,v0-s0 if v0-s0==v1-start else None))
    calls=set()
    for start,end,vram in spans:
        for at in range(start,end-3,4):
            w=int.from_bytes(rom[at:at+4],'big')
            if w>>26==3:calls.add(((vram+at-start+4)&0xf0000000)|((w&0x3ffffff)<<2))
    pointers=_stored(rom,{start+m for start,end,m in gaps if m is not None and end-start<=0x10000},spans)
    tables=[dict(rom_start=s,rom_end=e,vram=v) for s,e,v in list(spans)+list(declared)]
    result=[]
    for start,end,shared in gaps:
        if end-start>0x10000:continue
        choices=[start+m for m in mappings if start+m in calls]
        if not choices and shared is not None and start+shared in pointers:
            choices=[start+shared]
        if len(choices)!=1:continue
        vram=choices[0];frame=int.from_bytes(rom[start:start+4],'big')
        if not is_frame(frame):continue
        amount=0x10000-(frame&0xffff)
        if rom[end-8:end]!=JR_RA.to_bytes(4,'big')+(0x27bd0000|amount).to_bytes(4,'big'):continue
        info=extent(rom,start,end,vram,'big',table_ranges=tables)
        # A switch's cases are followed only through a table at an address the config maps.
        closed=info['direct_flow_closed'] or info['flow_closed'] and all(
            t['state']=='validated' for t in info['jump_tables'])
        if not closed or info['extent_bytes']!=end-start:continue
        if any(is_frame(int.from_bytes(rom[at:at+4],'big')) for at in range(start+4,end,4)):continue
        if any(int.from_bytes(rom[at:at+4],'big')==JR_RA and
               int.from_bytes(rom[at+4:at+8],'big')!=(0x27bd0000|amount) for at in range(start,end-4,4)):continue
        if any(b<vram+end-start and vram<b+e-s for s,e,b in spans):continue
        result.append((start,end,vram))
    return result
