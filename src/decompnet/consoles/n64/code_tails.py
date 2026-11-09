"""Complete a frame prologue caught in the alignment tail of a detected code segment.

The block scanner can see no frame/return for several blocks inside a large function.
Its last-return alignment then includes the next prologue but leaves its body as bin.
Only extend that specific shape, through valid instructions to a balanced return.
"""
from ...arch.mips import JR_RA, is_frame

MAX_TAIL = 0x10000


def complete(rom, segments, taken=()):
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
        frames = [at for at in range(last,min(end+16,len(rom)-3),4) if is_frame(word(at))]
        if not frames:
            result.append((start,end,vram));continue
        frame = frames[0]
        if any(not rabbitizer.Instruction(word(at)).isValid() or word(at)>>26 in (1,2,3,4,5,6,7)
               or (word(at)>>26==0 and word(at)&63 in (8,9)) for at in range(last,frame,4)):
            result.append((start,end,vram));continue
        size = 0x10000-(word(frame)&0xffff)
        limit = min(len(rom),frame+MAX_TAIL,*[s for s,_,_ in all_segments if s>=end and s!=start])
        # Also stop before a different ROM segment occupying the same RAM mapping.
        for s,e,v in all_segments:
            if s!=start and v>=vram+end-start:
                limit = min(limit,start+v-vram)
        highest = end
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
                highest=max(highest,target+4,at+8)
            if w==JR_RA and at+8<=limit:
                # The selected terminal return must pop precisely this prologue's frame.
                if word(at+4)==(0x27BD0000|size) and at+8>=highest:
                    recovered=at+8
                break
        new_end=max(end,recovered or end)
        result.append((start,new_end,vram))
    return result
