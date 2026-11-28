"""Normalize proven scalar-MIPS import extents; uncertain ownership stays with review.

Only frozen disassembly is changed, before it becomes project work. A balanced,
closed single-frame body may cross generated fragment boundaries, but not explicit
symbols, data, independent references, image gaps or competing mappings.
"""
import heapq
import re
import struct
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import replace

from ..arch.mips import JR_RA, is_frame
from ..arch.mips_code_recovery import called_extent, leaf_extent


def _has_delay_slot(word):
    import rabbitizer
    return rabbitizer.Instruction(word).hasDelaySlot()


def _owned_partition(group, cuts, padding):
    """Reject a partition that begins a function in an unowned alignment gap."""
    ordered = sorted(cuts)
    spans = [(a, b) for a, b in zip(ordered, ordered[1:]) if (a, b) not in padding]
    return spans if all(any(fn.rom <= a < fn.rom + fn.size for fn in group) for a, _ in spans) else None


def _restore_original_delay_slots(functions, original_entries, image, order):
    """Keep an original transfer's delay word even when it is also a separate entry."""
    out = []
    for fn in functions:
        original = original_entries.get((fn.rom, fn.vram))
        if (fn.lines and _has_delay_slot(fn.lines[-1].word) and original is not None
                and original.size >= fn.size + 4
                and all(a.rom == b.rom and a.vram == b.vram and a.word == b.word
                        for a, b in zip(fn.lines, original.lines))):
            delay = original.lines[len(fn.lines)]
            if (delay.rom == fn.rom + fn.size and delay.vram == fn.vram + fn.size
                    and image[delay.rom:delay.rom + 4] == delay.word.to_bytes(4, order)):
                fn = replace(fn, lines=fn.lines + [delay])
        out.append(fn)
    return out


_AT = re.compile(r'\$(?:at|1)\b')
_HI = re.compile(r'%hi\(([^)]+)\)')
_LO = re.compile(r'%lo\(([^)]+)\)')


