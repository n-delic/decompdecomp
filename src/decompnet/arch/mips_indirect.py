"""Bounded MIPS indirect-transfer evidence. No guessed mapping becomes an executable edge."""

import struct
from bisect import bisect_left, bisect_right
from collections import defaultdict

MAX_ENTRIES=256
MAX_CALLS=512
MAX_WORDS=500000


def signed(w):return (w&0xffff)-0x10000 if w&0x8000 else w&0xffff


def locations(address,size,image,ranges,mappings=()):
    """Declared ranges first; established-code deltas are explicitly weaker hypotheses."""
    hits={s['rom_start']+address-s['vram'] for s in ranges
          if s['vram']<=address and address+size<=s['vram']+s['rom_end']-s['rom_start']}
    if any(not 0<=off<=len(image)-size for off in hits):return [],'mapping lies outside the image'
    if len(hits)>1:return [],'conflicting mappings'
    if hits:return [(hits.pop(),True)],None
    return [(address-m,False) for m in mappings if 0<=address-m<=len(image)-size],None


def _guard(image,at,order,scheduled):
    """The guard/scale/LUI/add/load/JR words before the JR at `at`, as (guard's address, bound, branch, delay,
    scale, hi, add, load, jump, slot), or None. `scheduled`: the scale sits in the guard branch's delay slot, as
    an N64 racing game's GCC scheduled it (sltiu; beqz; sll in its slot; lui; addu; lw; jr), one word shorter."""
    if scheduled:
        bound,branch,scale,hi,add,load,jump,slot=[int.from_bytes(image[p:p+4],order) for p in range(at-24,at+8,4)]
        return at-24,bound,branch,0,scale,hi,add,load,jump,slot
    bound,branch,delay,scale,hi,add,load,jump,slot=[int.from_bytes(image[p:p+4],order) for p in range(at-28,at+8,4)]
    return at-28,bound,branch,delay,scale,hi,add,load,jump,slot


def _saved_base(image,start,guard,order,register):
    """Resolve a callee-saved base initialized before the first transfer."""
    import rabbitizer

    value = None
    transferred = False
    paired_low = -1
    for at in range(start,guard,4):
        if at==paired_low:
            continue
        word = int.from_bytes(image[at:at+4],order)
        ins = rabbitizer.Instruction(word)
        if not ins.isValid():
            return None
        if ins.hasDelaySlot():
            transferred = True
        if not ((ins.modifiesRt() and (word>>16)&31==register)
                or (ins.modifiesRd() and (word>>11)&31==register)):
            continue
        if transferred:
            return None
        if word>>16 == 0x3c00|register and at+8<=guard:
            low = int.from_bytes(image[at+4:at+8],order)
            if low>>16 != 0x2400|register<<5|register:
                return None
            value = ((word&0xffff)<<16)+signed(low)
            paired_low = at+4
        else:
            return None
    return value


def _straight_return(image, at, end, order):
    """Follow a short straight-line epilogue through its return delay slot."""
    import rabbitizer

    for pc in range(at, min(end - 4, at + 96), 4):
        word = int.from_bytes(image[pc:pc + 4], order)
        ins = rabbitizer.Instruction(word)
        if not ins.isValid() or ins.isTrap() or word == 0x42000018:  # eret
            return False
        if word == 0x03e00008:  # jr $ra
            slot = rabbitizer.Instruction(int.from_bytes(image[pc + 4:pc + 8], order))
            return slot.isValid() and not slot.hasDelaySlot() and not slot.isTrap()
        if ins.hasDelaySlot():
            return False
    return False


