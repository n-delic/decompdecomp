"""Strict framed scalar-MIPS bodies that can seed a fresh disassembly of dropped text."""
from .mips import JR_RA, asm_reason, is_frame
from .mips_gaps import extent

MAX_BYTES = 0x10000


def _closed(info, table_ranges):
    """Direct flow closed; or, with `table_ranges` (where the game's data is mapped), flow closed through jump
    tables each validated there: a switch's cases followed through its own table, as code_references takes them."""
    return info['direct_flow_closed'] or bool(table_ranges) and info['flow_closed'] and bool(info['jump_tables']) and all(
        t['state'] == 'validated' for t in info['jump_tables'])


def called_extent(image, start, end, vram, *, big_endian=True, cpu='', asm_check=None, table_ranges=(),
                  allow_loop_epilogue=False, max_lead=None):
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
    lead=_lead(image,start,end,order,cpu,max_lead=max_lead)
    if lead is None:
        return None
    entry=start;start+=lead  # the frame's own address: the checks below are about the body it opens
    frame=word(start)
    amount=0x10000-(frame&0xffff)
    if amount%8 or amount>0x1000:
        return None
    info=extent(image,entry,end,vram,order,cpu,table_ranges=table_ranges,max_steps=MAX_BYTES//4)
    size=info['extent_bytes']-lead
    if info['step_limit'] or size<16:
        return None
    if not _closed(info,table_ranges):
        if (not allow_loop_epilogue or not info['flow_bounded'] or info['return_sites']
                or not (extra := _loop_epilogue(word, start, start+size, end, amount))):
            return None
        size += extra
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

    # A compiler may anchor its frame in $fp and restore $sp from it at the
    # epilogue. Require an early straight-line setup and a single exact restore;
    # branches may not enter that sequence or bypass the setup.
    fp_setup = [at for at in range(start+4, min(start+20, start+size), 4) if word(at) == 0x03a0f021]
    fp_restore = [at for at in range(start+4, start+size, 4) if word(at) == 0x03c0e821]
    fp_pair = None
    if len(fp_setup) == len(fp_restore) == 1:
        setup, restore = fp_setup[0], fp_restore[0]
        saved = [word(at) & 0xffff for at in range(start+4, setup, 4) if word(at) >> 16 == 0xafbe]
        delay_pop = word(start+size-8) == JR_RA and word(start+size-4) == pop
        before_pop = (word(start+size-12) == pop and word(start+size-8) == JR_RA
                      and word(start+size-4) == 0)
        fp_load = start+size-(12 if delay_pop else 16)
        if (len(saved) == 1 and saved[0] < amount and not saved[0] % 4
                and all(not rabbitizer.Instruction(word(at),0,rabbitizer_category(cpu)).hasDelaySlot()
                        for at in range(start, setup, 4))
                and (delay_pop or before_pop) and restore < fp_load
                and word(fp_load) == 0x8fbe0000 | saved[0]):
            fp_pair = (setup, restore, fp_load)
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
            if not fp_pair or at != fp_pair[1]:return None
        if fp_pair and ((ins.modifiesRt() and ins.rt.value==30) or (ins.modifiesRd() and ins.rd.value==30)):
            if at not in (fp_pair[0], fp_pair[2]):return None
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
    if fp_pair and (returns != 1 or destinations & ({*range(entry+4, fp_pair[0]+4, 4)}
                                                      | {*range(fp_pair[1]+4, start+size, 4)})):
        return None
    if destinations & {*range(entry,start+4,4)} or (pops|protected_returns) & destinations:
        return None  # no loop can allocate the entry frame again or enter a return delay slot
    return size+lead if returns else None


def _loop_epilogue(word, frame, finish, limit, amount):
    """Match the compiler's unused cleanup after a closed worker loop."""
    registers = {*range(16,24), 30, 31}
    saved = set()
    for at in range(frame+4, min(frame+64,finish), 4):
        value = word(at)
        reg, slot = (value>>16)&31, value&0xffff
        if value>>21 == 0x57d and reg in registers and slot < amount and not slot%4:
            saved.add((reg,slot))
    if not any(reg == 31 for reg,_ in saved) or len(saved)<2:
        return None
    restored = set()
    for at in range(finish, min(finish+64,limit-4), 4):
        value = word(at)
        if value == JR_RA and restored == saved and word(at+4) == (0x27bd0000|amount):
            return at+8-finish
        if value>>21 != 0x47d:
            return None
        pair = ((value>>16)&31, value&0xffff)
        if pair not in saved or pair in restored:
            return None
        restored.add(pair)
    return None


LEAD = 5  # instructions the compiler may schedule ahead of the frame


def _lead(image, start, end, order, cpu, *, max_lead=None):
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

    for n in range((LEAD if max_lead is None else min(max_lead, 16))+1):
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
    # This finds where a leaf ends; whether C can make it is decided later. asm_reason has called a body with cache,
    # ll or sc hand-written assembly since decomp-repair 7c5ad94 (22 Nov), which would drop every cache-maintenance
    # leaf from boundary recovery, so those bodies skip the check (their verdict is already "assembly") and the
    # loop below still vets each instruction.
    atomic=any(int.from_bytes(body[at:at+4],order)>>26 in (0x2f,0x30,0x38) for at in range(0,size,4))
    if not atomic and (asm_check(body) if asm_check else asm_reason(body,big_endian,cpu=cpu)):return None
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