def _indexed_store_references(fn):
    """Symbolic addresses used only for an indexed store, without escaping in $at.

    A different overlay can store to RAM occupied by a function in this image.
    Splat names that data address after the function, but the store does not
    establish a callable entry. Decline uncertain control flow or any use of
    the computed address before $at is overwritten.
    """
    lines = fn.lines
    if any(b.rom != a.rom + 4 or b.vram != a.vram + 4 for a, b in zip(lines, lines[1:])):
        return {}
    by_vram = {line.vram: i for i, line in enumerate(lines)}

    def unsafe(line):
        return (line.mnemonic.startswith('.') or _AT.search(line.operands)
                or line.mnemonic.lower() in ('eret', 'syscall', 'break', 'teq', 'tne', 'tge', 'tgeu', 'tlt', 'tltu',
                                              'teqi', 'tnei', 'tgei', 'tgeiu', 'tlti', 'tltiu'))

    def dies_after(start):
        pending, seen = [start], set()
        while pending:
            i = pending.pop()
            if i in seen:
                continue
            if i < 0 or i >= len(lines) or len(seen) >= 512:
                return False
            seen.add(i)
            line = lines[i]
            word, op = line.word, line.word >> 26
            if op == 15 and (word >> 16) & 31 == 1:
                continue  # an unconditional overwrite
            if unsafe(line):
                return False
            if not _has_delay_slot(word):
                pending.append(i + 1)
                continue
            if i + 1 >= len(lines):
                return False
            delay = lines[i + 1]
            if unsafe(delay) or _has_delay_slot(delay.word):
                return False
            if word == JR_RA:
                continue
            if op == 2:
                target = ((line.vram + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                if target not in by_vram:
                    return False
                pending.append(by_vram[target])
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
                if op == 1 and (word >> 16) & 31 in (16, 17, 18, 19):
                    return False  # a linking branch may pass the register to its callee
                delta = word & 0xffff
                target = line.vram + 4 + (delta - 0x10000 if delta & 0x8000 else delta) * 4
                if target not in by_vram:
                    return False
                pending.extend((i + 2, by_vram[target]))
            else:
                return False  # calls and indirect transfers can consume $at
        return True

    out = defaultdict(set)
    for i in range(len(lines) - 2):
        high, add, store = lines[i:i + 3]
        a, b, c = high.word, add.word, store.word
        if not (a >> 26 == 15 and (a >> 16) & 31 == 1
                and i > 0 and not _has_delay_slot(lines[i - 1].word)
                and add.rom == high.rom + 4 and store.rom == add.rom + 4
                and add.vram == high.vram + 4 and store.vram == add.vram + 4
                and b >> 26 == 0 and b & 63 == 33 and (b >> 21) & 31 == 1
                and (b >> 11) & 31 == 1 and (b >> 16) & 31 != 1
                and c >> 26 in (40, 41, 43, 57) and (c >> 21) & 31 == 1
                and ((c >> 16) & 31 != 1 or c >> 26 == 57)):
            continue
        hi, lo = _HI.search(high.operands), _LO.search(store.operands)
        if not hi or not lo or hi.group(1) != lo.group(1) or not dies_after(i + 3):
            continue
        symbol = re.match(r'\s*([\w.$]+)', hi.group(1))
        if symbol:
            out[high.rom].add(symbol[1])
            out[store.rom].add(symbol[1])
    return out


def repair(functions, data, image, console, segments, *, symbols=(), log=print, inline_data=()):
    """Keep all original instructions while joining/splitting strictly validated bodies."""
    if console.arch!='mips':return functions
    from .split import _LOCAL_REF, _SYMBOL_LINE, AsmLine, DataSymbol
    explicit={int(m[2],16) for text in symbols for m in _SYMBOL_LINE.finditer(text)}
    from .callback_prefix import recover as recover_callback_prefixes
    before_callbacks = len(functions)
    functions = recover_callback_prefixes(functions, data, image, console, segments, explicit,
                                           check=getattr(log, 'check_cancel', lambda: None))
    if len(functions) < before_callbacks:
        log(f'Recovered {before_callbacks-len(functions)} callback entries with validated shared slots.')
    starts={f.vram for f in functions}
    original_entries={(f.rom, f.vram):f for f in functions}
    order='big' if console.big_endian else 'little'
    fmt='>I' if console.big_endian else '<I'
    # Generated C units can change at a false cut inside one mapped code segment.
    def owner(fn):
        mapped = [i for i, s in enumerate(segments)
                  if s.rom_start <= fn.rom < fn.rom+fn.size <= s.rom_end
                  and s.vram-s.rom_start == fn.vram-fn.rom]
        return (mapped[0], '') if len(mapped) == 1 else (-1, fn.segment)

    # A raw function-pointer entry is independent evidence even without a direct call.
    shared,slots=shared_slot_calls(functions,image,console,segments)
    store_tails = set()
    if slots:
        ordered = sorted(functions, key=lambda fn: (owner(fn), fn.rom))
        for head, tail in zip(ordered, ordered[1:]):
            if (owner(head)[0] < 0 or owner(head) != owner(tail) or not head.lines or tail.size < 8
                    or head.rom + head.size != tail.rom or head.vram + head.size != tail.vram
                    or not is_frame(head.lines[0].word) or is_frame(tail.lines[0].word)
                    or tail.lines[-2].word != JR_RA):
                continue
            if called_extent(image, head.rom, tail.rom + tail.size, head.vram,
                             big_endian=console.big_endian, cpu=console.cpu_variant,
                             asm_check=console.asm_reason) == head.size + tail.size:
                store_tails.add(tail.vram)
    # A stored word equal to an address in a shared slot may be any sibling overlay's table entry, and the segment's
    # own extent can run on over its siblings' images (the game above: five overlays in one segment's ROM range).
    zero_entries = {fn.vram for fn in functions if fn.size == 4 and not fn.lines[0].word}
    pointers={w for i,(w,) in enumerate(struct.iter_unpack(fmt,image[:len(image)//4*4]))
              if w in starts and not any(s.contains(w,4) for s in slots)
              and (w not in zero_entries or not console.import_pointer_is_data(image,4*i))}
    named_or_pointed = explicit | pointers
    references=defaultdict(set)
    direct_calls=set()
    transfer_targets=set()
    shared_calls=set(shared)
    names={f.name:f.vram for f in functions}
    lines_by_rom={line.rom:line for fn in functions for line in fn.lines}
    reference_bodies={}
    for fn in functions:
        store_refs = _indexed_store_references(fn) if slots else {}
        for index,line in enumerate(fn.lines):
            op=line.word>>26
            # A conditional link with a transfer in its delay slot has no defined MIPS execution. In particular,
            # instruction-shaped data can look like a call into the middle of a real framed function. Do not
            # treat that impossible link as an independent entry, while retaining all ordinary calls and pointers.
            if (op == 1 and (line.word >> 16) & 31 in (16, 17, 18, 19) and index+1 < len(fn.lines)
                    and _has_delay_slot(fn.lines[index+1].word)):
                continue
            if op in (2,3):
                dest=((line.vram+4)&0xf0000000)|((line.word&0x3ffffff)<<2)
                references[dest].add((line.rom,fn.segment))
                transfer_targets.add(dest)
                if op==3 and (dest,line.rom,fn.segment) not in shared_calls:
                    direct_calls.add(dest)
            elif op in (1,4,5,6,7,20,21,22,23) or op==17 and (line.word>>21)&31==8:
                delta=line.word&0xffff
                dest=line.vram+4+(delta-0x10000 if delta&0x8000 else delta)*4
                references[dest].add((line.rom,fn.segment))
                transfer_targets.add(dest)
            for token in re.findall(r'[\w.]+',line.operands):
                if token in names:
                    address = names[token]
                    if (address in store_tails and token in store_refs.get(line.rom, ())
                            and any(s.contains(address, 4) and not s.rom_start <= line.rom < s.rom_end for s in slots)):
                        continue
                    references[names[token]].add((line.rom,fn.segment))
                    reference_bodies[line.rom]=fn
    for dest,offset,segment in shared:
        references[dest].discard((offset,segment))
        if not references[dest]:del references[dest]
    stale = stale_code_copies(functions, image, order, console, segments, owner, explicit,
                              references, direct_calls, data)
    distant = distant_stale_fragments(functions, image, order, console, segments, owner, explicit,
                                      references, direct_calls, data)
    for fn in functions:
        if (fn.rom, fn.vram) in distant:
            data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data', boundary_proven=True))
    stale |= distant
    if stale:
        removed_sources = {line.rom for fn in functions if (fn.rom, fn.vram) in stale for line in fn.lines}
        functions = [fn for fn in functions if (fn.rom, fn.vram) not in stale]
        for target in list(references):
            references[target] = {(source, unit) for source, unit in references[target] if source not in removed_sources}
            if not references[target]:
                del references[target]
        direct_calls = {target for target in direct_calls if any(
            int.from_bytes(image[source:source+4], order) >> 26 == 3 for source, _ in references.get(target, ()))}
        transfer_targets = {target for target in transfer_targets if any(
            int.from_bytes(image[source:source+4], order) >> 26 in (1, 2, 3, 4, 5, 6, 7, 17, 20, 21, 22, 23)
            for source, _ in references.get(target, ()))}
        log(f'Left {len(stale)} proven stale code copies in the original image.')
    functions = recover_inline_tables(functions, data, image, order, explicit | pointers, references, segments, log)
    buffer_written = buffer_write_evidence(functions, image, console, check=getattr(log,'check_cancel',lambda:None))
    data_entry_targets = explicit|pointers|transfer_targets
    data_only = {
        fn.vram for fn in functions
        if (fn.size==4 or fn.size==8 and all(line.word==0xbf800000 for line in fn.lines))
        and image[fn.rom:fn.rom+fn.size]==b''.join(line.word.to_bytes(4,order) for line in fn.lines)
        and not any(address in data_entry_targets for address in range(fn.vram,fn.vram+fn.size,4))
        and not any(d.rom is not None and d.rom<fn.rom+fn.size and fn.rom<d.rom+d.size for d in data)
        and (address_word_references(fn, references, lines_by_rom)
             or not fn.lines[0].word and buffer_written(fn, references, reference_bodies))
    }
    tables=[dict(rom_start=s.rom_start,rom_end=s.rom_end,vram=s.vram) for s in segments
            if 0<=s.rom_start<s.rom_end<=len(image) and not s.rom_start%4 and not s.rom_end%4]
    functions = recover_segment_switch_data(functions, data, image, order, tables,
                                            explicit | pointers | transfer_targets, references, log)
    tables.extend(console.import_table_ranges(image,segments))
    functions, removed = split_address_tables_after_return(functions, data, image, order, console, owner,
                                                          segments, explicit | pointers | set(references))
    entered = explicit | pointers | set(references)
    functions = split_aligned_frames(functions, data, image, order, console, owner, segments, entered)
    functions = split_alignment_after_leaf(functions, data, image, order, console, owner, segments, entered)
    gaps = multiword_alignment(functions, image, order, owner, explicit | pointers, references,
                               direct_calls, data, segments, console)
    if gaps or removed:
        removed.update(line.rom for fn in functions if (fn.rom, fn.vram) in gaps for line in fn.lines)
        functions = [fn for fn in functions if (fn.rom, fn.vram) not in gaps]
        # Discard edges decoded from filler, keeping an overlapping delay
        # word wherever the next function owns it as an actual instruction.
        removed.difference_update(line.rom for fn in functions for line in fn.lines)
        for target in list(references):
            references[target] = {(source, unit) for source, unit in references[target] if source not in removed}
            if not references[target]:
                del references[target]
                direct_calls.discard(target)
        if gaps:log(f'Left {len(gaps)} fragments of alignment between functions in the original image.')
    destinations=sorted(references)
    entered_addresses=explicit|pointers|set(destinations)
    returns={fn.rom+fn.size for fn in functions if fn.size>=8 and fn.lines[-2].word==JR_RA}
    groups=[];group_end=0
    for fn in sorted(functions,key=lambda f:(owner(f),f.rom)):
        if (groups and owner(groups[-1][-1])==owner(fn)
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
                or any(s is not owners[0] and s.vram<hi+mapping and lo+mapping<s.vram+s.rom_end-s.rom_start
                       and not (owners[0].exclusive_ram_id and s.exclusive_ram_id == owners[0].exclusive_ram_id)
                       for s in segments)):
            result.extend(group);continue
        segment = owners[0]
        siblings = [other for other in segments if other is not segment and segment.exclusive_ram_id
                    and other.exclusive_ram_id == segment.exclusive_ram_id]
        cuts={f.rom for f in group}|{hi}|{x for hole in holes for x in hole};accepted=[]
        # A new interior entry needs the preceding balanced return, never a frame pattern alone.
        candidates=[at for at,line in lines.items() if is_frame(line.word) and (at in cuts or
                    at-8 in lines and lines[at-8].word==JR_RA and lines[at-4].word&0xffff0000==0x27bd0000)]
        padding=set(holes);proposed_padding={}
        # A directly called frameless entry may have been cut from its own return.
        leaves={at for at in cuts if at in lines and at+mapping in direct_calls}
        link_fill={}
        for cut in cuts & returns:
            # A stray alignment word can decode as a conditional link through $at.
            # If taken it would replace the return address of the frameless body.
            if cut+8>hi or (cut+mapping)%8!=4 or lines[cut].word>>16!=0x0433:
                continue
            at=cut+4
            delta=lines[cut].word&0xffff
            target=cut+4+(delta-0x10000 if delta&0x8000 else delta)*4
            if (target<=at+4 or target not in cuts or cut+mapping in entered_addresses
                    or any(d.rom is not None and d.rom<at and cut<d.rom+d.size for d in data)):
                continue
            if any(offset!=cut for offset,_ in references.get(target+mapping,())):
                continue
            leaves.add(at)
            link_fill[at]=(cut,target)
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
        called_cuts = sorted(cut for cut in cuts if cut+mapping in direct_calls)
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
            size=leaf_extent(image,at,hi,at+mapping,table_ranges=tables,**framed) if at in leaves else None
            closed_leaf=bool(size)
            ignored_edge=None
            if at in link_fill:
                filler,target=link_fill[at]
                ignored_edge=target+mapping
                if not size or target>=at+size:
                    continue
            else:
                filler,target=None,None
                independently_entered = (at+mapping in named_or_pointed or
                                         any(source < at or source >= hi
                                             for source,_ in references.get(at+mapping, ())))
                size=size or called_extent(image,at,hi,at+mapping,**framed,table_ranges=tables,
                                           allow_loop_epilogue=independently_entered)
                if not size and at+mapping in direct_calls and at in cuts:
                    size=assembly_extent(image,at,hi,at+mapping,console,order)
                    if size and at+size not in cuts:
                        size=None
            if not size:continue
            end=at+size;address=at+mapping;finish=end+mapping
            preceding=at-4
            # A bogus conditional branch in one alignment word can point into
            # the called leaf and otherwise look like an independent entry.
            if (filler is None and closed_leaf and address in direct_calls
                    and preceding in cuts & returns and (preceding+mapping)%8==4
                    and preceding+mapping not in entered_addresses
                    and not any(d.rom is not None and d.rom<at and preceding<d.rom+d.size for d in data)):
                word=lines[preceding].word
                if word>>26 in (4,5,6,7,20,21,22,23):
                    delta=word&0xffff
                    dest=address+4*(delta-0x10000 if delta&0x8000 else delta)
                    if address<dest<finish:
                        filler,ignored_edge=preceding,dest
            if any(address<a<finish for a in explicit|pointers):continue
            if any(address<=a<finish for a in data_only):continue
            if any(d.rom is not None and d.rom<end and at<d.rom+d.size for d in data):continue
            # Alignment between a return and the next called entry may
            # decode as a backward branch into the return's delay slot.
            index = bisect_left(called_cuts, end)
            following = called_cuts[index] if index < len(called_cuts) else None
            tail = _return_delay_alignment(end, following, mapping, lines, references, named_or_pointed, data)
            # A branch within another occupant targets its own copy of the shared address.
            independent=any(not at<=offset<end
                            for dest in destinations[bisect_right(destinations,address):bisect_right(destinations,finish-1)]
                            for offset,_ in references[dest]
                            if (offset!=filler or dest!=ignored_edge)
                            and not (offset==tail and dest==finish-4)
                            and not any(other.rom_start <= offset < other.rom_end and other.contains(dest, 4)
                                        for other in siblings))
            if independent:continue
            if not any(at<c<end for c in cuts) and at in cuts and end in cuts:continue
            # Retain alignment in the original image, outside the C function.
            if filler is not None:
                padding.add((filler,at))
                cuts.add(at)
            if at in proposed_padding:
                padding.add(proposed_padding[at])
                cuts.update(proposed_padding[at])
            accepted.append((at,end));cuts={c for c in cuts if not at<c<end}|{at,end}
            if tail is not None:
                cuts = {c for c in cuts if not end<c<following}
                padding.add((end, following))
            if end-8 in lines and lines[end-8].word==JR_RA:
                if (following:=seed(end)) is not None:heapq.heappush(pending,following)
        if not accepted:result.extend(group);continue
        names={f.rom:f.name for f in group};by_start={f.rom:f for f in group}
        labels=set().union(*(f.labels for f in group))
        # A proposed cut can land inside an unowned alignment hole. Do not
        # invent an owner for it: retain the original group for review.
        spans = _owned_partition(group, cuts, padding)
        if spans is None:
            log(f'Kept original boundaries near 0x{lo+mapping:08X}: a proposed split crosses unowned alignment.')
            result.extend(group)
            continue
        changed+=len(accepted)
        for start,end in spans:
            source=by_start.get(start) or next(f for f in group if f.rom<=start<f.rom+f.size)
            name=names.get(start,f'func_{start+mapping:08X}')
            body=[replace(lines[at]) for at in range(start,end,4)]
            local={f.name:f.vram for f in group if start<f.rom<end}
            for line in body:
                line.operands=re.sub(r'[\w.]+',lambda m,local=local:f'.L{local[m[0]]:08X}' if m[0] in local else m[0],line.operands)
            owned={a for a in labels if start+mapping<=a<end+mapping}|set(local.values())
            result.append(replace(source,name=name,vram=start+mapping,rom=start,lines=body,labels=owned,piece=source.piece if start==source.rom and not any(a==start and b==end for a,b in accepted) else False))
    if changed:
        # Joining a validated body can expose alignment that the disassembler
        # originally attached to the preceding routine.
        recovered_gaps = multiword_alignment(result, image, order, owner, explicit | pointers, references,
                                             direct_calls, data, segments, console)
        if recovered_gaps:
            result = [fn for fn in result if (fn.rom, fn.vram) not in recovered_gaps]
            log(f'Left {len(recovered_gaps)} newly exposed alignment fragments in the original image.')
        # A label moved to a separate body is now an entry symbol, not a local
        # label in the preceding assembly file. Keep cross-body branches linkable.
        entries={(owner(fn),fn.vram):fn.name for fn in result}
        rewritten=[]
        for fn in result:
            body=[]
            for line in fn.lines:
                def entry(match, fn=fn):
                    address=int(match[1],16)
                    return entries.get((owner(fn),address),match[0]) if not fn.vram<=address<fn.vram+fn.size else match[0]
                body.append(replace(line,operands=_LOCAL_REF.sub(entry,line.operands)))
            rewritten.append(replace(fn,lines=body))
        result=rewritten
    if changed:log(f'Automatically repaired {changed} proven function boundaries; original instruction bytes retained. Uncertain entries still need review.')
    entered=explicit|pointers|set(destinations)
    def referenced(fn):
        return (fn.vram in explicit or fn.vram in pointers or any(offset!=fn.rom for offset,_ in references.get(fn.vram,()))
                or any(d.rom is not None and d.rom<fn.rom+fn.size and fn.rom<d.rom+d.size for d in data))
    # A resplit can give padding its own source unit, so adjacent unit names
    # do not establish whether an isolated nop belongs to an entry.
    returns_by_end = {(fn.rom+fn.size, fn.vram-fn.rom) for fn in result
                      if fn.size >= 8 and fn.lines[-2].word == JR_RA}
    protected = sorted(entered)
    inline_data = set(inline_data)
    entries_by_rom = {(fn.rom, fn.vram):fn for fn in result}

    def zero_alignment(fn, previous):
        if fn.size != 4 or fn.lines[0].word or image[fn.rom:fn.rom+4] != bytes(4):
            return False
        mapping = fn.vram-fn.rom
        for before in range(fn.rom, max(-1, fn.rom-HOLE), -4):
            returned = (before, mapping) in returns_by_end
            looped = previous is not None and closes_with_back_edge(previous, before, mapping)
            exception_fallback = (previous is not None and owner(previous)==owner(fn)
                                  and previous.rom+previous.size==before and previous.size>=16
                                  and previous.lines[-2].word>>26==3
                                  # The zero thread argument can be set before the call or in its delay slot.
                                  and ([line.word for line in previous.lines[-4:-2]]==[0x42000018,0x00002021]
                                       and not previous.lines[-1].word
                                       or previous.lines[-3].word==0x42000018
                                       and previous.lines[-1].word in (0x00002021,0x00002025)))
            if not (returned or looped or exception_fallback) or any(image[before:fn.rom+4]):
                continue
            index = owner(fn)[0]
            limit = segments[index].rom_end if index >= 0 else len(image)
            after = before
            while after < min(limit, len(image)-3, before+LONG_ALIGNMENT+4) and image[after:after+4] == bytes(4):
                after += 4
            following = entries_by_rom.get((after, after+mapping))
            if exception_fallback:
                transfer = previous.lines[-2]
                target = ((transfer.vram+4)&0xf0000000)|((transfer.word&0x3ffffff)<<2)
                if following is None or after-before > HOLE:
                    continue
                adjacent = following.vram == target and not following.vram%16 and is_frame(following.lines[0].word)
                scheduler = (not following.vram%8 and any(is_frame(line.word) for line in following.lines[:3])
                             and calls_current_thread_dispatch(image, target, previous.vram, segments, order))
                # A separate SDK leaf can follow the exception fallback when
                # the thread destroyer lives elsewhere in the same mapping.
                interrupt_leaf = (fn.rom == before and after-before == 4
                                  and following.vram in direct_calls and not following.vram%16
                                  and tuple(line.word for line in following.lines)
                                  == (0x40086000, 0x2401fffe, 0x01014824, 0x40896000,
                                      0x31020001, 0, JR_RA, 0)
                                  and calls_current_thread_dispatch(image, target, previous.vram, segments, order))
                if not (adjacent or scheduler or interrupt_leaf):
                    continue
            if looped and (following is None or following.vram not in direct_calls
                           or after-before != 4 and (after-before > HOLE or following.vram % 16)):
                return False
            if after-before > HOLE:
                # A pointer into a zero run can enter its suffix. Only the prefix before
                # the earliest possible entry is unreachable from that pointer.
                next_is_data = following is None and (after, after+mapping) in inline_data
                next_is_entry = (following is not None and not following.vram%8 and following.vram in direct_calls
                                 and (is_frame(following.lines[0].word)
                                      or assembly_extent(image,following.rom,following.rom+following.size,
                                                         following.vram,console,order,allow_cache=True)==following.size))
                if after-before > LONG_ALIGNMENT or not (next_is_data or next_is_entry):
                    return False
                candidates = b'|'.join(re.escape(addr.to_bytes(4,order))
                                       for addr in range(before+mapping,after+mapping,4))
                if any(not match.start()%4 and int.from_bytes(match[1],order)<=fn.vram
                       for match in re.finditer(b'(?=('+candidates+b'))', image)):
                    return False
            index = bisect_right(protected, before+mapping-1)
            while index<len(protected) and protected[index]<after+mapping:
                if protected[index] not in data_only and (after-before<=HOLE or protected[index]<=fn.vram):
                    return False
                index += 1
            owners = [s for s in segments if s.rom_start <= before-8 < after <= s.rom_end
                      and s.vram-s.rom_start == mapping and s.contains(before+mapping-8, after-before+8)]
            return (len(owners) == 1
                    and not any(d.rom is not None and d.rom < after and before < d.rom+d.size for d in data))
        return False

    # A disassembler can start code in the tail of a double constant. Keep the
    # proof tied to actual typed loads and to the single mapped segment.
    loaded_words = loaded_constant_words(functions)
    recovered_data=[]
    predecessors={(fn.rom+fn.size,fn.vram+fn.size):fn for fn in result if fn.size>=8}
    kept=[];dropped=markers=recovered_shared=recovered_fallthrough=recovered_continuations=shared_delay_entries=return_wrappers=backward_loops=cache_prefixes=saved_stack_tails=0;ordered=sorted(result,key=lambda f:(owner(f),f.rom))
    for i,fn in enumerate(ordered):
        previous=kept[-1] if kept else None;following=ordered[i+1] if i+1<len(ordered) else None
        successor = following if fn.size == 4 else original_entries.get((fn.rom+4, fn.vram+4))
        merged = fn.size > 4
        if (previous is not None and successor is not None and owner(fn)[0] >= 0
                and owner(previous) == owner(fn) == owner(successor)
                and previous.rom+previous.size == fn.rom and previous.vram+previous.size == fn.vram
                and previous.lines[-1].word == JR_RA and not fn.lines[0].word
                and (not merged or successor.size == fn.size-4
                     and [line.word for line in successor.lines] == [line.word for line in fn.lines[1:]])
                and image[fn.rom:fn.rom+4] == bytes(4) and fn.vram in direct_calls
                and any(segments[owner(fn)[0]].rom_start <= source < segments[owner(fn)[0]].rom_end
                        and int.from_bytes(image[source:source+4], order) >> 26 == 3
                        for source, _ in references.get(fn.vram, ()))
                and successor.rom == fn.rom+4 and successor.vram == fn.vram+4
                and is_frame(successor.lines[0].word)
                and not any(d.rom is not None and d.rom < fn.rom+4 and fn.rom < d.rom+d.size for d in data)
                and called_extent(image, successor.rom, successor.rom+successor.size, successor.vram,
                                  big_endian=console.big_endian, cpu=console.cpu_variant,
                                  asm_check=console.asm_reason, table_ranges=tables) == successor.size):
            # A call may enter on the previous return's NOP delay slot and then
            # fall into the next closed function. Keep both addresses: the
            # word completes the return, while the entry remains a symbol.
            kept[-1] = replace(previous, lines=previous.lines+[fn.lines[0]])
            data.append(DataSymbol(fn.name, fn.vram, fn.rom, 0, 'func'))
            if merged:
                kept.append(replace(successor, lines=fn.lines[1:],
                                    labels=successor.labels | {a for a in fn.labels if a >= successor.vram}))
            shared_delay_entries += 1
            continue
        if (previous is not None and owner(fn)[0] >= 0 and owner(previous) == owner(fn)
                and saved_stack_shared_tail(previous, fn, image, order, segments, entered_addresses, data)):
            padding = [AsmLine(previous.rom+previous.size, previous.vram+previous.size, 0, 'nop', '')] if fn.rom > previous.rom+previous.size else []
            kept[-1] = replace(previous, lines=previous.lines+padding+fn.lines,
                               labels=previous.labels | fn.labels,
                               entry_aliases={**previous.entry_aliases, fn.vram:fn.name})
            data.append(DataSymbol(fn.name, fn.vram, fn.rom, 0, 'func'))
            saved_stack_tails += 1
            continue
        if (previous is not None and owner(fn)[0] >= 0 and owner(previous) == owner(fn)
                and previous.rom+previous.size == fn.rom and previous.vram+previous.size == fn.vram
                and previous.lines[0].word & 0xffff0000 == 0x27bd0000
                and previous.lines[0].word & 0x8000 and fn.size >= 8
                and not is_frame(fn.lines[0].word)
                and not any(line.word == JR_RA for line in previous.lines)
                and fn.lines[-2].word == JR_RA
                and fn.lines[-1].word == 0x27bd0000 | ((-previous.lines[0].word) & 0xffff)
                and fn.vram not in entered_addresses and not referenced(fn)
                and not any(d.rom is not None and d.rom < fn.rom+fn.size and previous.rom < d.rom+d.size for d in data)):
            import rabbitizer
            tail = rabbitizer.Instruction(previous.lines[-1].word)
            if (tail.isValid() and not tail.hasDelaySlot()
                    and not rabbitizer.Instruction(fn.lines[0].word).hasDelaySlot()
                    and (len(previous.lines) < 2 or not rabbitizer.Instruction(previous.lines[-2].word).hasDelaySlot())
                    and tail.getOpcodeName() not in ('eret', 'break', 'syscall', 'teq', 'tne', 'tge', 'tgeu', 'tlt', 'tltu')):
                # A framed function falls straight into an unentered continuation
                # that restores its exact frame before returning.
                kept[-1] = replace(previous, lines=previous.lines+fn.lines,
                                   labels=previous.labels | fn.labels,
                                   entry_aliases={**previous.entry_aliases, **fn.entry_aliases})
                recovered_continuations += 1
                continue
        if overlay_end_marker(fn, previous, following, image, order, segments) and not referenced(fn):
            data.append(DataSymbol(fn.name, fn.vram, fn.rom, 4, 'data'))
            markers += 1
            continue
        # A hand-written wrapper temporarily replaces $ra to reuse a separately
        # callable leaf, then returns through the saved register. Preserve both
        # entries and its local continuation when the whole shape is proven.
        wrapper = kept[-2] if len(kept) >= 2 else None
        if (wrapper is not None and owner(wrapper)[0] >= 0 and owner(wrapper) == owner(previous) == owner(fn)
                and wrapper.vram in direct_calls and previous.vram in direct_calls
                and fn.size == 8 and fn.lines[0].word == 0x03200008):
            external = explicit | pointers | {address for address, sources in references.items()
                if any(not wrapper.rom <= src < fn.rom+fn.size for src, _ in sources)}
            if shared_return_wrapper(wrapper, previous, fn, image, console, order, segments, external, data):
                kept[-2] = replace(wrapper, lines=wrapper.lines+previous.lines+fn.lines,
                    labels=wrapper.labels | previous.labels | fn.labels,
                    entry_aliases={**wrapper.entry_aliases, previous.vram:previous.name, fn.vram:fn.name})
                kept.pop()
                data.extend((DataSymbol(previous.name,previous.vram,previous.rom,0,'func'),
                             DataSymbol(fn.name,fn.vram,fn.rom,0,'func')))
                return_wrappers += 1
                continue
        if (previous is not None and owner(previous)[0] >= 0 and owner(previous) == owner(fn)
                and previous.size == 8 and previous.vram not in direct_calls and fn.vram in direct_calls
                and previous.lines[0].word >> 26 in (20,21,22,23)):
            external = explicit | pointers | {address for address, sources in references.items()
                if any(not previous.rom <= src < fn.rom+fn.size for src, _ in sources)}
            if backward_loop_entry(previous,fn,image,console,order,segments,external,data):
                kept[-1] = replace(previous,lines=previous.lines+fn.lines,labels=previous.labels|fn.labels,
                                   entry_aliases={**previous.entry_aliases,fn.vram:fn.name})
                data.append(DataSymbol(fn.name,fn.vram,fn.rom,0,'func'))
                backward_loops += 1
                continue
        if (previous is not None and owner(previous)[0] >= 0 and owner(previous) == owner(fn)
                and previous.vram not in direct_calls and fn.vram in direct_calls
                and 8 <= previous.size <= 64 and fn.size >= 16 and fn.lines[0].word >> 26 == 47):
            external = explicit | pointers | {address for address, sources in references.items()
                if any(not previous.rom <= src < fn.rom+fn.size for src, _ in sources)}
            if cache_fallthrough(previous,fn,image,console,order,segments,external,data):
                kept[-1] = replace(previous,lines=previous.lines+fn.lines,labels=previous.labels|fn.labels,
                                   entry_aliases={**previous.entry_aliases,fn.vram:fn.name})
                data.append(DataSymbol(fn.name,fn.vram,fn.rom,0,'func'))
                cache_prefixes += 1
                continue
        same_owner = previous is not None and owner(previous) == owner(fn)
        possible_fallthrough = (same_owner and 8 <= previous.size <= 128 and fn.lines
                                and previous.rom+previous.size == fn.rom
                                and previous.vram+previous.size == fn.vram and is_frame(fn.lines[0].word))
        both_called = same_owner and previous.vram in direct_calls and fn.vram in direct_calls
        # A single argument adjustment can also be an independently callable
        # prefix to a leaf, even when only the shared entry is called here.
        arg_adjust = (same_owner and previous.size == 4 and previous.lines
                      and previous.lines[0].word >> 26 == 9
                      and 4 <= (previous.lines[0].word >> 21) & 31 <= 7
                      and (previous.lines[0].word >> 21) & 31 == (previous.lines[0].word >> 16) & 31)
        possible_shuffle = same_owner and fn.vram in direct_calls and (previous.size in (8, 12) or arg_adjust)
        external_entries = (explicit | pointers | {address for address, sources in references.items()
            if any(not previous.rom <= src < fn.rom+fn.size for src, _ in sources)}
            if possible_fallthrough or both_called or possible_shuffle else set())
        fallthrough = (possible_fallthrough
                       and shared_fallthrough_frame(previous,fn,image,console,order,segments,external_entries,data))
        if (same_owner
                and (fn.vram in (explicit | pointers)
                     and shared_entry_leaf(previous,fn,image,console,order,segments,entered)
                     or (both_called or possible_shuffle)
                     and shared_called_leaf(previous,fn,image,console,order,segments,external_entries,data)
                     or fallthrough)):
            kept[-1] = replace(previous,lines=previous.lines+fn.lines,
                               labels=previous.labels|fn.labels,
                               entry_aliases={**previous.entry_aliases,fn.vram:fn.name})
            data.append(DataSymbol(fn.name,fn.vram,fn.rom,0,'func'))
            if fallthrough:recovered_fallthrough += 1
            else:recovered_shared += 1
            continue
        predecessor=predecessors.get((fn.rom,fn.vram))
        if (fn.size >= 16 and fn.size % 4 == 0 and owner(fn)[0] >= 0
                and all(line.word in starts for line in fn.lines)
                and image[fn.rom:fn.rom+fn.size] == b''.join(line.word.to_bytes(4, order) for line in fn.lines)
                and not any(fn.vram <= address < fn.vram+fn.size for address in explicit|transfer_targets)
                and not any(d.rom is not None and d.rom < fn.rom+fn.size and fn.rom < d.rom+d.size for d in data)):
            # An array whose every word names a discovered entry is data even
            # when a caller passes its address and splat creates a function.
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if (owner(fn)[0] >= 0 and not any(fn.vram <= address < fn.vram+fn.size
                                          for address in explicit | transfer_targets)
                and not any(d.rom is not None and d.rom < fn.rom+fn.size and fn.rom < d.rom+d.size for d in data)
                and terminated_mapped_addresses(fn, image, order, segments, segments[owner(fn)[0]])):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if (terminal_loaded_record(fn, previous, image, order, segments, functions, references,
                                   lines_by_rom, explicit | pointers | transfer_targets, data)):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if (leading_loaded_constants(fn, following, image, order, segments, loaded_words,
                                     explicit | pointers | transfer_targets, data)):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if (leading_constant_padding(fn, following, image, order, segments, direct_calls, entered-{fn.vram})
                and fn.vram not in explicit|pointers|transfer_targets
                and string_address_references(fn, references, lines_by_rom, reference_bodies)
                and not any(d.rom is not None and d.rom < fn.rom+fn.size and fn.rom < d.rom+d.size for d in data)):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if leading_repeated_double(fn, following, image, order, segments, direct_calls) and not referenced(fn):
            dropped+=1;continue
        if (fn.vram in data_only and predecessor is not None and owner(fn)==owner(predecessor)
                and predecessor.lines[-2].word==JR_RA
                and (not fn.lines[0].word or fn.size==8)):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data'))
            continue
        if (fn.vram not in data_entry_targets and owner(fn)[0]>=0
                and predecessor is not None and owner(fn)==owner(predecessor)
                and default_color_word(fn, predecessor, following, image, order, references, direct_calls)):
            recovered_data.append(DataSymbol(fn.name, fn.vram, fn.rom, 4, 'data'))
            continue
        if ((terminal_data_alignment(fn, predecessor, image, order, segments)
             or terminal_halfword_data_alignment(fn, predecessor, image, order, segments)
             or terminal_descending_halfwords(fn, predecessor, image, order, segments)
             or terminal_memory_ranges(fn, predecessor, image, order, segments)
             or terminal_display_modes(fn, predecessor, image, order, segments)) and not referenced(fn)):
            dropped+=1;continue
        if (fn.size == 4 and previous is not None and following is not None
                and owner(fn)[0] >= 0 and owner(previous) == owner(fn) == owner(following)
                and previous.rom+previous.size == fn.rom and previous.vram+previous.size == fn.vram
                and following.rom == fn.rom+4 and following.vram == fn.vram+4
                and previous.size >= 8 and previous.lines[-2].word == JR_RA
                and fn.lines[0].word == previous.lines[-1].word
                and image[fn.rom:fn.rom+4] == fn.lines[0].word.to_bytes(4, order)
                and following.vram in direct_calls and not referenced(fn)):
            # A duplicate of the return delay slot between the closed function
            # and the independently called next entry is unreachable padding.
            dropped += 1
            continue
        if (fn.size == 12 and previous is not None and following is not None
                and owner(fn)[0] >= 0 and owner(previous) == owner(fn) == owner(following)
                and previous.size == 8 and previous.rom+8 == fn.rom and previous.vram+8 == fn.vram
                and previous.lines[0].word == 0x08000000 | ((previous.vram >> 2) & 0x03ffffff)
                and previous.lines[1].word == 0 and previous.vram in direct_calls
                and following.rom == fn.rom+8 and following.vram == fn.vram+8
                and following.vram in direct_calls and is_frame(following.lines[0].word)
                and fn.lines[-1].word == following.lines[0].word
                and not ({fn.vram, fn.vram+4} & entered_addresses)
                and not any(m.start() % 4 == 0 for m in re.finditer(
                    re.escape((fn.vram+4).to_bytes(4, order)), image))
                and not any(d.rom is not None and d.rom < fn.rom+8 and fn.rom < d.rom+d.size for d in data)
                and image[fn.rom:fn.rom+12] == b''.join(line.word.to_bytes(4, order) for line in fn.lines)):
            import rabbitizer
            if all(rabbitizer.Instruction(line.word).hasDelaySlot() for line in fn.lines[:2]):
                # Two unentered transfer words after a called self-loop cannot be its fallthrough; the
                # second transfer would also be in the first one's delay slot. The called frame overlaps
                # only because the disassembler borrowed its first word as that second transfer's slot.
                dropped += 1
                continue
        if zero_alignment(fn, previous):
            if fn.vram in data_only:
                recovered_data.append(DataSymbol(fn.name,fn.vram,fn.rom,4,'data'))
            else:
                dropped+=1
            continue
        if (fn.size == 4 and not fn.lines[0].word and owner(fn)[0] >= 0
                and segments[owner(fn)[0]].rom_start == fn.rom
                and following is not None and owner(fn) == owner(following)
                and following.rom == fn.rom+4 and following.vram == fn.vram+4
                and following.vram % 16 == 0 and following.vram in direct_calls
                and not referenced(fn)
                and image[fn.rom:fn.rom+4] == bytes(4)
                and leaf_extent(image, following.rom, following.rom+following.size, following.vram,
                                big_endian=console.big_endian, cpu=console.cpu_variant,
                                asm_check=console.asm_reason) == following.size):
            # An unreferenced NOP before a called, aligned leaf can be a
            # mapping's leading alignment, rather than a function of its own.
            dropped += 1
            continue
        if (filler_extent(fn.size,fn.lines[0].word) and following is not None
                and owner(fn)==owner(following) and following.rom==fn.rom+4 and following.vram==fn.vram+4):
            returned = (previous is not None and owner(previous)==owner(fn)
                        and previous.rom+previous.size==fn.rom
                        and previous.size>=8 and previous.lines[-2].word==JR_RA)
            # A separately called entry can follow a return in a piece that has
            # no standalone function record. Confirm the bytes in its mapping.
            if not returned and following.vram in direct_calls and owner(fn)[0]>=0:
                segment = segments[owner(fn)[0]]
                returned = raw_return_before(fn, segment, segments, image, order)
            if not alignment_fill(fn.vram,returned,following.vram in entered or is_frame(following.lines[0].word),
                                  referenced(fn) and fn.vram not in data_only):
                kept.append(fn)
                continue
            if fn.vram in data_only:
                recovered_data.append(DataSymbol(fn.name,fn.vram,fn.rom,4,'data'))
            else:
                dropped+=1
            continue
        kept.append(fn)
    if dropped:
        log(f'Left {dropped} alignment words out of the functions: original bytes kept in the image.')
    if markers:
        log(f'Recovered {markers} overlay end markers as data.')
    if recovered_data:
        data.extend(replace(item, boundary_proven=True) for item in recovered_data)
        log(f'Recovered {len(recovered_data)} referenced words as data between functions.')
    if recovered_shared:
        log(f'Recovered {recovered_shared} closed leaves with independently entered assembly tails.')
    if recovered_fallthrough:
        log(f'Recovered {recovered_fallthrough} straight initializers with preserved framed entries.')
    if cache_prefixes:
        log(f'Joined {cache_prefixes} proven cache-writing fallthroughs, preserving their called entries.')
    if backward_loops:
        log(f'Joined {backward_loops} backward-entered assembly loops, preserving their callable entries.')
    if return_wrappers:
        log(f'Recovered {return_wrappers} shared assembly return wrappers with their entry symbols.')
    if saved_stack_tails:
        log(f'Recovered {saved_stack_tails} saved-stack bodies with their shared entry symbols.')
    if recovered_continuations:
        log(f'Joined {recovered_continuations} unentered framed continuations.')
    if shared_delay_entries:
        log(f'Preserved {shared_delay_entries} called NOP entries shared with a return delay slot.')
    final = kept if dropped or markers or recovered_data or recovered_shared or recovered_fallthrough or recovered_continuations or shared_delay_entries or return_wrappers or backward_loops or cache_prefixes or saved_stack_tails else result if changed else functions
    # Closing called bodies can expose a stale fragment that was initially part
    # of a larger piece. Recheck the frozen result with the same entry guards.
    late_stale = stale_code_copies(final, image, order, console, segments, owner, explicit,
                                   references, direct_calls, data)
    if late_stale:
        # Reimport must not restore the old function solely because its bytes
        # still exist. Keep its original extent and record why it was removed.
        data.extend(DataSymbol(fn.name, fn.vram, fn.rom, fn.size, 'data', boundary_proven=True)
                    for fn in final if (fn.rom, fn.vram) in late_stale)
        final = [fn for fn in final if (fn.rom, fn.vram) not in late_stale]
        log(f'Left {len(late_stale)} additional proven stale code copies in the original image.')
    return _restore_original_delay_slots(final, original_entries, image, order)



def identified_sdk_tail(console, image, tail):
    """Keep separately identifiable SDK routines intact for SDK classification."""
    import hashlib

    code = image[tail.rom:tail.rom+tail.size]
    if (tail.size in console.sdk_assembly_sizes and console.sdk_assembly_identity(code,big_endian=console.big_endian)):
        return True
    lo, hi = console.sdk_shape_sizes
    if lo <= tail.size <= hi and console.sdk_shape_identity(code,big_endian=console.big_endian):
        return True
    return bool(tail.size in console.sdk_linked_sizes
                and console.sdk_linked_identity(code,hashlib.sha1(image).hexdigest()))


def cache_fallthrough(head, tail, image, console, order, segments, entered, data):
    """A global pointer update falls into a separately called cache-writing leaf."""
    import rabbitizer

    from ..arch.mips import _mips_uses, rabbitizer_category
    from ..arch.mips_assets import read

    if (head.piece or tail.piece or not 8 <= head.size <= 64 or tail.size < 16
            or head.rom+head.size != tail.rom or head.vram+head.size != tail.vram
            or head.entry_aliases or tail.entry_aliases
            or sum(segment.contains(head.vram,head.size+tail.size) for segment in segments) != 1
            or any(head.vram <= address < tail.vram+tail.size and address != tail.vram for address in entered)):
        return False
    end = tail.rom+tail.size
    if (end > len(image) or any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data)
            or image[head.rom:end] != b''.join(line.word.to_bytes(4,order) for fn in (head,tail) for line in fn.lines)
            or len(tail.lines) < 2 or any(line.word >> 26 != 47 for line in tail.lines[:2])):
        return False
    if identified_sdk_tail(console,image,tail):
        return False
    for line in head.lines:
        instruction = rabbitizer.Instruction(line.word,line.vram,rabbitizer_category(console.cpu_variant))
        uses = _mips_uses(line.word,console.cpu_variant)
        if (not instruction.isValid() or instruction.hasDelaySlot() or uses is None
                or {29,31} & (uses[0] | uses[1])
                or instruction.getOpcodeName() in ('eret','break','syscall','teq','tne','tge','tgeu','tlt','tltu')):
            return False
    facts = read(head.vram,image[head.rom:tail.rom],big_endian=console.big_endian,cpu=console.cpu_variant)
    addresses = {kind:{event['place']['value'] for event in facts['events']
                       if event.get('kind') == kind and event.get('place',{}).get('kind') == 'constant'
                       and 0x80000000 <= event['place']['value'] < 0x80800000}
                 for kind in ('load','store')}
    if not addresses['load'] & addresses['store']:
        return False
    return (assembly_extent(image,tail.rom,end,tail.vram,console,order,allow_cache=True) == tail.size
            and assembly_extent(image,head.rom,end,head.vram,console,order,allow_cache=True) == head.size+tail.size)


def backward_loop_entry(head, body, image, console, order, segments, entered, data):
    """Keep a callable leaf whose loop branches back to the preceding two words."""
    if (head.piece or body.piece or head.size != 8 or body.size < 16 or head.entry_aliases or body.entry_aliases
            or head.rom+head.size != body.rom or head.vram+head.size != body.vram
            or sum(segment.contains(head.vram,head.size+body.size) for segment in segments) != 1
            or any(head.vram <= address < body.vram+body.size and address != body.vram for address in entered)):
        return False
    end = body.rom+body.size
    if (end > len(image) or any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data)
            or image[head.rom:end] != b''.join(line.word.to_bytes(4,order) for fn in (head,body) for line in fn.lines)):
        return False
    if identified_sdk_tail(console,image,body):
        return False
    branch = head.lines[0].word
    if branch >> 26 not in (20,21,22,23):
        return False
    delta = branch & 0xffff
    target = head.vram+4+(delta-0x10000 if delta & 0x8000 else delta)*4
    if not body.vram <= target < body.vram+body.size:
        return False
    for line in body.lines:
        op = line.word >> 26
        if op in (1,4,5,6,7,20,21,22,23):
            offset = line.word & 0xffff
            destination = line.vram+4+(offset-0x10000 if offset & 0x8000 else offset)*4
            if destination == head.vram:
                break
    else:
        return False
    return assembly_extent(image,head.rom,end,head.vram,console,order) == head.size+body.size


def shared_return_wrapper(head, body, continuation, image, console, order, segments, entered, data):
    """Prove a saved-$ra wrapper, shared leaf and local return continuation."""
    import rabbitizer

    from ..arch.mips import _mips_uses, rabbitizer_category

    if (head.piece or body.piece or continuation.piece or head.size != 12 or body.size < 8
            or continuation.size != 8 or head.rom+head.size != body.rom
            or body.rom+body.size != continuation.rom or head.vram+head.size != body.vram
            or body.vram+body.size != continuation.vram
            or head.entry_aliases or body.entry_aliases or continuation.entry_aliases
            or sum(segment.contains(head.vram,head.size+body.size+continuation.size) for segment in segments) != 1):
        return False
    end = continuation.rom+continuation.size
    if (end > len(image) or any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data)
            or any(head.vram < address < continuation.vram+continuation.size and address != body.vram
                   for address in entered)
            or image[head.rom:end] != b''.join(line.word.to_bytes(4,order)
                                                for fn in (head,body,continuation) for line in fn.lines)):
        return False
    if identified_sdk_tail(console,image,body):
        return False
    first, high, low = (line.word for line in head.lines)
    if first != 0x03e0c825 or high >> 16 != 0x3c1f or low >> 16 != 0x27ff:
        return False
    lo = low & 0xffff
    target = (((high & 0xffff) << 16)+(lo-0x10000 if lo & 0x8000 else lo)) & 0xffffffff
    if target != continuation.vram or body.lines[-2].word != JR_RA or continuation.lines[0].word != 0x03200008:
        return False
    for line in body.lines[:-2] + [body.lines[-1], continuation.lines[-1]]:
        instruction = rabbitizer.Instruction(line.word,line.vram,rabbitizer_category(console.cpu_variant))
        uses = _mips_uses(line.word,console.cpu_variant)
        if (not instruction.isValid() or instruction.hasDelaySlot() or uses is None
                or {25,31} & (uses[0] | uses[1])
                or instruction.getOpcodeName() in ('eret','break','syscall','teq','tne','tge','tgeu','tlt','tltu')):
            return False
    return bool(console.asm_reason(image[head.rom:end]))


def shared_called_leaf(head, tail, image, console, order, segments, entered, data):
    """Keep both called entries when a short prefix falls through into a closed leaf."""
    if (head.size not in (4, 8, 12) or tail.size < 8 or head.rom+head.size != tail.rom
            or head.vram+head.size != tail.vram or head.entry_aliases or tail.entry_aliases
            or any(head.vram < address < head.vram+head.size or tail.vram < address < tail.vram+tail.size
                   for address in entered)):
        return False
    # Longer wrappers must only rearrange the argument registers before
    # falling into the separately callable implementation.
    if head.size > 4 and any(line.word >> 26 != 0 or line.word & 0x7ff != 0x25
                             or not 4 <= (line.word >> 11) & 31 <= 7
                             or (line.word >> 16) & 31 != 0 for line in head.lines):
        return False
    if sum(s.contains(head.vram,head.size+tail.size) for s in segments) != 1:
        return False
    end = tail.rom+tail.size
    if any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data):
        return False
    if image[head.rom:end] != b''.join(line.word.to_bytes(4,order) for fn in (head,tail) for line in fn.lines):
        return False
    if identified_sdk_tail(console,image,tail):
        return False
    options = dict(big_endian=console.big_endian, cpu=console.cpu_variant, asm_check=console.asm_reason)
    if leaf_extent(image,head.rom,end,head.vram,**options) != head.size+tail.size:
        return False
    # A valid independently entered assembly leaf can start with an FPU
    # instruction that is not itself a compiler-like function prologue.
    from ..arch.mips_gaps import extent
    flow = extent(image,tail.rom,end,tail.vram,order,cpu=console.cpu_variant)
    return (flow['direct_flow_closed'] and not flow['step_limit'] and flow['extent_bytes'] == tail.size
            and flow['decoded_instructions']*4 == tail.size)


def shared_fallthrough_frame(head, tail, image, console, order, segments, entered, data):
    """A straight global initializer falls through to a closed framed entry."""
    import rabbitizer

    from ..arch.mips import _mips_uses, rabbitizer_category
    from ..arch.mips_assets import read
    from ..arch.mips_gaps import extent

    if (head.piece or tail.piece or not 8 <= head.size <= 128 or head.rom+head.size != tail.rom
            or head.vram+head.size != tail.vram or head.entry_aliases or tail.entry_aliases
            or any(head.vram < address < head.vram+head.size or tail.vram < address < tail.vram+tail.size
                   for address in entered)
            or sum(segment.contains(head.vram, head.size+tail.size) for segment in segments) != 1):
        return False
    end = tail.rom+tail.size
    if (end > len(image) or not is_frame(tail.lines[0].word)
            or any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data)
            or image[head.rom:end] != b''.join(line.word.to_bytes(4, order) for fn in (head, tail) for line in fn.lines)
            or not console.boundary_reason(image[head.rom:tail.rom])):
        return False
    for line in head.lines:
        ins = rabbitizer.Instruction(line.word, line.vram, rabbitizer_category(console.cpu_variant))
        registers = _mips_uses(line.word, console.cpu_variant)
        if (not ins.isValid() or ins.hasDelaySlot() or registers is None
                or {29, 31} & (registers[0] | registers[1])
                or ins.getOpcodeName() in ('eret', 'break', 'syscall', 'teq', 'tne', 'tge', 'tgeu', 'tlt', 'tltu')):
            return False
    facts = read(head.vram, image[head.rom:tail.rom], big_endian=console.big_endian, cpu=console.cpu_variant)
    stores = {event['place']['value'] for event in facts['events']
              if event.get('kind') == 'store' and (event.get('place') or {}).get('kind') == 'constant'
              and 0x80000000 <= event['place']['value'] < 0x80800000}
    if len(stores) < 2:return False
    if called_extent(image, tail.rom, end, tail.vram, big_endian=console.big_endian,
                     cpu=console.cpu_variant, asm_check=console.asm_reason) != tail.size:
        return False
    flow = extent(image, head.rom, end, head.vram, order, cpu=console.cpu_variant)
    return flow['direct_flow_closed'] and flow['extent_bytes'] == head.size+tail.size