def _saved_base_after_early_returns(image, start, end, vram, guard, jump, order, register):
    """A saved base may follow branches that either rejoin before it or return."""
    import rabbitizer

    pairs = [pc for pc in range(start, guard - 4, 4)
             if int.from_bytes(image[pc:pc + 4], order) >> 16 == 0x3c00 | register]
    if len(pairs) != 1:
        return None
    pair = pairs[0]
    if pair > start and rabbitizer.Instruction(int.from_bytes(image[pair - 4:pair], order)).hasDelaySlot():
        return None
    value = _saved_base(image, pair, guard, order, register)
    if value is None:
        return None
    in_delay_slot = False
    for pc in range(start, end, 4):
        word = int.from_bytes(image[pc:pc + 4], order)
        ins = rabbitizer.Instruction(word)
        if not ins.isValid() or word == 0x42000018 or in_delay_slot and ins.hasDelaySlot():
            return None
        if pc >= guard and ((ins.modifiesRt() and (word >> 16) & 31 == register)
                            or (ins.modifiesRd() and (word >> 11) & 31 == register)):
            if pc <= jump + 4 or in_delay_slot or not _straight_return(image, pc, end, order):
                return None
        in_delay_slot = ins.hasDelaySlot()
        if not in_delay_slot:
            continue
        op = word >> 26
        if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or (op == 17 and (word >> 21) & 31 == 8):
            target = pc + 4 + signed(word) * 4
        elif op in (2, 3):
            address = (vram + pc - start + 4) & 0xf0000000 | (word & 0x3ffffff) << 2
            target = start + address - vram
            if op == 3 and not start <= target < end:
                continue  # an ordinary outside call preserves its saved registers
        elif word == 0x03e00008 or pc == jump:
            continue
        else:
            return None
        if target == pair + 4:
            return None
        if pc < pair and not (start <= target <= pair
                              or jump + 4 < target < end and _straight_return(image, target, end, order)):
            return None
    return value


def _saved_register_guard(image,start,end,vram,at,order):
    if at-start<20:
        return None
    guard = at-20
    bound,branch,scale,add,load,jump,slot = (
        int.from_bytes(image[p:p+4],order) for p in range(guard,at+8,4))
    index,condition,count = (bound>>21)&31,(bound>>16)&31,bound&0xffff
    scaled = (scale>>11)&31
    left,right = (add>>21)&31,(add>>16)&31
    base = right if left==scaled else left
    if (bound>>26!=11 or not 0<count<=MAX_ENTRIES or not index or not condition or condition==index
            or branch>>16!=0x1000|condition<<5
            or scale!=index<<16|scaled<<11|2<<6
            or not scaled or base not in range(16,24) or scaled==base or scaled not in (left,right)
            or condition==base or add&0xfc00ffff!=scaled<<11|33
            or load!=0x8c000000|scaled<<21|scaled<<16 or jump!=scaled<<21|8 or slot):
        return None
    address = _saved_base(image,start,guard,order,base)
    early_returns = address is None
    if early_returns:
        address = _saved_base_after_early_returns(image, start, end, vram, guard, at, order, base)
    if address is not None:
        return guard, branch, count, address & 0xffffffff, early_returns


def _ido_register_guard(image,start,at,order):
    """IDO materializes the complete table address before scaling its index."""
    for scheduled in (False, True):
        guard = at-(28 if scheduled else 32)
        if guard<start:
            continue
        words = [int.from_bytes(image[p:p+4],order) for p in range(guard,at+8,4)]
        if not scheduled:
            delay = words.pop(2)
            move = delay&0xfc1f07ff in (0x21,0x25)
            if delay and delay>>21!=0x57d and not move:  # sw to the current stack frame
                continue
        bound,branch,hi,lo,scale,add,load,jump,slot = words
        index, condition, count = (bound>>21)&31, (bound>>16)&31, bound&0xffff
        high, base, scaled = (hi>>16)&31, (lo>>16)&31, (scale>>11)&31
        destination = (load>>16)&31
        if (bound>>26!=11 or not 1<=count<=MAX_ENTRIES or not index or not condition or condition==index
                or branch>>16!=0x1000|condition<<5 or not high or not base or index in (high,base)
                or hi>>16!=0x3c00|high or lo>>16!=0x2400|high<<5|base
                or not scaled or scaled==base or scale!=index<<16|scaled<<11|2<<6
                or add&0xfc00ffff!=scaled<<11|33 or {(add>>21)&31,(add>>16)&31}!={base,scaled}
                or not destination or load!=0x8c000000|scaled<<21|destination<<16
                or jump!=destination<<21|8 or slot):
            continue
        if not scheduled and move and (delay>>11)&31==index:
            continue
        return guard,branch,count,(((hi&0xffff)<<16)+signed(lo))&0xffffffff
    return None


