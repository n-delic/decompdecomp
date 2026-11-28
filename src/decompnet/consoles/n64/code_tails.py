"""Complete called functions caught in the alignment tail of a detected code segment.

The block scanner can see no frame/return for several blocks inside a large function.
Its last-return alignment then includes the next prologue but leaves its body as bin.
Extend a frame through a balanced return, or a directly called leaf through closed
control flow and a return, without crossing a known segment or another called entry.
"""
from ...arch.mips import JR_RA, is_frame

MAX_TAIL = 0x10000


def _called_leaf_tail(rom, start, end, vram, entry, limit, called):
    """Finish a called leaf cut just after its preceding return, if its control flow closes."""
    import rabbitizer

    address = vram + entry - start
    if address not in called or end - entry > 16:
        return None
    targets, delays = set(), set()
    for at in range(entry, limit - 7, 4):
        word = int.from_bytes(rom[at:at+4], 'big')
        insn = rabbitizer.Instruction(word)
        op = word >> 26
        if (not insn.isValid() or is_frame(word) or op == 3
                or op == 1 and (word >> 16) & 31 in (16, 17, 18, 19)
                or at in delays and insn.hasDelaySlot()):
            return None
        if op == 0 and word & 63 in (8, 9):
            if word != JR_RA:
                return None
            delay_word = int.from_bytes(rom[at+4:at+8], 'big')
            delay = rabbitizer.Instruction(delay_word)
            if not delay.isValid() or delay.hasDelaySlot() or is_frame(delay_word):
                return None
            recovered = at + 8
            if recovered <= end or any(not entry <= target < recovered or target in delays for target in targets):
                return None
            if any(address < target < vram + recovered - start for target in called):
                return None
            return recovered
        if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or (op == 17 and (word >> 21) & 31 == 8):
            delta = word & 0xffff
            targets.add(at + 4 + (delta - 0x10000 if delta & 0x8000 else delta) * 4)
        elif op == 2:
            target = ((vram + at - start + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
            targets.add(start + target - vram)
        if insn.hasDelaySlot():
            delays.add(at + 4)
    return None


def bridge_called_frame(rom, segments, taken=(), *, called=()):
    """Join a called frame whose body and epilogue straddle a missed code gap."""
    from ... import consoles
    from ...arch.mips_code_recovery import called_extent

    console = consoles.get('n64')
    ordered = sorted(segments)
    result = []
    index = 0
    while index < len(ordered):
        left = ordered[index]
        if index+1 == len(ordered):
            result.append(left)
            break
        right = ordered[index+1]
        start, end, vram = left
        next_start, next_end, next_vram = right
        entry = end-4
        address = vram+entry-start
        gap = next_start-end
        if (not 0 < gap <= MAX_TAIL or entry < start or address not in called
                or next_vram-next_start != vram-start
                or any(a < next_start and end < b for a, b, _ in taken)):
            result.append(left);index += 1;continue
        size = called_extent(rom, entry, next_end, address, big_endian=True,
                             cpu=console.cpu_variant, asm_check=console.asm_reason)
        stop = entry+(size or 0)
        limit = address+(size or 0)
        if (not size or not next_start < stop <= next_end
                or any(address < target < limit for target in called)
                or any(a < stop and entry < b for a,b,v in (*taken, *ordered)
                       if (a,b,v) not in (left,right))
                or any(v < limit and address < v+b-a for a,b,v in (*taken,*ordered)
                       if (a,b,v) not in (left,right))):
            result.append(left);index += 1;continue
        result.append((start,next_end,vram))
        index += 2
    return result


def complete(rom, segments, taken=(), *, called=(), allow_inferred_overlap=False):
    import rabbitizer

    result = []
    all_segments = list(segments)+list(taken)
    for start,end,vram in segments:
        word = lambda at:int.from_bytes(rom[at:at+4],'big')
        # Alignment adds at most 12 bytes after a return and its delay slot.
        returns = [at for at in range(max(start,end-32),end-4,4) if word(at)==JR_RA]
        # A separately detected segment can contain only the next prologue's
        # first one or two instructions, without a preceding return in this parent.
        last = returns[-1]+8 if returns else start if is_frame(word(start)) else end
        if last>=end or not any(word(at) for at in range(last,end,4)):
            result.append((start,end,vram));continue
        # A compiler may schedule one or two loads before the frame, caught by padding.
        lookahead = 64 if vram+last-start in called else 16
        frames = [at for at in range(last,min(end+lookahead,len(rom)-3),4) if is_frame(word(at))]
        if not frames:
            limit = min(len(rom), end + MAX_TAIL, *[s for s,_,_ in all_segments if s>=end and s!=start])
            for s,e,v in all_segments:
                if s!=start and v>=vram+end-start and (not allow_inferred_overlap or (s,e,v) in taken):
                    limit = min(limit,start+v-vram)
            recovered = _called_leaf_tail(rom, start, end, vram, last, limit, called) if returns else None
            result.append((start,recovered or end,vram));continue
        frame = frames[0]
        if any(not rabbitizer.Instruction(word(at)).isValid() or word(at)>>26 in (1,2,3,4,5,6,7)
               or (word(at)>>26==0 and word(at)&63 in (8,9)) for at in range(last,frame,4)):
            result.append((start,end,vram));continue
        size = 0x10000-(word(frame)&0xffff)
        limit = min(len(rom),frame+MAX_TAIL,*[s for s,_,_ in all_segments if s>=end and s!=start])
        # Also stop before a different ROM segment occupying the same RAM mapping.
        for s,e,v in all_segments:
            if (s!=start and v>=vram+end-start
                    and (not allow_inferred_overlap or (s,e,v) in taken)):
                limit = min(limit,start+v-vram)
        highest = end
        destinations = set()
        recovered = None
        for at in range(frame,limit-3,4):
            w = word(at)
            if not rabbitizer.Instruction(w).isValid() or (at!=frame and is_frame(w)):
                break
            op = w>>26
            # Direct local branches must fit inside the recovered extent, delay slot too.
            if op in (1,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8):
                delta=w&0xffff;delta=delta-0x10000 if delta&0x8000 else delta
                target=at+4+delta*4
                if target<start or target>=limit:
                    break
                destinations.add(target)
                highest=max(highest,target+4,at+8)
            if w==JR_RA and at+8<=limit:
                # The selected terminal return must pop precisely this prologue's frame.
                pop = 0x27BD0000 | size
                delayed = word(at+4) == pop
                # Branches may share the stack restore as an epilogue. A jump
                # to the return or its delay slot would skip that restore.
                before = (at >= frame+8 and word(at-4) == pop
                          and not rabbitizer.Instruction(word(at-8)).hasDelaySlot()
                          and not destinations.intersection((at, at+4)))
                if (delayed or before) and at+8>=highest:
                    recovered=at+8
                break
        new_end=max(end,recovered or end)
        result.append((start,new_end,vram))
    return result