def saved_stack_shared_tail(head, tail, image, order, segments, entered, data):
    """A saved stack frame falls into a separately entered, matching restore sequence."""
    gap = tail.rom-head.rom-head.size
    if (gap not in (0, 4) or tail.vram-head.vram-head.size != gap or head.piece or tail.piece
            or head.entry_aliases or tail.entry_aliases or head.size < 32 or tail.size < 32
            or tail.vram not in entered or any(tail.vram < a < tail.vram+tail.size for a in entered)
            or any(head.vram+head.size <= a < tail.vram for a in entered)
            or image[head.rom+head.size:tail.rom] != bytes(gap)
            or sum(s.contains(head.vram, head.size+gap+tail.size) for s in segments) != 1
            or any(d.rom is not None and d.rom < tail.rom+tail.size and head.rom < d.rom+d.size for d in data)):
        return False
    before = [line.word for line in head.lines]
    after = [line.word for line in tail.lines]
    frame = (-before[0]) & 0xffff
    if (before[0] >> 16 != 0x27bd or not before[0] & 0x8000 or not 8 <= frame <= 0x1000
            or frame % 8 or after[-2:] != [JR_RA, 0x27bd0000 | frame]
            or JR_RA in before or not any(w >> 26 == 0 and w & 63 == 9 for w in before)
            or _has_delay_slot(before[-1]) or before[-1] == 0x42000018
            or before[-1] >> 26 == 0 and before[-1] & 63 in (12, 13)
            or any(_has_delay_slot(w) for w in after[:-2])
            or before[-2] >> 26 == 2 or before[-2] >> 26 == 0 and before[-2] & 63 == 8):
        return False
    # The same saved register and stack offset must be restored, including
    # floating registers. This also distinguishes ordinary stack adjustment.
    def stack_words(words, op):
        return {((w >> 16) & 31, w & 0xffff) for w in words if w >> 26 == op and (w >> 21) & 31 == 29}
    saved = stack_words(before, 43)
    if (not any(reg == 31 for reg, _ in saved)
            or len(saved) < 6 or saved != stack_words(after, 35)
            or any(offset % 4 or offset+4 > frame for _, offset in saved)
            or stack_words(before, 61) != stack_words(after, 53)
            or any(offset % 8 or offset+8 > frame for _, offset in stack_words(before, 61))):
        return False
    # A LUI/store saves SP to a global; the tail reads that same global and
    # installs it as SP before restoring the registers.
    slots = set()
    for a,b in zip(before,before[1:]):
        reg = (a >> 16) & 31
        if a >> 26 == 15 and reg and b >> 26 == 43 and (b >> 21) & 31 == reg and (b >> 16) & 31 == 29:
            slots.add(((a & 0xffff) << 16) + ((b & 0xffff) - (0x10000 if b & 0x8000 else 0)))
    if not slots or len(after) < 6:
        return False
    a,b,c,d = after[:4]
    reg = (a >> 16) & 31
    address = ((a & 0xffff) << 16) + ((b & 0xffff) - (0x10000 if b & 0x8000 else 0))
    return (a >> 26 == 15 and reg and b >> 26 == 35 and (b >> 21) & 31 == reg
            and (b >> 16) & 31 == reg and c == 0 and d == ((reg << 21) | (29 << 11) | 0x21)
            and address in slots)


