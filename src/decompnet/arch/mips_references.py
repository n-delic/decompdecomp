"""Direct edges and locally materialized addresses in frozen scalar-MIPS code."""
from .mips import _mips_uses


def references(image, start, end, vram, *, big_endian=True):
    """Yield target, instruction offset and evidence kind; never propagate through flow joins."""
    order='big' if big_endian else 'little';uppers={};delay=False;joins=set()
    for at in range(start,end-3,4):
        w=int.from_bytes(image[at:at+4],order);op=w>>26;pc=vram+at-start
        if op in (1,4,5,6,7,20,21,22,23) or op==17 and (w>>21)&31==8:
            joins.add(at+4+(((w&0xffff)^0x8000)-0x8000)*4)
        elif op in (2,3):
            target=((pc+4)&0xf0000000)|((w&0x3ffffff)<<2)
            joins.add(start+target-vram)
    for at in range(start,end-3,4):
        w=int.from_bytes(image[at:at+4],order);op=w>>26
        rs=(w>>21)&31;rt=(w>>16)&31;pc=vram+at-start
        if at in joins:uppers.clear()
        upper=uppers.get(rs);uses=_mips_uses(w,'')
        if uses is None:uppers.clear()
        else:
            for r in uses[1]:uppers.pop(r,None)
        if op==15 and rt:uppers[rt]=((w&0xffff)<<16,at)
        elif op in (9,13) and upper and at-upper[1]<=32:
            imm=((w&0xffff)^0x8000)-0x8000
            target=(upper[0]+imm if op==9 else upper[0]|(w&0xffff))&0xffffffff
            yield target,at,'address'
        if op in (2,3):yield ((pc+4)&0xf0000000)|((w&0x3ffffff)<<2),at,'call' if op==3 else 'branch'
        elif op in (1,4,5,6,7,20,21,22,23) or op==17 and rs==8:
            imm=((w&0xffff)^0x8000)-0x8000
            yield pc+4+imm*4,at,'branch'
        if delay:uppers.clear();delay=False
        if op in (1,2,3,4,5,6,7,20,21,22,23) or op==17 and rs==8 or op==0 and w&63 in (8,9):delay=True
