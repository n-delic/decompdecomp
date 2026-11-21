"""Strict framed scalar-MIPS bodies that can seed a fresh disassembly of dropped text."""
from .mips import JR_RA, asm_reason, is_frame
from .mips_gaps import extent

MAX_BYTES = 0x10000


def _closed(info, table_ranges):
    """Direct flow closed; or, with `table_ranges` (where the game's data is mapped), flow closed through jump
    tables each validated there: a switch's cases followed through its own table, as code_references takes them."""
    return info['direct_flow_closed'] or bool(table_ranges) and info['flow_closed'] and bool(info['jump_tables']) and all(
        t['state'] == 'validated' for t in info['jump_tables'])


def called_extent(image, start, end, vram, *, big_endian=True, cpu='', asm_check=None, table_ranges=()):
    """Reject incomplete, unbalanced and multi-frame bodies; this never grants a match.

    A call into raw disassembler output can locate code, but a frame pattern alone is
    not enough. Keep this smaller than the review audit: no tail jumps, interior entry
    points or inferred callback destinations are promoted here, and a switch only with
    `table_ranges`, its table validated where the game's data is mapped.
    """
    order='big' if big_endian else 'little'
    end=min(end,start+MAX_BYTES)
    if start<0 or start%4 or end>len(image) or start+8>end:
        return None
    word=lambda at:int.from_bytes(image[at:at+4],order)
    lead=_lead(image,start,end,order,cpu)
    if lead is None:
        return None
    entry=start;start+=lead  # the frame's own address: the checks below are about the body it opens
    frame=word(start)
    amount=0x10000-(frame&0xffff)
    if amount%8 or amount>0x1000:
        return None
    info=extent(image,entry,end,vram,order,cpu,table_ranges=table_ranges,max_steps=MAX_BYTES//4)
    size=info['extent_bytes']-lead
    if not _closed(info,table_ranges) or info['step_limit'] or size<16:
        return None
    body=image[entry:start+size]
    if (asm_check(body) if asm_check else asm_reason(body,big_endian,cpu=cpu)):
        return None
    vram+=lead
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
    if destinations & {*range(entry,start+4,4)} or (pops|protected_returns) & destinations:
        return None  # no loop can allocate the entry frame again or enter a return delay slot
    return size+lead if returns else None


LEAD = 4  # instructions the compiler may schedule ahead of the frame


def _lead(image, start, end, order, cpu):
    """How many bytes come before the frame: GCC can schedule a load or two ahead of `addiu $sp` (an N64 game's
    overlay: `lui $v0; lbu $v0; addiu $sp, -0x20`), and those stay part of the function. Only plain loads and
    arithmetic into scratch registers or a full run of argument home stores qualify. Transfers and other
    accesses to $sp, $ra, $fp or saved registers stay outside this rule."""
    import rabbitizer

    from .mips import _mips_uses, rabbitizer_category

    # o32 variadic entries spill the remaining incoming argument registers
    # into their caller-provided home slots before allocating their own frame.
    word = int.from_bytes(image[start:start+4], order)
    first_arg = (word >> 16) & 31
    homes = (8-first_arg)*4
    if (4 <= first_arg <= 6 and start+homes+4 <= end
            and all(int.from_bytes(image[at:at+4], order) == (0xafa00000 | arg << 16 | (arg-4)*4)
                    for arg, at in zip(range(first_arg,8), range(start,start+homes,4)))
            and is_frame(int.from_bytes(image[start+homes:start+homes+4], order))):
        return homes

    for n in range(LEAD+1):
        at=start+4*n
        if at+4>end:return None
        w=int.from_bytes(image[at:at+4],order)
        if is_frame(w):return 4*n
        ins=rabbitizer.Instruction(w,0,rabbitizer_category(cpu))
        uses=_mips_uses(w,cpu)
        if (not ins.isValid() or ins.isBranch() or ins.isJump() or ins.hasDelaySlot() or ins.doesStore() or uses is None
                or (uses[0]|uses[1])&{16,17,18,19,20,21,22,23,29,30,31}):
            return None
    return None


def leaf_extent(image,start,end,vram,*,big_endian=True,cpu='',asm_check=None,table_ranges=()):
    """A closed frameless leaf: no calls, stack writes or callee-saved clobbers.

    Callers must independently establish its entry from a call, callback or known
    return boundary, never scan arbitrary data for a pattern. Indirect flow stays review-only, but for a switch
    whose table `table_ranges` validates.
    """
    import rabbitizer

    from .mips import _mips_uses, rabbitizer_category

    end=min(end,start+MAX_BYTES)
    if start<0 or start%4 or start+8>end or end>len(image):return None
    order='big' if big_endian else 'little'
    info=extent(image,start,end,vram,order,cpu,table_ranges=table_ranges,max_steps=MAX_BYTES//4)
    size=info['extent_bytes']
    if not _closed(info,table_ranges) or info['step_limit'] or size<8:return None
    body=image[start:start+size]
    if (asm_check(body) if asm_check else asm_reason(body,big_endian,cpu=cpu)):return None
    preserved={*range(16,24),29,30,31}
    for at in range(start,start+size,4):
        word=int.from_bytes(image[at:at+4],order)
        ins=rabbitizer.Instruction(word,0,rabbitizer_category(cpu))
        uses=_mips_uses(word,cpu)
        if ins.isValid() and ins.getOpcodeName() == 'cache':
            # CACHE reads its address base and clobbers no general registers.
            # This establishes a leaf's extent without granting a C match.
            continue
        if not ins.isValid() or ins.doesLink() or uses is None or uses[1]&preserved:return None
        if any(getattr(ins,'modifies'+field)() and getattr(ins,field.lower()).value>=20
               for field in ('Fd','Fs','Ft')):return None
    return size