def shared_entry_leaf(head, tail, image, console, order, segments, entered):
    """Close the first entry while retaining a verified alias at the assembly tail."""
    if (tail.size != 16 or head.size < 16 or head.rom+head.size != tail.rom
            or head.vram+head.size != tail.vram or head.entry_aliases or tail.entry_aliases
            or any(tail.vram < address < tail.vram+tail.size for address in entered)):
        return False
    if sum(s.contains(head.vram,head.size+tail.size) for s in segments) != 1:
        return False
    end = tail.rom+tail.size
    if not console.boundary_reason(image[head.rom:tail.rom]):
        return False
    if assembly_extent(image,tail.rom,end,tail.vram,console,order) != tail.size:
        return False
    size = leaf_extent(image,head.rom,end,head.vram,big_endian=console.big_endian,
                       cpu=console.cpu_variant,asm_check=console.asm_reason)
    return size == head.size+tail.size


def call_targets_delay_slot(word, address, image, order, segments):
    if word >> 26 != 3:
        return False
    target = ((address+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
    offsets = [target-4-s.vram+s.rom_start for s in segments if s.contains(target-4, 8)]
    if len(offsets) != 1 or offsets[0]+8 > len(image):
        return False
    import rabbitizer
    return rabbitizer.Instruction(int.from_bytes(image[offsets[0]:offsets[0]+4], order)).hasDelaySlot()


def call_targets_stack_restore(word, address, image, order, segments):
    """An apparent call enters a return sequence that requires an existing frame."""
    if word >> 26 != 3:
        return False
    target = ((address+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
    owners = [s for s in segments if s.contains(target, 12)]
    if len(owners) != 1:
        return False
    segment = owners[0]
    start = target-segment.vram+segment.rom_start
    end = min(start+40, segment.rom_end, len(image))
    for at in range(start, end-7, 4):
        value = int.from_bytes(image[at:at+4], order)
        if value == JR_RA and at > start:
            pop = int.from_bytes(image[at+4:at+8], order)
            return pop >> 16 == 0x27bd and 8 <= (pop & 0xffff) <= 0x1000 and not pop%8
        if value >> 21 != 0x47d or (value >> 16) & 31 not in (*range(16,24), 30, 31):
            return False
    return False


def split_address_tables_after_return(functions, data, image, order, console, owner, segments, entered):
    """Separate a size-prefixed address record folded between two closed bodies."""
    result = []
    removed = set()
    for fn in functions:
        if owner(fn)[0] < 0:
            result.append(fn)
            continue
        for index in range(4, len(fn.lines)-10):
            call = fn.lines[index].word
            if call>>26 != 3:
                continue
            delay, ret, slot, size = (line.word for line in fn.lines[index+1:index+5])
            if (delay or ret != JR_RA or slot or not 36 <= size <= 256 or size%4
                    or fn.lines[index-2].word != JR_RA):
                continue
            start = fn.rom+index*4
            end = start+size
            entry = fn.vram+index*4
            following = entry+size
            stop = fn.rom+fn.size
            if (end >= stop or following%16
                    or not call_targets_delay_slot(call, entry, image, order, segments)
                    or any(address in entered for address in range(entry, following, 4))
                    or any(symbol.rom is not None and symbol.rom < end and start < symbol.rom+symbol.size
                           for symbol in data)):
                continue
            fmt = '>I' if order=='big' else '<I'
            if any(address & 0xe0000003 != 0x80000000 for address, in struct.iter_unpack(fmt,image[start+20:end])):
                continue
            pointers = b'|'.join(re.escape(address.to_bytes(4,order)) for address in range(entry,following,4))
            if any(not hit.start()%4 for hit in re.finditer(pointers,image)):
                continue
            options = dict(big_endian=console.big_endian, cpu=console.cpu_variant, asm_check=console.asm_reason)
            if (assembly_extent(image,fn.rom,start,fn.vram,console,order) != start-fn.rom
                    or leaf_extent(image,end,stop,following,**options) != stop-end):
                continue
            removed.update(range(start,end,4))
            result.append(replace(fn, lines=fn.lines[:index], labels={label for label in fn.labels if label < entry}))
            result.append(replace(fn, name=f'func_{following:08X}', vram=following, rom=end,
                                  lines=fn.lines[(end-fn.rom)//4:],
                                  labels={label for label in fn.labels if label >= following}))
            break
        else:
            result.append(fn)
    return result, removed


def split_aligned_frames(functions, data, image, order, console, owner, segments, entered):
    """Separate short alignment folded into a validated following frame."""
    result = []
    for fn in functions:
        segment_index = owner(fn)[0]
        if segment_index < 0 or fn.rom-8 < segments[segment_index].rom_start or fn.size < 24 or fn.vram in entered:
            result.append(fn)
            continue
        size = -fn.vram % 16
        start = fn.rom+size
        if (size not in (4, 8, 12) or not is_frame(fn.lines[size//4].word)
                or size == 4 and not (call_targets_delay_slot(fn.lines[0].word,fn.vram,image,order,segments)
                                      or call_targets_stack_restore(fn.lines[0].word,fn.vram,image,order,segments))
                or not any(image[at:at+4] == JR_RA.to_bytes(4,order) and not any(image[at+8:fn.rom])
                           for at in range(max(segments[segment_index].rom_start, start-20), fn.rom-7, 4))
                or any(address in entered for address in range(fn.vram, fn.vram+size, 4))
                or any(d.rom is not None and d.rom < start and fn.rom < d.rom+d.size for d in data)
                or not any(line.word >> 26 == 3 for line in fn.lines[:size//4])
                or called_extent(image, start, fn.rom+fn.size, fn.vram+size,
                                 big_endian=console.big_endian, cpu=console.cpu_variant,
                                 asm_check=console.asm_reason) != fn.size-size):
            result.append(fn)
            continue
        split = fn.vram+size
        head, tail = fn.lines[:size//4], fn.lines[size//4:]
        result.append(replace(fn, lines=head, labels={label for label in fn.labels if label < split}))
        result.append(replace(fn, name=f'func_{split:08X}', vram=split, rom=start, lines=tail,
                              labels={label for label in fn.labels if label >= split}))
    return result


def split_alignment_after_leaf(functions, data, image, order, console, owner, segments, entered):
    """Expose filler included in the previous record before a validated aligned leaf."""
    starts = {(fn.rom, fn.vram):fn for fn in functions}
    result = []
    for fn in functions:
        end = fn.rom+fn.size
        address = fn.vram+fn.size
        # A word that decodes as JAL can borrow the leaf's first instruction
        # as its delay slot, resulting in a one-word overlap.
        overlap = address%16 == 4
        if overlap:
            end -= 4
            address -= 4
        following = starts.get((end,address))
        split = (end-fn.rom)//4
        if (address%16 or split<5 or following is None or owner(fn)[0]<0 or owner(fn)!=owner(following)
                or fn.lines[split-4].word != JR_RA
                or not call_targets_stack_restore(fn.lines[split-1].word,address-4,image,order,segments)
                or any(value in entered for value in (address-8,address-4))
                or any(symbol.rom is not None and symbol.rom < end and end-8 < symbol.rom+symbol.size for symbol in data)
                or leaf_extent(image,end,end+following.size,address,big_endian=console.big_endian,
                               cpu=console.cpu_variant,asm_check=console.asm_reason)!=following.size):
            result.append(fn)
            continue
        result.append(replace(fn,lines=fn.lines[:split-2],labels={label for label in fn.labels if label<address-8}))
        result.append(replace(fn,name=f'func_{address-8:08X}',vram=address-8,rom=end-8,lines=fn.lines[split-2:],labels=set()))
    return result


def _return_delay_alignment(end, following, mapping, lines, references, named, data):
    """A branch from unentered alignment can imitate an entry into a stack pop."""
    if (following is None or not 8 <= following-end <= 28 or (following+mapping)%32
            or end-8 not in lines or lines[end-8].word != JR_RA
            or lines[end-4].word >> 16 != 0x27bd or not 0 < lines[end-4].word & 0xffff < 0x8000
            or any(symbol.rom is not None and symbol.rom < following and end < symbol.rom+symbol.size
                   for symbol in data)):
        return None
    branch = None
    for offset in range(end, following, 4):
        if (offset+mapping in named or any(not end <= source < following
                                          for source, _ in references.get(offset+mapping, ()))):
            return None
        word = lines[offset].word
        if word & 0xfc1fffff == 8:
            return None
        if word>>26 not in (4, 5, 6, 7, 20, 21, 22, 23):
            continue
        delta = (word & 0xffff) - 0x10000
        if branch is not None or not word & 0x8000 or offset+4+4*delta != end-4 or offset+8 > following:
            return None
        if lines[offset+4].word:
            return None
        branch = offset
    return branch


def multiword_alignment(functions, image, order, owner, named, references, direct_calls, data, segments, console):
    """Find short unreachable tails before aligned code entries."""
    gaps = set()
    entries = {(fn.rom, fn.vram): fn for fn in functions}
    for following in functions:
        if following.vram % 16 or owner(following)[0] < 0:
            continue
        segment = segments[owner(following)[0]]
        sizes = (4, 8, 12)
        # Some objects align directly called entries to 32 bytes. Their
        # unreachable suffix can look like a standalone loop to the splitter.
        if following.vram % 32 == 0 and following.vram in direct_calls:
            sizes += (16, 20, 24, 28)
            # A reserved zero-filled area can follow a few leftover alignment
            # words. The same return and incoming-reference checks still apply.
            zero_start = following.rom
            floor = max(segment.rom_start, zero_start-128)
            while zero_start > floor and image[zero_start-4:zero_start] == bytes(4):
                zero_start -= 4
            zero_bytes = following.rom-zero_start
            if zero_bytes >= 64:
                sizes += tuple(range(zero_bytes, zero_bytes+29, 4))
        for size in sizes:
            start = following.rom - size
            address = following.vram - size
            if start - 8 < segment.rom_start or int.from_bytes(image[start-8:start-4], order) != JR_RA:
                continue
            if size == 4:
                # An apparent backward branch in one filler word can falsely
                # make the preceding routine look as if it has a second entry.
                word = int.from_bytes(image[start:start+4], order)
                backward_branch = (following.vram in direct_calls and word>>26 in (4,5,6,7,20,21,22,23)
                                   and word & 0x8000)
                discarded_call = (is_frame(following.lines[0].word)
                                  and (call_targets_delay_slot(word,address,image,order,segments)
                                       or call_targets_stack_restore(word,address,image,order,segments)))
                if not (backward_branch or discarded_call):
                    continue
            # Filler may decode as a branch into its own gap. Only incoming
            # references from outside the gap establish a possible entry.
            if any(dest in named or any(not start <= source < following.rom
                                        for source, _ in references.get(dest, ()))
                   for dest in range(address, following.vram, 4)):
                continue
            if any(d.rom is not None and d.rom < following.rom and start < d.rom+d.size for d in data):
                continue
            fragments = [entries[(start+offset, address+offset)] for offset in range(0, size, 4)
                         if (start+offset, address+offset) in entries]
            if not fragments:
                continue
            if following.vram not in direct_calls and not is_frame(following.lines[0].word):
                options = dict(big_endian=console.big_endian, cpu=console.cpu_variant, asm_check=console.asm_reason)
                args = (image, following.rom, following.rom + following.size, following.vram)
                if (called_extent(*args, **options) != following.size
                        and leaf_extent(*args, **options) != following.size):
                    continue
            if any(owner(fn) != owner(following) or fn.rom+fn.size > following.rom+4
                   or any(line.word & 0xfc1fffff == 8 for line in fn.lines)
                   for fn in fragments):
                continue
            gaps.update((fn.rom, fn.vram) for fn in fragments)
    for tail in functions:
        index, _ = owner(tail)
        if index < 0 or tail.size not in (4, 8, 12):
            continue
        segment = segments[index]
        end = tail.rom + tail.size
        if (end != segment.rom_end or (tail.vram + tail.size) % 16 or end + 64 > len(image)
                or any(image[end:end+64]) or any(s.rom_start < end+64 and end < s.rom_end for s in segments)):
            continue
        if tail.rom - 8 < segment.rom_start or int.from_bytes(image[tail.rom-8:tail.rom-4], order) != JR_RA:
            continue
        if any(address in named or any(not tail.rom <= source < end
                                      for source, _ in references.get(address, ()))
               for address in range(tail.vram, tail.vram + tail.size, 4)):
            continue
        if any(symbol.rom is not None and symbol.rom < end and tail.rom < symbol.rom + symbol.size
               for symbol in data):
            continue
        if any(line.word & 0xfc1fffff == 8 for line in tail.lines):
            continue
        # A prolonged zero run outside the code mapping corroborates the
        # alignment. Keep the unentered suffix in the original rebuild bytes.
        gaps.add((tail.rom, tail.vram))
    return gaps


def assembly_extent(image, start, end, vram, console, order, *, allow_cache=False):
    """A closed assembly body can span generated fragments even without a C-compatible frame."""
    from ..arch.mips_gaps import extent

    end = min(end,start+4096)
    flow = extent(image,start,end,vram,order,console.cpu_variant)
    size = flow['extent_bytes']
    if (not flow['direct_flow_closed'] or flow['step_limit'] or size<16
            or flow['decoded_instructions']*4!=size or image[start+size-8:start+size-4]!=JR_RA.to_bytes(4,order)):
        return None
    body = image[start:start+size]
    # ERET resumes the saved exception context; the following bytes can be
    # a separate cleanup entry even when a simple direct-flow walk continues.
    words = [word for word, in struct.iter_unpack('>I' if order=='big' else '<I',body)]
    if 0x42000018 in words:
        return None
    # The SDK cache routines have multiple closed exits and use the cache
    # instruction even where the console's whole-function classification is unset.
    cache = allow_cache and any(word>>26==47 for word in words)
    return size if cache or console.asm_reason(body) else None


def recover_segment_switch_data(functions, data, image, order, tables, protected, references, log):
    """Recover a segment prefix whose data precedes a proven switch table."""
    from ..arch.mips_indirect import jump_table
    from .split import DataSymbol

    starts = {(mapping['rom_start'], mapping['vram']) for mapping in tables}
    entries = sorted(protected)
    removed = set()
    for fn in functions:
        if ((fn.rom, fn.vram) not in starts or not 20<=fn.size<=1088
                or any(d.rom is not None and d.rom<fn.rom+fn.size and fn.rom<d.rom+d.size for d in data)):
            continue
        index = bisect_right(entries,fn.vram-1)
        if index<len(entries) and entries[index]<fn.vram+fn.size:
            continue
        raw = image[fn.rom:fn.rom+fn.size]
        string_prefix = bool(re.match(rb'[ -~]{4,60}\x00', raw))
        constant_prefix = raw[:16] == (0x30000000).to_bytes(4, order) + bytes(12)
        if not (string_prefix or constant_prefix):
            continue
        offsets = {offset for offset,_ in references.get(fn.vram,())}
        for consumer in functions:
            if not constant_prefix and not any(consumer.rom<=offset<consumer.rom+consumer.size for offset in offsets):
                continue
            for line in consumer.lines:
                if line.word==JR_RA or line.word&0xfc1fffff!=8:
                    continue
                table = jump_table(image,consumer.rom,consumer.rom+consumer.size,consumer.vram,
                                   line.rom,order,tables,allow_saved_base=True)
                if not table or table['state']!='validated':
                    continue
                start = table['rom_offset']-fn.rom
                prefix_ok = (string_prefix and re.fullmatch(rb'[ -~]{4,60}\x00+', raw[:start])
                             or constant_prefix and start == 16)
                if not (0<start<fn.size and start+table['entry_count']*4==fn.size and prefix_ok):
                    continue
                data.append(DataSymbol(fn.name,fn.vram,fn.rom,start,'data',boundary_proven=True))
                data.append(DataSymbol(f"jtbl_{table['table_address']:08X}",table['table_address'],
                                       table['rom_offset'],fn.size-start,'jtbl',boundary_proven=True))
                removed.add(id(fn))
                break
            if id(fn) in removed:
                break
    if removed:
        log(f'Recovered {len(removed)} segment prefixes containing validated switch tables.')
        return [fn for fn in functions if id(fn) not in removed]
    return functions


def closes_with_back_edge(fn, end, mapping):
    if fn.rom+fn.size != end or fn.vram-fn.rom != mapping or fn.size < 12:
        return False
    branch = fn.lines[-2]
    # An unconditional local branch and its delay slot cannot fall into alignment.
    if branch.word>>16 != 0x1000:
        return False
    delta = branch.word & 0xffff
    target = branch.vram+4+(delta-0x10000 if delta&0x8000 else delta)*4
    return fn.vram <= target < branch.vram


def terminated_mapped_addresses(fn, image, order, segments, owner):
    """Recognize a padded, ordered table of addresses into one other code bank."""
    import rabbitizer

    if not 16 <= fn.size <= 128 or fn.size % 4 or fn.rom+fn.size+8 > len(image):
        return False
    words = [line.word for line in fn.lines]
    if (len(words)*4 != fn.size or words != sorted(words) or len(set(words)) < 3
            or any(word % 4 or not 0x80000400 <= word < 0x80800000 for word in words)
            or image[fn.rom:fn.rom+fn.size] != b''.join(word.to_bytes(4, order) for word in words)
            or any(image[fn.rom+fn.size:fn.rom+fn.size+8])):
        return False
    returned = any(image[at:at+4] == JR_RA.to_bytes(4, order)
                   and (slot := rabbitizer.Instruction(int.from_bytes(image[at+4:at+8], order))).isValid()
                   and not slot.hasDelaySlot() and not any(image[at+8:fn.rom])
                   for at in range(max(owner.rom_start, fn.rom-64), fn.rom-7, 4))
    if not returned:
        return False
    targets = [[segment for segment in segments if segment is not owner and segment.contains(word, 4)]
               for word in words]
    if any(len(matches) != 1 for matches in targets) or len({id(matches[0]) for matches in targets}) != 1:
        return False
    segment = targets[0][0]
    return sum(bool(any(image[segment.rom_start+word-segment.vram:segment.rom_start+word-segment.vram+4]))
               for word in set(words)) >= 3


def raw_return_before(fn, segment, segments, image, order):
    import rabbitizer

    if (segment.rom_start>fn.rom-8 or image[fn.rom-8:fn.rom-4]!=JR_RA.to_bytes(4,order)
            or any(s is not segment and s.contains(fn.vram-8,12) for s in segments)):
        return False
    slot = rabbitizer.Instruction(int.from_bytes(image[fn.rom-4:fn.rom],order))
    return slot.isValid() and not slot.hasDelaySlot()


def buffer_write_evidence(functions, image, console, *, check):
    """Prove referenced address producers reach a direct straight-line buffer writer."""
    from functools import lru_cache

    import rabbitizer

    from ..arch.mips import rabbitizer_category
    from ..arch.mips_assets import read

    order='big' if console.big_endian else 'little'
    targets=defaultdict(list)
    bodies={}
    for fn in functions:
        targets[fn.vram].append(fn)
        bodies[fn.rom]=fn

    @lru_cache(maxsize=64)
    def trace(rom):
        fn=bodies[rom]
        return read(fn.vram,image[rom:rom+fn.size],big_endian=console.big_endian,
                    cpu=console.cpu_variant,check=check)

    @lru_cache(maxsize=128)
    def writers(address):
        candidates=targets.get(address,())
        if len(candidates)!=1:
            return set()
        fn=candidates[0]
        if (not 8<=fn.size<=64 or fn.lines[-2].word!=JR_RA
                or image[fn.rom:fn.rom+fn.size]!=b''.join(line.word.to_bytes(4,order) for line in fn.lines)):
            return set()
        category=rabbitizer_category(console.cpu_variant)
        if any((not ins.isValid() or ins.hasDelaySlot()) for ins in
               (rabbitizer.Instruction(line.word,0,category) for line in (*fn.lines[:-2],fn.lines[-1]))):
            return set()
        return {event['place']['value'] for event in trace(fn.rom)['events']
                if event['kind']=='store' and event['place'] and event['place']['kind']=='argument'}

    def verified(fn, references, referrers):
        offsets={offset for offset,_ in references.get(fn.vram,())}
        if not offsets or not offsets<=referrers.keys():
            return False
        covered=set()
        for rom in {referrers[offset].rom for offset in offsets}:
            caller=bodies[rom]
            delta=caller.vram-rom
            for event in trace(rom)['events']:
                if (event['kind']!='call'
                        or int.from_bytes(image[event['instruction']-delta:event['instruction']-delta+4],
                                          order)>>26!=3):
                    continue
                for i in writers(event['target']):
                    arg=event['args'][i]
                    if arg and arg['kind']=='constant' and arg['value']==fn.vram:
                        covered.update(at-delta for at in arg['evidence'])
        return offsets<=covered

    return verified


def address_word_references(fn, references, lines):
    """Every symbolic use must be a matching LUI or a typed memory access."""
    has_high = has_access = False
    pattern = re.compile(r'%((?:hi|lo))\(' + re.escape(fn.name) + r'(?: \+ (0x[0-9a-fA-F]+))?\)')
    for offset,_ in references.get(fn.vram,()):
        line = lines.get(offset)
        symbol = line and pattern.search(line.operands)
        if not symbol:
            return False
        part, addend = symbol.groups()
        shift = int(addend, 16) if addend else 0
        # A zero base can label an array of globals. Keep the proof tied to
        # an exact access to the base even when other references use a member.
        if shift and (fn.lines[0].word or shift >= 64):
            return False
        address = fn.vram + shift
        opcode = line.word>>26
        immediate = line.word&0xffff
        if part=='hi' and opcode==15 and immediate==((address+0x8000)>>16)&0xffff:
            has_high |= not shift
        elif part=='lo' and opcode in (32,33,35,36,37,40,41,43,49,57) and immediate==address&0xffff:
            has_access |= not shift
        else:
            return False
    return has_high and has_access


def default_color_word(fn, previous, following, image, order, references, direct_calls):
    """A default RGBA word stored in a record before the next callable body."""
    if (fn.size!=4 or fn.lines[0].word!=0xff or (fn.vram+4)%16
            or previous.lines[-2].word!=JR_RA or following is None
            or following.vram not in direct_calls or following.rom!=fn.rom+4
            or following.vram!=fn.vram+4):
        return False
    uses = {at for at,_ in references.get(fn.vram, ())}
    if len(uses)!=2:
        return False
    start, low = sorted(uses)
    if low!=start+4 or start+28>len(image):
        return False
    hi, lo, first, second, third, fourth, assignment = struct.unpack_from(
        '>7I' if order=='big' else '<7I',image,start)
    if hi>>21!=0x1e0:
        return False
    value = hi>>16&31
    base = first>>21&31
    upper = ((fn.vram+0x8000)>>16)&0xffff
    return (value!=0 and base!=0 and hi&0xffff==upper
            and lo==(0x24000000|value<<21|value<<16|fn.vram&0xffff)
            and (first,second,third,fourth,assignment)
            == tuple(op|base<<21 for op in (0xa400000c,0xac000008,0xa4000014,0xa4000016,
                                            0xac000004|value<<16)))


def terminal_display_modes(fn, previous, image, order, segments):
    """An all-ones marker and a zero before a table of display modes."""
    end = fn.rom+8
    if (fn.size!=4 or fn.lines[0].word!=0xffffffff or (fn.vram+8)%16
            or previous is None or previous.size<8 or previous.lines[-2].word!=JR_RA
            or previous.rom+previous.size!=fn.rom or previous.vram+previous.size!=fn.vram
            or not 8<=fn.rom<end+48<=len(image) or any(image[fn.rom+4:end])):
        return False
    owners = [s for s in segments if s.rom_start<=fn.rom-8 and s.rom_end==end
              and s.vram-s.rom_start==fn.vram-fn.rom]
    if len(owners)!=1 or any(s.rom_start<end+48 and end<s.rom_end for s in segments):
        return False
    rows = tuple(struct.iter_unpack('>4H' if order=='big' else '<4H',image[end:end+48]))
    return (len({height for _,height,_,_ in rows})>=3
            and all(width==640 and height in (240,480,720) and field==511 and not reserved
                    for width,height,field,reserved in rows))


def leading_repeated_double(fn, following, image, order, segments, calls):
    """Recognize the last half of a repeated double where a split starts early."""
    start = fn.rom
    if (fn.size != 8 or start < 12 or start%8 != 4 or following is None
            or following.rom != start+12 or following.vram != fn.vram+12
            or following.vram%16 or following.vram not in calls
            or not is_frame(following.lines[0].word)
            or fn.lines[0].word>>26 not in (4,5,6,7,20,21,22,23)
            or any(image[start+4:start+12])):
        return False
    if not any(s.rom_start == start and s.vram == fn.vram
               and following.rom+following.size <= s.rom_end for s in segments):
        return False
    pair = image[start-4:start+4]
    if image[start-12:start-4] != pair:
        return False
    value, = struct.unpack('>d' if order == 'big' else '<d', pair)
    return 0.0001 <= abs(value) <= 1_000_000




def string_address_references(fn, references, lines, bodies):
    """Allow address materialization of a short string, but never an indirect jump through its register."""
    uses = {offset for offset,_ in references.get(fn.vram, ())}
    while uses:
        start = min(uses)
        first, second = lines.get(start), lines.get(start+4)
        if first is None or second is None or start+4 not in uses:
            return False
        reg = (first.word>>16)&31
        if (not reg or first.word>>26 != 15 or first.word&0xffff != ((fn.vram+0x8000)>>16)&0xffff
                or second.word != (0x24000000 | reg<<21 | reg<<16 | fn.vram&0xffff)):
            return False
        body = bodies.get(start)
        if body is None or any(line.word>>26 == 0 and (line.word&63) in (8,9)
                               and (line.word>>21)&31 == reg for line in body.lines):
            return False
        uses.difference_update((start,start+4))
    return True

def _stale_next_frame(fn):
    """Accept a frame scheduled after a global load as well as at entry."""
    if is_frame(fn.lines[0].word):
        return True
    if len(fn.lines) < 3 or not is_frame(fn.lines[2].word):
        return False
    first, second = (line.word for line in fn.lines[:2])
    reg = (first >> 16) & 31
    # This straight-line prelude cannot enter the copied fragment or depend on
    # its registers. The function's own frame follows the global load.
    return (first >> 26 == 15 and reg not in (0, 29, 31)
            and second >> 26 == 35 and (second >> 21) & 31 == reg and (second >> 16) & 31 == reg)


def stale_code_copies(functions, image, order, console, segments, owner, explicit, references, calls, data):
    """Find unentered stale copies of another called function between a return and the next frame."""
    ordered = sorted(functions, key=lambda fn: (owner(fn), fn.rom))
    stale = set()
    closed = {}
    named = _code_table_entries(functions, image, order, segments)
    for index in range(1, len(ordered)-1):
        before, fn, after = ordered[index-1:index+2]
        if (not 32 <= fn.size <= 64 or fn.size % 4 or owner(fn)[0] < 0
                or owner(before) != owner(fn) or owner(after) != owner(fn)
                or before.rom + before.size != fn.rom or before.vram + before.size != fn.vram
                or before.size < 8 or before.lines[-2].word != JR_RA
                or fn.rom + fn.size != after.rom or fn.vram + fn.size != after.vram
                or after.vram not in calls or not _stale_next_frame(after)
                or not any(not fn.rom <= src < fn.rom+fn.size
                           and int.from_bytes(image[src:src+4], order) >> 26 == 3
                           for src, _ in references.get(after.vram, ()))
                or any(fn.vram <= address < fn.vram+fn.size for address in explicit)
                or any(d.rom is not None and d.rom < fn.rom+fn.size and fn.rom < d.rom+d.size for d in data)
                or image[fn.rom:fn.rom+fn.size] != b''.join(line.word.to_bytes(4, order) for line in fn.lines)):
            continue
        if any(any(not fn.rom <= source < fn.rom+fn.size for source, _ in references.get(address, ()))
               for address in range(fn.vram, fn.vram+fn.size, 4)):
            continue
        if any(not match.start() % 4 for address in range(fn.vram, fn.vram+fn.size, 4)
               for match in re.finditer(re.escape(address.to_bytes(4, order)), image)):
            continue
        index_owner = owner(fn)[0]
        segment = segments[index_owner]
        if sum(s.contains(fn.vram, fn.size) for s in segments) != 1:
            continue
        if not is_frame(after.lines[0].word):
            # A delayed frame is useful evidence only when the called entry's
            # entire extent closes independently of the fragment before it.
            length = called_extent(image, after.rom, min(segment.rom_end, after.rom+0x4000),
                                   after.vram, big_endian=console.big_endian,
                                   cpu=console.cpu_variant, asm_check=console.asm_reason)
            if length != after.size:
                continue
        for other in ordered:
            if (other is fn or owner(other) != owner(fn) or abs(other.rom-fn.rom) > 0x800
                    or other.vram not in named and (other.vram not in calls
                        or not any(not fn.rom <= src < fn.rom+fn.size
                                   and int.from_bytes(image[src:src+4], order) >> 26 == 3
                                   for src, _ in references.get(other.vram, ())))):
                continue
            key = (other.rom, other.vram)
            if key not in closed:
                closed[key] = called_extent(image, other.rom, min(segment.rom_end, other.rom+0x4000),
                                            other.vram, big_endian=console.big_endian,
                                            cpu=console.cpu_variant, asm_check=console.asm_reason)
            length = closed[key]
            if not length or length < fn.size:
                continue
            for at in range(other.rom, other.rom+length-fn.size+1, 4):
                if _same_stale_instructions(image, fn.rom, at, fn.size, order):
                    stale.add((fn.rom, fn.vram))
                    break
            if (fn.rom, fn.vram) in stale:
                break
    return stale


def distant_stale_fragments(functions, image, order, console, segments, owner, explicit, references, calls, data):
    """An unentered fragment exactly repeats an interior of another complete, called body."""
    ordered = sorted(functions, key=lambda fn: (owner(fn), fn.rom))
    stale = set()
    for index in range(1, len(ordered)-1):
        before, fn, after = ordered[index-1:index+2]
        gap = fn.rom-before.rom-before.size
        if (not 32 <= fn.size <= 64 or fn.size % 4 or not 0 <= gap <= 16 or gap % 4
                or owner(fn)[0] < 0 or owner(before) != owner(fn) or owner(after) != owner(fn)
                or before.vram+before.size+gap != fn.vram or before.size < 8 or before.lines[-2].word != JR_RA
                or any(image[before.rom+before.size:fn.rom]) or fn.rom+fn.size != after.rom
                or fn.vram+fn.size != after.vram or after.vram not in calls or not is_frame(after.lines[0].word)
                or not any(not fn.rom <= src < fn.rom+fn.size and int.from_bytes(image[src:src+4], order) >> 26 == 3
                           for src, _ in references.get(after.vram, ()))
                or sum(s.contains(fn.vram, fn.size) for s in segments) != 1
                or any(d.rom is not None and d.rom < fn.rom+fn.size and fn.rom < d.rom+d.size for d in data)):
            continue
        lo = fn.vram-gap
        if (any(lo <= address < fn.vram+fn.size for address in explicit)
                or any(any(not fn.rom <= source < fn.rom+fn.size for source, _ in references.get(address, ()))
                       for address in range(lo, fn.vram+fn.size, 4))
                or any(not match.start() % 4 for address in range(lo, fn.vram+fn.size, 4)
                       for match in re.finditer(re.escape(address.to_bytes(4, order)), image))):
            continue
        segment = segments[owner(after)[0]]
        if called_extent(image, after.rom, min(segment.rom_end, after.rom+0x4000), after.vram,
                         big_endian=console.big_endian, cpu=console.cpu_variant,
                         asm_check=console.asm_reason) != after.size:
            continue
        source = image[fn.rom:fn.rom+fn.size]
        if source != b''.join(line.word.to_bytes(4, order) for line in fn.lines):
            continue
        hits = [m.start() for m in re.finditer(b'(?=('+re.escape(source)+b'))', image) if not m.start() % 4]
        if len(hits) != 2 or fn.rom not in hits:
            continue
        donor = hits[0] if hits[1] == fn.rom else hits[1]
        bodies = [other for other in functions if other is not fn and other.rom < donor
                  and donor+fn.size <= other.rom+other.size and other.vram in calls and owner(other)[0] >= 0]
        if len(bodies) != 1:
            continue
        body = bodies[0]
        segment = segments[owner(body)[0]]
        if (not any(not fn.rom <= src < fn.rom+fn.size and not body.rom <= src < body.rom+body.size
                    and int.from_bytes(image[src:src+4], order) >> 26 == 3 for src, _ in references.get(body.vram, ()))
                or sum(s.contains(body.vram, body.size) for s in segments) != 1
                or called_extent(image, body.rom, min(segment.rom_end, body.rom+0x4000), body.vram,
                                 big_endian=console.big_endian, cpu=console.cpu_variant,
                                 asm_check=console.asm_reason) != body.size):
            continue
        stale.add((fn.rom, fn.vram))
    return stale


def _code_table_entries(functions, image, order, segments):
    """Code starts named by a contiguous table outside executable mappings."""
    starts = {fn.vram for fn in functions}
    fmt = '>I' if order == 'big' else '<I'
    result, run = set(), []
    def finish():
        if len(run) >= 3 and len(set(run)) >= 3:
            result.update(run)
        run.clear()
    for index, (word,) in enumerate(struct.iter_unpack(fmt, image[:len(image)//4*4])):
        offset = index*4
        if word in starts and not any(s.rom_start <= offset < s.rom_end for s in segments):
            run.append(word)
        elif run:
            finish()
    finish()
    return result


def _same_stale_instructions(image, first, second, size, order):
    """Accept exact copies or one consistent relocation, retaining register and instruction shapes."""
    same = 0
    delta = None
    for offset in range(0, size, 4):
        a = int.from_bytes(image[first+offset:first+offset+4], order)
        b = int.from_bytes(image[second+offset:second+offset+4], order)
        if a == b:
            same += 1
            continue
        op = a >> 26
        if op in (2, 3) and b >> 26 == op:
            change = ((b & 0x3ffffff) - (a & 0x3ffffff))*4
        elif (op in (9, 32, 33, 35, 36, 37, 40, 41, 43, 49, 53, 57, 61)
              and a >> 16 == b >> 16 and (a >> 21) & 31 not in (0, 29, 30, 31)):
            change = (b & 0xffff) - (a & 0xffff)
        else:
            return False
        if not change or abs(change) > 0x10000 or change % 4 or delta is not None and change != delta:
            return False
        delta = change
    return same >= 6


def terminal_loaded_record(fn, previous, image, order, segments, functions, references, lines, entered, data):
    """A loaded pointer and an adjacent address passed in a stack record, after a closed overlay."""
    if (fn.size != 8 or previous is None or previous.size < 8
            or previous.lines[-2].word != JR_RA or previous.rom + previous.size + 4 != fn.rom
            or previous.vram + previous.size + 4 != fn.vram
            or image[fn.rom - 4:fn.rom] != bytes(4)
            or image[fn.rom:fn.rom + 8] != b''.join(line.word.to_bytes(4, order) for line in fn.lines)
            or not 0x80000000 <= fn.lines[0].word < 0x80800000 or fn.lines[0].word % 4
            or not 0 < fn.lines[1].word <= 0x10000
            or any(fn.vram <= addr < fn.vram + 8 for addr in entered)
            or any(d.rom is not None and d.rom < fn.rom + 8 and fn.rom < d.rom + d.size for d in data)
            or not address_word_references(fn, references, lines)
            or not any(lines[offset].word >> 26 == 35 for offset, _ in references.get(fn.vram, ())
                       if offset in lines)):
        return False
    owners = [s for s in segments if s.rom_start <= previous.rom and s.rom_end == fn.rom + 8
              and s.vram - s.rom_start == fn.vram - fn.rom and s.contains(fn.vram, 8)]
    if len(owners) != 1:
        return False
    if any(not match.start() % 4 for address in (fn.vram, fn.vram + 4)
           for match in re.finditer(re.escape(address.to_bytes(4, order)), image)):
        return False
    address = fn.vram + 4
    for body in functions:
        for first, second, third in zip(body.lines, body.lines[1:], body.lines[2:]):
            a, b, c = first.word, second.word, third.word
            reg = (a >> 16) & 31
            if (reg and a >> 26 == 15 and a & 0xffff == ((address + 0x8000) >> 16) & 0xffff
                    and b == (0x24000000 | reg << 21 | reg << 16 | address & 0xffff)
                    and c >> 26 == 43 and (c >> 16) & 31 == reg and (c >> 21) & 31 in (29, 30)):
                return True
    return False


def loaded_constant_words(functions):
    """Find words read by direct floating-point loads from a tracked LUI base."""
    words, doubles = set(), set()
    for fn in functions:
        high = {}
        for line in fn.lines:
            word = line.word
            op, rs, rt = word >> 26, (word >> 21) & 31, (word >> 16) & 31
            if op in (49, 53) and rs in high:
                offset = (word & 0xffff) - (0x10000 if word & 0x8000 else 0)
                address = (high[rs] + offset) & 0xffffffff
                if not address % 4:
                    words.add(address)
                    if op == 53 and not address % 8:
                        doubles.add(address)
                        words.add(address + 4)
            if op == 15 and rt:
                high[rt] = (word & 0xffff) << 16
            elif op == 0:
                high.pop((word >> 11) & 31, None)
            elif op in (2, 3):
                high.clear()
            elif op in (16, 17) and rs < 8:
                high.pop(rt, None)
            elif op in (8, 9, 10, 11, 12, 13, 14, 32, 33, 34, 35, 36, 37, 38):
                high.pop(rt, None)
            elif op not in (1, 4, 5, 6, 7, 16, 17, 20, 21, 22, 23, 40, 41, 42, 43, 46, 49, 53, 57, 61):
                high.clear()
    return words, doubles


def leading_loaded_constants(fn, following, image, order, segments, loaded, entered, data):
    """A segment begins inside a double and float, both read as floating data."""
    if (fn.size != 8 or fn.rom < 4 or following is None or following.rom != fn.rom + 12
            or following.vram != fn.vram + 12 or not is_frame(following.lines[0].word)
            or image[fn.rom + 8:fn.rom + 12] != bytes(4)
            or image[fn.rom:fn.rom + 8] != b''.join(line.word.to_bytes(4, order) for line in fn.lines)
            or fn.vram - 4 not in loaded[1] or fn.vram + 4 not in loaded[0]
            or any(fn.vram <= addr < fn.vram + 8 for addr in entered)
            or any(d.rom is not None and d.rom < fn.rom + 8 and fn.rom < d.rom + d.size for d in data)):
        return False
    if not any(s.rom_start == fn.rom and s.vram == fn.vram and following.rom + following.size <= s.rom_end
               for s in segments):
        return False
    if any(sum(s.contains(addr, 4) for s in segments) != 1 for addr in (fn.vram, fn.vram + 4)):
        return False
    if any(not match.start() % 4 for address in (fn.vram, fn.vram + 4)
           for match in re.finditer(re.escape(address.to_bytes(4, order)), image)):
        return False
    return True


def leading_constant_padding(fn, following, image, order, segments, calls, entered):
    """A harmless instruction-shaped word and zero alignment after a floating constant."""
    start = fn.rom
    if (fn.size != 4 or fn.lines[0].word != 0x30000000 or start < 8 or following is None
            or following.rom != start+16 or following.vram != fn.vram+16 or following.vram%16
            or following.vram not in calls or not is_frame(following.lines[0].word)
            or any(image[start+4:start+16]) or any(fn.vram <= address < following.vram for address in entered)):
        return False
    if not any(s.rom_start == start and s.vram == fn.vram and following.rom+following.size <= s.rom_end
               for s in segments):
        return False
    value, = struct.unpack('>d' if order == 'big' else '<d', image[start-8:start])
    return 0.0001 <= abs(value) <= 1_000_000_000


def terminal_descending_halfwords(fn, previous, image, order, segments):
    """A branch-shaped final word before a smooth signed halfword lookup table."""
    end = fn.rom+4
    word = fn.lines[0].word
    if (fn.size != 8 or word>>26 not in (4,5,6,7,20,21,22,23) or not 1 <= word&0xffff < 0x8000
            or end%16 or previous is None or previous.size < 8 or previous.lines[-2].word != JR_RA
            or previous.rom+previous.size != fn.rom or previous.vram+previous.size != fn.vram
            or fn.lines[1].rom != end or not 8 <= fn.rom < end+64 <= len(image)):
        return False
    owners = [s for s in segments if s.rom_start <= fn.rom-8 and s.rom_end == end
              and s.vram-s.rom_start == fn.vram-fn.rom]
    target = end+4*(word&0xffff)
    if len(owners) != 1 or any(s.rom_start < end+64 and end < s.rom_end for s in segments) or any(
            s.rom_start <= target < s.rom_end for s in segments):
        return False
    values = struct.unpack_from('>32h' if order == 'big' else '<32h', image, end)
    steps = [a-b for a,b in zip(values,values[1:])]
    return (all(v < 0 for v in values) and all(0 < step <= 512 for step in steps)
            and all(b <= a+2 for a,b in zip(steps,steps[1:])))

def terminal_data_alignment(fn, previous, image, order, segments):
    """A word of assembler filler at the aligned end of code, before float data."""
    end = fn.rom+4
    # A filler that decodes as a branch can borrow the first word of the float
    # table as a delay slot. Its apparent destination must also be in the data.
    word = fn.lines[0].word
    branch_filler = (fn.size==8 and not end%16 and not (fn.vram+4)%8
                     and (word>>21==0x228 or word>>26 in (4,5,6,7,20,21,22,23))
                     and 1 <= word&0xffff < 0x8000 and fn.lines[1].rom==end)
    text_filler = fn.size==4 and not (fn.vram+4)%16 and image[fn.rom+1:end]==bytes((13,10,13))
    load_filler = fn.size==4 and not (fn.vram+4)%16 and word>>26 in (32,33,35,36,37,49)
    if (not (branch_filler or text_filler or load_filler) or previous is None or previous.size<8
            or previous.rom+previous.size!=fn.rom or previous.vram+previous.size!=fn.vram
            or previous.lines[-2].word!=JR_RA
            or not 0<=fn.rom-8<end+32<=len(image)):
        return False
    owner = [s for s in segments if s.rom_start<=fn.rom-8 and s.rom_end==end
             and s.vram-s.rom_start==fn.vram-fn.rom]
    if len(owner)!=1 or any(s.rom_start<end+32 and end<s.rom_end for s in segments):
        return False
    fmt = '>f' if order=='big' else '<f'
    values = [value for value, in struct.iter_unpack(fmt,image[end:end+32])]
    def finite(value):
        return value==0 or 0.0001<=abs(value)<=1_000_000
    if branch_filler:
        target = end+4*(word&0xffff)
        if (target+4>len(image) or any(s.rom_start<=target<s.rom_end for s in segments)
                or not finite(struct.unpack_from(fmt,image,target)[0])):
            return False
    if load_filler:
        # Some float records end with a null field and continue with addresses.
        # Require four distinct float fields before those addresses.
        addresses = [address for address, in struct.iter_unpack('>I' if order=='big' else '<I', image[end+16:end+32])]
        if any(address % 4 or not any(segment.contains(address, 4) for segment in segments) for address in addresses):
            return False
        values = values[:4]
    return all(map(finite,values)) and len(set(values))>=4


def calls_current_thread_dispatch(image, target, dispatcher, segments, order):
    """Verify the self-destroy path from the running-thread load to its dispatch."""
    owners = [segment for segment in segments if segment.contains(target, 51*4)]
    if len(owners) != 1:
        return False
    owner = owners[0]
    start = target-owner.vram+owner.rom_start
    # On a null argument it loads the current thread into s0. The matching
    # global is loaded again before the conditional dispatch call.
    for index, length in ((38, 51), (41, 54)):
        if not 0<=start<=len(image)-length*4 or not owner.contains(target,length*4):
            continue
        words = struct.unpack_from(f'{">" if order=="big" else "<"}{length}I',image,start)
        high, load, branch, delay, call, call_delay, next_word = words[index:index+7]
        if (words[0]!=0x27bdffe0 or words[2]!=0x00808021 or words[6]!=0x16000004
                or words[8]>>16!=0x3c10 or words[10]>>16!=0x8e10
                or high>>16!=0x3c02 or load>>16!=0x8c42 or high&0xffff!=words[8]&0xffff
                or load&0xffff!=words[10]&0xffff or (branch,delay,call_delay)!=(0x16020003,0,0)
                or words[-2:]!=(JR_RA,0x27bd0020) or call>>26!=3):
            continue
        if index==38 and (words[9]>>26!=2 or words[11]>>16!=0x9603
                          or next_word>>26!=3 or words[index+7]!=0x02202021):
            continue
        if index==41 and next_word!=0:
            continue
        if (((target+(index+5)*4)&0xf0000000)|((call&0x3ffffff)<<2))==dispatcher:
            return True
    return False


def terminal_halfword_data_alignment(fn, previous, image, order, segments):
    """Keep an unentered final scalar word ahead of an aligned halfword table in data."""
    end = fn.rom+4
    if (fn.size != 4 or fn.lines[0].word>>26 != 11 or (fn.vram+4)%16
            or previous is None or previous.size<8 or previous.lines[-2].word != JR_RA
            or previous.rom+previous.size != fn.rom or previous.vram+previous.size != fn.vram
            or not 8<=fn.rom<end+80<=len(image)):
        return False
    owner = [s for s in segments if s.rom_start<=fn.rom-8 and s.rom_end==end
             and s.vram-s.rom_start==fn.vram-fn.rom]
    if len(owner)!=1 or any(s.rom_start<end+80 and end<s.rom_end for s in segments):
        return False
    record = struct.unpack_from('>12I' if order=='big' else '<12I', image, end)
    indices = struct.unpack_from('>8I' if order=='big' else '<8I', image, end+48)
    return (record[:7]==(0,0,0x8000,0x8000,0x4000,0x4000,0)
            and record[9:]==(0,0,0) and indices[:2]==(1,2)
            and all(1<=b-a<=2 for a,b in zip(indices,indices[1:])))


def terminal_memory_ranges(fn, previous, image, order, segments):
    """A terminal data word mistaken for a call borrowing a range-table limit."""
    end = fn.rom+8
    if (fn.size != 8 or fn.lines[0].word != 0x0d000000 or fn.lines[1].word != 0x7fffffff
            or previous is None or previous.size < 8 or previous.lines[-2].word != JR_RA
            or previous.rom+previous.size != fn.rom or previous.vram+previous.size != fn.vram
            or not 8 <= fn.rom < end+28 <= len(image)):
        return False
    table = struct.unpack_from('>8I' if order == 'big' else '<8I', image, fn.rom+4)
    if table != (0x7fffffff, 0, 0x803fffff, 3, 0x9fffffff, 0, 0xa03fffff, 3):
        return False
    owners = [segment for segment in segments
              if segment.rom_start <= fn.rom-8 and segment.rom_end in (end-4, end)
              and segment.vram-segment.rom_start == fn.vram-fn.rom]
    return len(owners) == 1 and not any(
        s is not owners[0] and s.rom_start < end+28 and end-4 < s.rom_end for s in segments)


def overlay_end_marker(fn, previous, following, image, order, segments):
    # Some overlays use an inert 0xBEEF sentinel among alignment zeros after a tail jump.
    if (fn.size != 4 or fn.lines[0].word != 0x2400beef or fn.vram % 16
            or previous is None or previous.segment != fn.segment or previous.rom+previous.size != fn.rom-8
            or previous.size < 8 or previous.lines[-2].word >> 26 != 2
            or following is not None and following.segment == fn.segment and following.rom < fn.rom+32):
        return False
    jump = previous.lines[-2]
    target = ((jump.vram+4) & 0xf0000000) | ((jump.word & 0x3ffffff) << 2)
    owners = [segment for segment in segments if segment.contains(fn.vram-16, 48)]
    if len(owners) != 1:
        return False
    segment = owners[0]
    marker = (0x2400beef).to_bytes(4, order)
    jump_bytes = jump.word.to_bytes(4, order) + previous.lines[-1].word.to_bytes(4, order)
    return (segment.vram - segment.rom_start == fn.vram - fn.rom and not segment.contains(target)
            and image[fn.rom-16:fn.rom+32] == jump_bytes + bytes(8) + marker + bytes(28))


def recover_inline_tables(functions, data, image, order, protected, references, segments, log):
    """Recognize a short byte or halfword table reached through a never-taken link branch.

    Handwritten MIPS can use bltzal $zero to set $ra without transferring
    control, then load a table after the return using that PC-relative base.
    The branch operand can make the disassembler invent a function there.
    """
    from .split import DataSymbol

    ordered = sorted(functions, key=lambda fn: (fn.segment, fn.rom))
    removed = set()
    for previous, table, following in zip(ordered, ordered[1:], ordered[2:]):
        size = following.rom - table.rom
        end = table.vram + size
        if (previous.segment != table.segment or following.segment != table.segment
                or previous.rom + previous.size != table.rom or size != 8
                or table.size not in (8, 12) or following.vram != end
                or previous.vram + previous.size != table.vram
                or previous.size < 8 or previous.lines[-2].word != JR_RA
                or following.vram not in references
                or any(table.vram <= address < end for address in protected)
                or any(d.rom is not None and d.rom < following.rom and table.rom < d.rom + d.size for d in data)):
            continue
        owners = [segment for segment in segments if segment.contains(table.vram, size)]
        if (len(owners) != 1 or owners[0].vram - owners[0].rom_start != table.vram - table.rom
                or any(line.rom < 0 or line.rom+4 > len(image)
                       or int.from_bytes(image[line.rom:line.rom+4], order) != line.word
                       for line in (*previous.lines, *table.lines))):
            continue
        refs = references.get(table.vram, set())
        if len(refs) != 1 or references.get(table.vram + 4):
            continue
        offset, segment = next(iter(refs))
        if segment != previous.segment or not previous.rom <= offset < table.rom - 32:
            continue
        index = (offset - previous.rom) // 4
        branch, delay, address, restore, first, second, third = previous.lines[index:index+7]
        register = (address.word >> 16) & 31
        delta = table.vram - (branch.vram + 8)
        if (branch.word != 0x04100000 | ((delta + 4) // 4 & 0xffff) or delay.word
                or not 0 < delta <= 0x7fff or register in (0, 31)
                or address.word != 0x27e00000 | register << 16 | delta
                or restore.word & 0xfc1fffff != 0x0000f821
                or first.word & 0xffe0ffff != register << 21 | register << 11 | 0x21
                or not (second.word & 0xffe0ffff == 0x80000000 | register << 21
                        or first.word == second.word and third.word & 0xffe0ffff == 0x94000000 | register << 21)):
            continue
        data.append(DataSymbol(table.name, table.vram, table.rom, size, 'data'))
        removed.add(id(table))
    if removed:
        log(f'Recovered {len(removed)} PC-relative tables from false function entries.')
        return [fn for fn in functions if id(fn) not in removed]
    return functions


LONG_ALIGNMENT = 128
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
    # Splat may split a single contiguous loaded bank into several units. Calls
    # into any of those units describe the same slot and need joint evidence.
    groups = []
    for segment in sorted(segments, key=lambda s: s.rom_start):
        if (groups and groups[-1][-1].rom_end == segment.rom_start
                and groups[-1][-1].vram-groups[-1][-1].rom_start == segment.vram-segment.rom_start
                and groups[-1][-1].exclusive_ram_id == segment.exclusive_ram_id):
            groups[-1].append(segment)
        else:
            groups.append([segment])
    calls=defaultdict(list)
    for fn in functions:
        for line in fn.lines:
            if line.word>>26!=3:continue
            dest=((line.vram+4)&0xf0000000)|((line.word&0x3ffffff)<<2)
            for group_index, group in enumerate(groups):
                for segment in group:
                    if (segment.contains(dest,4)
                            and not (group[0].rom_start<=line.rom<group[-1].rom_end
                                     and line.vram-line.rom==segment.vram-segment.rom_start)):
                        calls[group_index].append((dest,line.rom,fn.segment,
                                                   dest-segment.vram+segment.rom_start,group[0].rom_start))
    out,slots=[],[]
    for group_index,found in calls.items():
        targets={c[3]:c[4] for c in found}
        if len(targets)<SHARED_SLOT_CALLS:continue
        fit=sum(1 for at,start in targets.items() if at==start or is_frame(word(at) or 0) or word(at-8)==JR_RA)
        if fit*2<len(targets):
            out += [c[:3] for c in found]
            slots += groups[group_index]
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
