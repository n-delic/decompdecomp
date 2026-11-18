"""Normalize proven scalar-MIPS import extents; uncertain ownership stays with review.

Only frozen disassembly is changed, before it becomes project work. A balanced,
closed single-frame body may cross generated fragment boundaries, but not explicit
symbols, data, independent references, image gaps or competing mappings.
"""
import heapq
import re
import struct
from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace

from ..arch.mips import JR_RA, is_frame
from ..arch.mips_code_recovery import called_extent, leaf_extent


def repair(functions, data, image, console, segments, *, symbols=(), log=print):
    """Keep all original instructions while joining/splitting strictly validated bodies."""
    if console.arch!='mips':return functions
    from .split import _LOCAL_REF, _SYMBOL_LINE, AsmLine
    explicit={int(m[2],16) for text in symbols for m in _SYMBOL_LINE.finditer(text)}
    starts={f.vram for f in functions}
    order='big' if console.big_endian else 'little'
    fmt='>I' if console.big_endian else '<I'
    # A raw function-pointer entry is independent evidence even without a direct call.
    shared,slots=shared_slot_calls(functions,image,console,segments)
    # A stored word equal to an address in a shared slot may be any sibling overlay's table entry, and the segment's
    # own extent can run on over its siblings' images (the game above: five overlays in one segment's ROM range).
    pointers={w for w, in struct.iter_unpack(fmt,image[:len(image)//4*4]) if w in starts and not any(s.contains(w,4) for s in slots)}
    references=defaultdict(set)
    names={f.name:f.vram for f in functions}
    for fn in functions:
        for line in fn.lines:
            op=line.word>>26
            if op in (2,3):
                dest=((line.vram+4)&0xf0000000)|((line.word&0x3ffffff)<<2)
                references[dest].add((line.rom,fn.segment))
            elif op in (1,4,5,6,7,20,21,22,23) or op==17 and (line.word>>21)&31==8:
                delta=line.word&0xffff
                dest=line.vram+4+(delta-0x10000 if delta&0x8000 else delta)*4
                references[dest].add((line.rom,fn.segment))
            for token in re.findall(r'[\w.]+',line.operands):
                if token in names:references[names[token]].add((line.rom,fn.segment))
    for dest,offset,segment in shared:
        references[dest].discard((offset,segment))
        if not references[dest]:del references[dest]
    destinations=sorted(references)
    tables=[dict(rom_start=s.rom_start,rom_end=s.rom_end,vram=s.vram) for s in segments
            if 0<=s.rom_start<s.rom_end<=len(image) and not s.rom_start%4 and not s.rom_end%4]
    returns={fn.rom+fn.size for fn in functions if fn.size>=8 and fn.lines[-2].word==JR_RA}
    groups=[];group_end=0
    for fn in sorted(functions,key=lambda f:(f.segment,f.rom)):
        if (groups and groups[-1][-1].segment==fn.segment
                and fn.vram-fn.rom==groups[-1][0].vram-groups[-1][0].rom
                and (fn.rom<=group_end or fn.rom-group_end<=HOLE and not any(image[group_end:fn.rom]))):
            groups[-1].append(fn)
        else:
            groups.append([fn]);group_end=0
        group_end=max(group_end,fn.rom+fn.size)
    result=[];changed=0
    for group in groups:
        check=getattr(log,'check_cancel',lambda:None);check()
        first=group[0];mapping=first.vram-first.rom
        lines={};consistent=True
        for fn in group:
            for line in fn.lines:
                if (line.rom<0 or line.rom+4>len(image) or line.vram!=line.rom+mapping
                        or int.from_bytes(image[line.rom:line.rom+4],order)!=line.word
                        or line.rom in lines and lines[line.rom].word!=line.word):consistent=False;break
                lines[line.rom]=line
        lo=first.rom;hi=max(f.rom+f.size for f in group)
        # Zero words the disassembler left after an endlabel as alignment, between two pieces that a call cut: kept
        # out of every function unless a validated body runs through them (then they're its nops).
        holes=[];covered=sorted((f.rom,f.rom+f.size) for f in group);reach=lo
        for a,b in covered:
            if a>reach:holes.append((reach,a))
            reach=max(reach,b)
        for a,b in holes:
            for at in range(a,b,4):lines[at]=AsmLine(at,at+mapping,0,'nop','')
        owners=[s for s in segments if s.rom_start<=lo<hi<=s.rom_end and s.vram-s.rom_start==mapping]
        if (not consistent or len(owners)!=1 or len(lines)*4!=hi-lo
                or any(s is not owners[0] and s.vram<hi+mapping and lo+mapping<s.vram+s.rom_end-s.rom_start for s in segments)):
            result.extend(group);continue
        cuts={f.rom for f in group}|{hi}|{x for hole in holes for x in hole};accepted=[]
        # A new interior entry needs the preceding balanced return, never a frame pattern alone.
        candidates=[at for at,line in lines.items() if is_frame(line.word) and (at in cuts or
                    at-8 in lines and lines[at-8].word==JR_RA and lines[at-4].word&0xffff0000==0x27bd0000)]
        padding=set(holes);proposed_padding={};leaves=set()
        def seed(cut,hi=hi,lines=lines,mapping=mapping,leaves=leaves,proposed_padding=proposed_padding):
            at=cut
            while at<min(cut+12,hi) and at in lines and lines[at].word==0:at+=4
            if at>=hi or at not in lines:return None
            # Alignment is excluded only after a known return and without any
            # explicit entry, pointer, branch or data reference into those NOPs.
            if any(cut+mapping<=a<at+mapping for a in explicit|pointers|set(destinations)):return None
            if any(d.rom is not None and d.rom<at and cut<d.rom+d.size for d in data):return None
            if not is_frame(lines[at].word):leaves.add(at)
            if at>cut:proposed_padding[at]=(cut,at)
            return at
        # The group's first entry is known as well: a leaf there ends at its own return, not at a call's cut.
        for cut in sorted(cuts & returns|{lo}):
            if (at:=seed(cut)) is not None:candidates.append(at)
        pending=list(set(candidates)|leaves);heapq.heapify(pending);seen=set()
        while pending:
            at=heapq.heappop(pending)
            if at in seen:continue
            seen.add(at)
            check()
            if accepted and at<accepted[-1][1]:continue
            # A switch's table is read where the config maps data, so a body with one closes (a shared overlay
            # slot's switch functions stayed cut where outside calls had cut them). An entry after a return that is
            # no leaf can be a frame the compiler scheduled a load or two ahead of (called_extent's lead).
            framed=dict(big_endian=console.big_endian,cpu=console.cpu_variant,asm_check=console.asm_reason)
            size=(leaf_extent(image,at,hi,at+mapping,**framed) if at in leaves else None) or \
                called_extent(image,at,hi,at+mapping,**framed,table_ranges=tables)
            if not size:continue
            end=at+size;address=at+mapping;finish=end+mapping
            if any(address<a<finish for a in explicit|pointers):continue
            if any(d.rom is not None and d.rom<end and at<d.rom+d.size for d in data):continue
            independent=any(not (at<=offset<end and segment==first.segment)
                            for dest in destinations[bisect_right(destinations,address):bisect_right(destinations,finish-1)]
                            for offset,segment in references[dest])
            if independent:continue
            if not any(at<c<end for c in cuts) and at in cuts and end in cuts:continue
            # Retain alignment in the original image, outside the C function.
            if at in proposed_padding:
                padding.add(proposed_padding[at])
                cuts.update(proposed_padding[at])
            accepted.append((at,end));cuts={c for c in cuts if not at<c<end}|{at,end}
            if end-8 in lines and lines[end-8].word==JR_RA:
                if (following:=seed(end)) is not None:heapq.heappush(pending,following)
        if not accepted:result.extend(group);continue
        changed+=len(accepted)
        names={f.rom:f.name for f in group};by_start={f.rom:f for f in group}
        labels=set().union(*(f.labels for f in group))
        ordered=sorted(cuts)
        for start,end in zip(ordered,ordered[1:]):
            if (start,end) in padding:continue
            source=by_start.get(start) or next(f for f in group if f.rom<=start<f.rom+f.size)
            name=names.get(start,f'func_{start+mapping:08X}')
            body=[replace(lines[at]) for at in range(start,end,4)]
            local={f.name:f.vram for f in group if start<f.rom<end}
            for line in body:
                line.operands=re.sub(r'[\w.]+',lambda m,local=local:f'.L{local[m[0]]:08X}' if m[0] in local else m[0],line.operands)
            owned={a for a in labels if start+mapping<=a<end+mapping}|set(local.values())
            result.append(replace(source,name=name,vram=start+mapping,rom=start,lines=body,labels=owned,piece=source.piece if start==source.rom and not any(a==start and b==end for a,b in accepted) else False))
    if changed:
        # A label moved to a separate body is now an entry symbol, not a local
        # label in the preceding assembly file. Keep cross-body branches linkable.
        entries={(fn.segment,fn.vram):fn.name for fn in result}
        rewritten=[]
        for fn in result:
            body=[]
            for line in fn.lines:
                def entry(match, fn=fn):
                    address=int(match[1],16)
                    return entries.get((fn.segment,address),match[0]) if not fn.vram<=address<fn.vram+fn.size else match[0]
                body.append(replace(line,operands=_LOCAL_REF.sub(entry,line.operands)))
            rewritten.append(replace(fn,lines=body))
        result=rewritten
    if changed:log(f'Automatically repaired {changed} proven function boundaries; original instruction bytes retained. Uncertain entries still need review.')
    entered=explicit|pointers|set(destinations)
    def referenced(fn):
        return (fn.vram in explicit or fn.vram in pointers or any(offset!=fn.rom for offset,_ in references.get(fn.vram,()))
                or any(d.rom is not None and d.rom<fn.rom+fn.size and fn.rom<d.rom+d.size for d in data))
    kept=[];dropped=0;ordered=sorted(result,key=lambda f:(f.segment,f.rom))
    for i,fn in enumerate(ordered):
        previous=kept[-1] if kept else None;following=ordered[i+1] if i+1<len(ordered) else None
        if (filler_extent(fn.size,fn.lines[0].word) and previous is not None and following is not None
                and previous.segment==fn.segment==following.segment
                and previous.rom+previous.size==fn.rom and following.rom==fn.rom+4 and following.vram==fn.vram+4
                and alignment_fill(fn.vram,previous.size>=8 and previous.lines[-2].word==JR_RA,
                                   following.vram in entered or is_frame(following.lines[0].word),referenced(fn))):
            dropped+=1;continue
        kept.append(fn)
    if dropped:
        log(f'Left {dropped} alignment words out of the functions: filler between a return and the next entry, kept in the image.')
        return kept
    return result


HOLE = 12  # the most alignment a group of one segment's pieces may skip between two of them
SHARED_SLOT_CALLS = 8  # calls from elsewhere into a segment before their fit says anything about it


def shared_slot_calls(functions, image, console, segments):
    """(target, caller rom, caller segment) of the calls from other segments into a segment that holds only one of
    the overlays loaded at its address. Code elsewhere calls every overlay of the slot, so most of those calls land
    mid-function in the one image the split mapped there, and none of them proves an entry in it. An N64 game loads
    five overlays at one address: 17 of main's 112 calls into the slot fit the image placed there, the rest cut 87
    of its functions into pieces. A segment whose outside calls mostly land on a stack frame, or right after a
    return's delay slot, is an ordinary overlay: its calls stay evidence. Also returns those segments: a stored word that
    equals an address in one (a sibling's function table) proves no entry either."""
    fmt='>I' if console.big_endian else '<I'
    def word(at):
        return struct.unpack_from(fmt,image,at)[0] if 0<=at<=len(image)-4 else None
    calls=defaultdict(list)
    for fn in functions:
        for line in fn.lines:
            if line.word>>26!=3:continue
            dest=((line.vram+4)&0xf0000000)|((line.word&0x3ffffff)<<2)
            for s in segments:
                if s.name!=fn.segment and s.contains(dest,4):
                    calls[s.name].append((dest,line.rom,fn.segment,dest-s.vram+s.rom_start,s.rom_start))
    out,slots=[],[]
    for name,found in calls.items():
        targets={c[3]:c[4] for c in found}
        if len(targets)<SHARED_SLOT_CALLS:continue
        fit=sum(1 for at,start in targets.items() if at==start or is_frame(word(at) or 0) or word(at-8)==JR_RA)
        if fit*2<len(targets):
            out+=[c[:3] for c in found];slots+=[s for s in segments if s.name==name]
    return out,slots


def alignment_fill(address, returned, entry_follows, referenced):
    """One word between two functions that is filler, not code: the 8-byte alignment some assemblers (the SN
    assembler of N64 kits) fill with whatever bytes were left over instead of zeros.

    spimdisasm makes it a 4-byte function of its own, which can't be complete (a return needs a delay slot) and
    runs on into the next one, so it was flagged for boundary review: an N64 game had 16 of them, another 73, one
    worked on by an agent as if it were assembly. The word sits at an address ending in 4 or C, right after a
    function that returned (`jr $ra` and its delay slot) and right before an 8-aligned entry something calls or
    that opens its own stack frame, and nothing branches to it, calls it or points at it. Zero alignment is
    trimmed earlier (split._trim_padding); export rebuilds either kind from the image (export_build.fill_gaps)."""
    return address%8==4 and returned and entry_follows and not referenced


def filler_extent(size, first):
    """Whether an entry of `size` bytes starting with the word `first` can be one alignment word: 4 bytes, or 8 when
    the filler happens to decode as a branch or jump, so the disassembler gave it the next entry's first
    instruction as its delay slot (an N64 game had 4 of these; the next entry still starts 4 bytes in)."""
    import rabbitizer
    return size==4 or size==8 and rabbitizer.Instruction(first).hasDelaySlot()