def _guard_slot(word, index, condition):
    # Unrelated integer and FP operations can fill an IDO scheduler slot.
    return (not word or word >> 21 == 0x57d
            or word & 0xfc1f07ff in (33, 37) and (word >> 11) & 31 not in (index, condition)
            or word >> 26 in (9, 10, 11, 12, 13, 14) and (word >> 16) & 31 not in (index, condition)
            # A scheduled load may fill the same gap if it preserves both
            # registers used by the bounds check and table index.
            or word >> 26 == 35 and (word >> 16) & 31 not in (index, condition)
            # FP multiply and move cannot change either general-purpose guard register.
            or word & 0xffe0003f in (0x46000002, 0x46000006))


def _prescaled_guard(image, start, end, at, order, vram):
    """A prior branch always scales the index in its delay slot before the guard."""
    if at-start < 40:
        return None
    early, scale, const, fp, nop, bound, branch, hi, add, load, jump, slot = [
        int.from_bytes(image[p:p+4], order) for p in range(at-40, at+8, 4)]
    guard = at-20
    index, condition, count = (bound >> 21) & 31, (bound >> 16) & 31, bound & 0xffff
    scaled, base, dest = (scale >> 11) & 31, (hi >> 16) & 31, (jump >> 21) & 31
    temp = (const >> 16) & 31
    if (early >> 26 not in (4, 5) or at-36+signed(early)*4 != guard
            or scale >> 26 or scale & 63 or (scale >> 6) & 31 != 2
            or (scale >> 16) & 31 != index or (scale >> 21) & 31
            or const >> 26 != 15 or temp in (0, index, scaled)
            or fp & 0xffe007ff != 0x44800000 or (fp >> 16) & 31 != temp or nop
            or bound >> 26 != 11 or not 0 < count <= MAX_ENTRIES or not index or not condition
            or not scaled or scaled == index or condition in (index, scaled) or branch >> 26 != 4
            or {(branch >> 21) & 31, (branch >> 16) & 31} != {0, condition}
            or hi >> 26 != 15 or not base or base in (index, scaled)
            or add >> 26 or add & 63 != 33 or (add >> 11) & 31 != base
            or {(add >> 21) & 31, (add >> 16) & 31} != {base, scaled}
            or load >> 26 != 35 or (load >> 21) & 31 != base or (load >> 16) & 31 != dest
            or jump & 0xfc1fffff != 8 or not dest or slot):
        return None
    # No local edge may enter after the scale or bypass it on the way to the guard.
    for pc in range(start, end-3, 4):
        if pc == at-40:
            continue
        word = int.from_bytes(image[pc:pc+4], order)
        op = word >> 26
        if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
            target = pc+4+signed(word)*4
        elif op in (2, 3):
            target = start+(((((vram+pc-start+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)))-vram)
        else:
            continue
        if at-40 < target <= guard:
            return None
    return guard, branch, count, (((hi & 0xffff) << 16)+signed(load)) & 0xffffffff


def _prechecked_guard(image, start, end, at, order, vram):
    """A separate guarded exit precedes the ordinary bounds check."""
    if at-start < 36:
        return None
    bound, mask, early, early_slot, branch, scale, hi, add, load, jump, slot = [
        int.from_bytes(image[p:p+4], order) for p in range(at-36, at+8, 4)]
    index, condition, count = (bound >> 21) & 31, (bound >> 16) & 31, bound & 0xffff
    masked = (mask >> 16) & 31
    scaled, base, dest = (scale >> 11) & 31, (hi >> 16) & 31, (jump >> 21) & 31
    guard = at-24  # immediately before the bounds branch
    if (bound >> 26 != 11 or not 0 < count <= MAX_ENTRIES or not index or not condition
            or condition == index or mask >> 26 != 12 or not masked or masked in (index, condition)
            or early >> 26 != 4 or {(early >> 21) & 31, (early >> 16) & 31} != {0, masked}
            or early_slot or branch >> 26 != 4
            or {(branch >> 21) & 31, (branch >> 16) & 31} != {0, condition}
            or at-24+signed(early)*4 != at-16+signed(branch)*4
            or scale >> 26 or scale & 63 or (scale >> 6) & 31 != 2
            or (scale >> 16) & 31 != index or (scale >> 21) & 31
            or not scaled or scaled == condition or hi >> 26 != 15
            or not base or base in (index, scaled)
            or add >> 26 or add & 63 != 33 or (add >> 11) & 31 != base
            or {(add >> 21) & 31, (add >> 16) & 31} != {base, scaled}
            or load >> 26 != 35 or (load >> 21) & 31 != base or (load >> 16) & 31 != dest
            or jump & 0xfc1fffff != 8 or not dest or slot):
        return None
    for pc in range(start, end-3, 4):
        word = int.from_bytes(image[pc:pc+4], order)
        op = word >> 26
        if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
            target = pc+4+signed(word)*4
        elif op in (2, 3):
            target = start+(((((vram+pc-start+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)))-vram)
        else:
            continue
        if at-36 < target <= guard:
            return None
    return guard, branch, count, (((hi & 0xffff) << 16)+signed(load)) & 0xffffffff


def jump_table(image,start,end,vram,at,order,ranges=(),mappings=(),*,reader=None, allow_saved_base=False):
    """Recognize one exact guard/scale/LUI/add/load/JR idiom (its scale in the guard's delay slot or after it),
    with no interior entry. Import data classification may also follow an early saved-register base."""
    if at+8>end:return None
    uses_ido_guard = False
    stack_guard = False
    early_returns = False
    for scheduled in (False,True):
        if at-start<(24 if scheduled else 28):continue
        guard,bound,branch,delay,scale,hi,add,load,jump,slot=_guard(image,at,order,scheduled)
        branch_pc = guard+4
        if scheduled and guard-start >= 12:
            fp, candidate, clear_fp, step = struct.unpack_from(
                '>4I' if order == 'big' else '<4I', image, guard-12)
            index_reg, guard_reg = (candidate>>21)&31, (candidate>>16)&31
            # The FP branch skips two instructions that leave the switch's
            # bound and index intact. The bound runs in its normal delay slot.
            if (fp in (0x45000003, 0x45010003) and candidate>>26 == 11
                    and clear_fp & 0xffff07ff == 0x44800000 and step>>26 == 9
                    and (step>>21)&31 == (step>>16)&31
                    and (step>>16)&31 not in (index_reg, guard_reg)
                    and bound == step):
                guard -= 8
                bound = candidate
        if bound >> 26 != 11:
            previous = max(start, guard - 12)
            words = [int.from_bytes(image[p:p+4], order) for p in range(previous, guard, 4)] + [bound]
            for offset in range(len(words) - 2, -1, -1):
                prior = words[offset]
                if prior >> 26 == 11 and all(_guard_slot(word, (prior >> 21) & 31, (prior >> 16) & 31)
                                             for word in words[offset + 1:]):
                    guard, bound = previous + offset * 4, prior
                    break
        index=(bound>>21)&31;condition=(bound>>16)&31;count=bound&0xffff
        dest=(jump>>21)&31;base=(hi>>16)&31;scaled=(scale>>11)&31
        if bound>>26!=11 or not 0<count<=MAX_ENTRIES or not index or not condition or condition==index:continue
        if branch>>26 not in (4,20) or {(branch>>21)&31,(branch>>16)&31}!={0,condition}:continue
        if not _guard_slot(delay, index, condition):continue
        if scale>>26 or scale&63 or (scale>>6)&31!=2 or (scale>>16)&31!=index or (scale>>21)&31:continue
        if hi>>26!=15 or not base or not scaled or base==scaled:continue
        if add>>26 or add&63!=33 or (add>>11)&31!=base or {(add>>21)&31,(add>>16)&31}!={base,scaled}:continue
        if load>>26!=35 or (load>>21)&31!=base or (load>>16)&31!=dest or jump&0xfc1fffff!=8 or not dest or slot:continue
        address=(((hi&0xffff)<<16)+signed(load))&0xffffffff
        break
    else:
        prescaled = (_prechecked_guard(image, start, end, at, order, vram)
                     or _prescaled_guard(image, start, end, at, order, vram))
        saved = _stack_index_guard(image, start, at, order) if prescaled is None else None
        if prescaled is not None:
            guard, branch, count, address = prescaled
            saved = prescaled
            direct = prescaled
        if saved is not None:
            if prescaled is None:
                stack_guard = True
                guard, branch, count, address = saved
        else:
            direct = _address_pair_guard(image, start, at, order)
            if direct is not None:
                guard, branch, count, address = direct
                saved = direct
            else:
                saved = _saved_register_guard(image,start,end,vram,at,order) if allow_saved_base else None
        if saved is not None and not stack_guard and direct is None:
            guard, branch, count, address, early_returns = saved
        elif saved is None:
            saved = _ido_register_guard(image,start,at,order)
            if saved is None:return None
            uses_ido_guard = True
            guard, branch, count, address = saved
        branch_pc = guard+4
    default=branch_pc+4+signed(branch)*4
    if not start<=default<end or guard<default<=at+4:return None
    result={'guard_address':vram+guard-start,'address':vram+at-start,'table_address':address,'entry_count':count,'state':'unresolved'}
    for p in range(start,end-3,4):
        w=int.from_bytes(image[p:p+4],order);op=w>>26
        if op in (1,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8):target=p+4+signed(w)*4
        elif op in (2,3):target=start+((((vram+p-start+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)
        else:continue
        # A normal FP branch can compute the bound in its delay slot,
        # then jump directly to the guard branch. Both paths run the bound.
        bound_in_slot = (p == guard-4 and target == branch_pc
                         and w >> 16 in (0x4500, 0x4501))
        if (guard<target<=at or stack_guard and target == guard) and not guard<=p<=at and not bound_in_slot:
            return {**result,'reason':'an incoming edge bypasses the guard'}
    def destinations(data, prefix=0):
        targets=[prefix|int.from_bytes(data[p:p+4],order) for p in range(0,len(data),4)]
        if early_returns and any(t <= vram + at - start + 4 for t in targets):
            return None
        if all(t%4==0 and vram<=t<vram+end-start and not vram+guard-start<t<=vram+at-start+4 for t in targets):
            return targets
        return None

    # The ROM judge already has an overlay-aware reader for the current
    # function. Its read is independent of the coverage scanner's mappings.
    if reader is not None:
        data = reader(address,count*4)
        targets = destinations(data) if data is not None and len(data)==count*4 else None
        return {**result,'state':'validated','targets':targets} if targets else result

    ordinary = [r for r in ranges if 'target_prefix' not in r]
    choices,error=locations(address,count*4,image,ordinary,mappings)
    if error:return {**result,'reason':error}
    valid=[]
    for offset,verified in choices:
        if targets := destinations(image[offset:offset+count*4]):
            valid.append((offset,verified,targets))
    if not valid:
        scoped = [r for r in ranges if r.get('scoped_copy') and r.get('target_prefix')==vram&0xe0000000
                  and r.get('code_delta')==vram-start and r['vram'] <= address
                  and address+count*4 <= r['vram']+r['rom_end']-r['rom_start']]
        # A verified overlay replaces the initial boot copy at the same address. Only the record belonging to
        # this code mapping may decide its table; sibling overlays and the older boot contents are not concurrent.
        aliases = scoped or [r for r in ranges if r.get('target_prefix')==vram&0xe0000000
                             and (r.get('boot_copy') or uses_ido_guard and r.get('code_delta')==vram-start)]
        choices,error = locations(address,count*4,image,aliases)
        if error:return {**result,'reason':error}
        resident = [r for r in aliases if (r.get('boot_copy') or r.get('scoped_copy'))
                    and r['vram']-r['rom_start']==vram-start]
        absolute, _ = locations(address, count*4, image, resident)
        for offset,verified in choices:
            data = image[offset:offset+count*4]
            # The initial IPL3 copy can also contain absolute KSEG0 table
            # entries. Require the caller to be in that same ROM mapping.
            if (offset,verified) in absolute and (targets := destinations(data)):
                valid.append((offset,verified,targets))
            elif all(word<0x20000000 for word, in struct.iter_unpack('>I' if order=='big' else '<I',data)):
                if targets := destinations(data,vram&0xe0000000):
                    valid.append((offset,verified,targets))
    if len(valid)!=1:return {**result,'reason':'no unique mapped table with all targets inside the candidate'}
    offset,verified,targets=valid[0]
    return {**result,'rom_offset':offset,'mapping_verified':verified,'targets':targets,
            'state':'validated' if verified else 'mapping_hypothesis',
            'reason':'bounded table; follow every case' if verified else 'review the raw-data mapping before following cases'}


def _stack_index_guard(image, start, at, order):
    """A bounded switch reloads the same frame slot after its branch, then builds the table address."""
    if at-start < 44:
        return None
    words = [int.from_bytes(image[p:p+4], order) for p in range(at-44, at+8, 4)]
    first, bound, branch, delay, reload, move, scale, hi, low, add, load, jump, slot = words
    index = (first >> 16) & 31
    cond = (bound >> 16) & 31
    copied = (move >> 11) & 31
    scaled = (scale >> 11) & 31
    base = (hi >> 16) & 31
    dest = (jump >> 21) & 31
    count = bound & 0xffff
    if (first >> 21 != 0x47e or not index or reload != first
            or bound >> 26 != 11 or (bound >> 21) & 31 != index or not cond or cond == index
            or not 0 < count <= MAX_ENTRIES
            or branch >> 26 != 4 or (branch >> 21) & 31 != cond or (branch >> 16) & 31
            or delay or slot or move >> 26 or move & 63 != 33 or (move >> 21) & 31 != index
            or (move >> 16) & 31 or not copied or scale >> 26 or scale & 63
            or (scale >> 6) & 31 != 2 or (scale >> 16) & 31 != copied or (scale >> 21) & 31
            or not scaled or hi >> 26 != 15 or not base or base == scaled
            or low >> 26 != 9 or (low >> 21) & 31 != base or (low >> 16) & 31 != base
            or add >> 26 or add & 63 != 33 or (add >> 11) & 31 != scaled
            or {(add >> 21) & 31, (add >> 16) & 31} != {base, scaled}
            or load >> 26 != 35 or (load >> 21) & 31 != scaled or (load >> 16) & 31 != dest
            or jump & 0xfc1fffff != 8 or not dest):
        return None
    return at-40, branch, count, (((hi & 0xffff) << 16) + signed(low) + signed(load)) & 0xffffffff


def _address_pair_guard(image, start, at, order):
    """The guarded index and a LUI/addiu address pair meet in a separate result register."""
    for scheduled in (False, True):
        distance = 28 if scheduled else 32
        if at-start < distance:
            continue
        words = [int.from_bytes(image[p:p+4], order) for p in range(at-distance, at+8, 4)]
        if not scheduled and words.pop(2):  # the unscheduled guard has an empty delay slot
            continue
        bound, branch, scale, hi, low, add, load, jump, slot = words
        index, cond = (bound >> 21) & 31, (bound >> 16) & 31
        count = bound & 0xffff
        scaled, base = (scale >> 11) & 31, (hi >> 16) & 31
        result, dest = (add >> 11) & 31, (jump >> 21) & 31
        if (bound >> 26 != 11 or not index or not cond or index == cond or not 0 < count <= MAX_ENTRIES
                or branch >> 26 != 4 or (branch >> 21) & 31 != cond or (branch >> 16) & 31
                or slot or scale >> 26 or scale & 63 or (scale >> 6) & 31 != 2
                or (scale >> 16) & 31 != index or (scale >> 21) & 31 or not scaled
                or hi >> 26 != 15 or not base or base == scaled
                or low >> 26 != 9 or (low >> 21) & 31 != base or (low >> 16) & 31 != base
                or add >> 26 or add & 63 != 33 or not result
                or {(add >> 21) & 31, (add >> 16) & 31} != {base, scaled}
                or load >> 26 != 35 or (load >> 21) & 31 != result or (load >> 16) & 31 != dest
                or jump & 0xfc1fffff != 8 or not dest):
            continue
        return at-distance, branch, count, (((hi & 0xffff) << 16) + signed(low) + signed(load)) & 0xffffffff
    return None


def calls(image,functions,order,*,full_scan=False,check=lambda:None,progress=lambda _:None,checkpoint=None):
    """Bounded agreeing-path dataflow; a full scan applies the budget to each function."""
    from .mips_callback_flow import trace
    if not full_scan:
        return trace(image,functions,order,MAX_WORDS,MAX_CALLS)
    saved = checkpoint['load']() if checkpoint else None
    index, results, limited = saved if saved is not None else (0, [], False)
    for at in range(index,len(functions)):
        check()
        if not at % 32:
            progress({'phase':'Checking indirect references','step':6,'steps':6,'done':at,'total':len(functions)})
        traced, incomplete = trace(image,[functions[at]],order,MAX_WORDS,MAX_CALLS)
        results.extend(traced)
        limited |= incomplete
        if checkpoint and (at + 1 == len(functions) or not (at + 1) % 32):
            checkpoint['save'](at + 1,results,limited)
    progress({'phase':'Checking indirect references','step':6,'steps':6,'done':len(functions),'total':len(functions)})
    return results,limited


def _consumer_index(traced,check):
    """Index call loads without losing trace order or weakening mapping evidence."""
    exact=defaultdict(list);indexed=defaultdict(list)
    for i,call in enumerate(traced):
        if not i % 128:check()
        base,terms=call['load_address']
        if not terms:
            exact[base].append(i)
        elif len(terms)==1:
            stride=next(iter(terms.values()))
            if stride and 4<=stride<=256 and stride%4==0:
                indexed[(stride,base%stride)].append((base,i))
    for entries in indexed.values():entries.sort()
    return exact,indexed


def _possible_consumers(cells,exact,indexed,strides):
    matches=set()
    for cell,_ in cells:
        matches.update(exact.get(cell,()))
        for stride in strides:
            entries=indexed.get((stride,cell%stride),())
            lo=bisect_left(entries,(cell-stride*255,-1))
            hi=bisect_right(entries,(cell,float('inf')))
            matches.update(i for _,i in entries[lo:hi])
    return sorted(matches)


def consumers(image,functions,rows,order,ranges,mappings,unresolved=None,*,check=lambda:None,
              full_scan=False,progress=lambda _:None,checkpoint=None):
    from .audit_spool import editable
    traced,limited=(calls(image,functions,order,full_scan=True,check=check,progress=progress,checkpoint=checkpoint)
                    if full_scan else calls(image,functions,order))
    exact,indexed=_consumer_index(traced,check)
    strides=sorted({stride for stride,_ in indexed})
    linked=set()
    for row in editable(rows):
        check()
        found=[]
        for ref in row['pointer_references']:
            cells={(s['vram']+ref['rom_offset']-s['rom_start'],True) for s in ranges if s['rom_start']<=ref['rom_offset']<s['rom_end']}
            if not cells:cells={(ref['rom_offset']+m,False) for m in mappings}
            for index in _possible_consumers(cells,exact,indexed,strides):
                call=traced[index]
                base,terms=call['load_address']
                for cell,verified in cells:
                    stride=next(iter(terms.values())) if len(terms)==1 else None
                    if not terms and base==cell:index=None
                    elif stride and 4<=stride<=256 and stride%4==0 and 0<=cell-base<stride*256 and (cell-base)%stride==0:index=(cell-base)//stride
                    else:continue
                    mapped,problem=locations(cell,4,image,ranges)
                    mapping_ok=verified and not problem and len(mapped)==1 and mapped[0][0]==ref['rom_offset']
                    linked.add((call['function'],call['address']))
                    found.append({'function':call['function'],'address':call['address'],'pointer_rom':ref['rom_offset'],
                                  'evidence_chain':call.get('evidence_chain',[]),'mapping_verified':bool(mapping_ok),'indexed':bool(terms),'stride':stride,'possible_index':index,
                                  'state':'cell_load_call' if mapping_ok and not terms else 'consumer_hypothesis','reason':problem or ('index reachability is unproven' if terms else ('reviewed cell mapping' if mapping_ok else 'raw-data mapping is unproven'))})
                    if len(found)>=8:break
                if len(found)>=8:break
            if len(found)>=8:break
        row['callback_consumers']=found
    if unresolved is not None:
        unresolved.extend({**c,'reason':'Runtime pointer or cell mapping unresolved; not linked to a candidate'} for c in traced if (c['function'],c['address']) not in linked)
    return limited
