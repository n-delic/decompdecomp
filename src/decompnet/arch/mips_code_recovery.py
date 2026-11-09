"""Strict framed scalar-MIPS bodies that can seed a fresh disassembly of dropped text."""
from .mips import JR_RA, asm_reason, is_frame
from .mips_gaps import extent

MAX_BYTES = 0x10000


def called_extent(image, start, end, vram, *, big_endian=True, cpu='', asm_check=None):
    """Reject incomplete, unbalanced and multi-frame bodies; this never grants a match.

    A call into raw disassembler output can locate code, but a frame pattern alone is
    not enough. Keep this smaller than the review audit: no switches, tail jumps,
    interior entry points or inferred callback destinations are promoted here.
    """
    order='big' if big_endian else 'little'
    end=min(end,start+MAX_BYTES)
    if start<0 or start%4 or end>len(image) or start+8>end:
        return None
    word=lambda at:int.from_bytes(image[at:at+4],order)
    frame=word(start)
    if not is_frame(frame):
        return None
    amount=0x10000-(frame&0xffff)
    if amount%8 or amount>0x1000:
        return None
    info=extent(image,start,end,vram,order,cpu,max_steps=MAX_BYTES//4)
    size=info['extent_bytes']
    if not info['direct_flow_closed'] or info['step_limit'] or size<16:
        return None
    body=image[start:start+size]
    if (asm_check(body) if asm_check else asm_reason(body,big_endian,cpu=cpu)):
        return None
    # One frame, popped in the return delay slot or immediately before the
    # return. Pre-return pops cannot be delay slots or have edges bypassing them.
    pop=0x27BD0000|amount
    returns=0
    destinations=set()
    pops=set();protected_returns=set()
    import rabbitizer

    from .mips import rabbitizer_category

    for at in range(start,start+size,4):
        w=word(at)
        if not rabbitizer.Instruction(w,0,rabbitizer_category(cpu)).isValid():
            return None
        if at>start and is_frame(w):
            return None
        # Other stack writes need path-sensitive accounting. A pre-return pop
        # must fall straight into JR, never execute in another transfer's delay slot.
        ins=rabbitizer.Instruction(w,0,rabbitizer_category(cpu))
        before_return=(at+4<start+size and word(at+4)==JR_RA and at>=start+4
                       and not rabbitizer.Instruction(word(at-4),0,rabbitizer_category(cpu)).hasDelaySlot())
        valid_pop=w==pop and (at>=start+4 and word(at-4)==JR_RA or before_return)
        if ins.modifiesRt() and ins.rt.value==29 and not valid_pop:
            if at!=start:return None
        if ins.modifiesRs() and ins.rs.value==29:
            return None
        if ins.modifiesRd() and ins.rd.value==29:
            return None
        op=w>>26
        if op==2:
            destinations.add((((vram+at-start+4)&0xF0000000)|((w&0x3FFFFFF)<<2))-vram+start)
        elif op in (1,4,5,6,7,20,21,22,23) or op==17 and (w>>21)&31==8:
            delta=w&0xFFFF
            destinations.add(at+4+(delta-0x10000 if delta&0x8000 else delta)*4)
        if w==JR_RA:
            if at+8>start+size:return None
            if word(at+4)==pop:
                if at>start and word(at-4)==pop:return None
                pops.add(at+4)
            elif at>start and word(at-4)==pop:
                protected_returns.update((at,at+4))
            else:return None
            returns+=1
    if start in destinations or (pops|protected_returns) & destinations:
        return None  # no loop can allocate the entry frame again or enter a return delay slot
    return size if returns else None


def leaf_extent(image,start,end,vram,*,big_endian=True,cpu='',asm_check=None):
    """A closed frameless leaf: no calls, stack writes or callee-saved clobbers.

    Callers must independently establish its entry from a call, callback or known
    return boundary, never scan arbitrary data for a pattern. Indirect flow stays review-only.
    """
    import rabbitizer

    from .mips import _mips_uses, rabbitizer_category

    end=min(end,start+MAX_BYTES)
    if start<0 or start%4 or start+8>end or end>len(image):return None
    order='big' if big_endian else 'little'
    info=extent(image,start,end,vram,order,cpu,max_steps=MAX_BYTES//4)
    size=info['extent_bytes']
    if not info['direct_flow_closed'] or info['step_limit'] or size<8:return None
    body=image[start:start+size]
    if (asm_check(body) if asm_check else asm_reason(body,big_endian,cpu=cpu)):return None
    preserved={*range(16,24),29,30,31}
    for at in range(start,start+size,4):
        word=int.from_bytes(image[at:at+4],order)
        ins=rabbitizer.Instruction(word,0,rabbitizer_category(cpu))
        uses=_mips_uses(word,cpu)
        if not ins.isValid() or ins.doesLink() or uses is None or uses[1]&preserved:return None
        if any(getattr(ins,'modifies'+field)() and getattr(ins,field.lower()).value>=20
               for field in ('Fd','Fs','Ft')):return None
    return size
