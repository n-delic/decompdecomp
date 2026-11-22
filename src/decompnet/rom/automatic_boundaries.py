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


def repair(functions, data, image, console, segments, *, symbols=(), log=print, inline_data=()):
    """Keep all original instructions while joining/splitting strictly validated bodies."""
    if console.arch!='mips':return functions
    from .split import _LOCAL_REF, _SYMBOL_LINE, AsmLine, DataSymbol
    explicit={int(m[2],16) for text in symbols for m in _SYMBOL_LINE.finditer(text)}
    starts={f.vram for f in functions}
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
        for line in fn.lines:
            op=line.word>>26
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
                    references[names[token]].add((line.rom,fn.segment))
                    reference_bodies[line.rom]=fn
    for dest,offset,segment in shared:
        references[dest].discard((offset,segment))
        if not references[dest]:del references[dest]
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

    recovered_data=[]
    predecessors={(fn.rom+fn.size,fn.vram+fn.size):fn for fn in result if fn.size>=8}
    kept=[];dropped=markers=recovered_shared=0;ordered=sorted(result,key=lambda f:(owner(f),f.rom))
    for i,fn in enumerate(ordered):
        previous=kept[-1] if kept else None;following=ordered[i+1] if i+1<len(ordered) else None
        if overlay_end_marker(fn, previous, following, image, order, segments) and not referenced(fn):
            data.append(DataSymbol(fn.name, fn.vram, fn.rom, 4, 'data'))
            markers += 1
            continue
        if (previous is not None and owner(previous)==owner(fn)
                and (fn.vram in (explicit | pointers)
                     and shared_entry_leaf(previous,fn,image,console,order,segments,entered)
                     or previous.vram in direct_calls and fn.vram in direct_calls
                     and shared_called_leaf(previous,fn,image,console,order,segments,
                                            explicit | pointers | {address for address, sources in references.items()
                                                if any(not previous.rom <= src < fn.rom+fn.size for src, _ in sources)},
                                            data))):
            kept[-1] = replace(previous,lines=previous.lines+fn.lines,
                               labels=previous.labels|fn.labels,
                               entry_aliases={**previous.entry_aliases,fn.vram:fn.name})
            data.append(DataSymbol(fn.name,fn.vram,fn.rom,0,'func'))
            recovered_shared += 1
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
        if (leading_repeated_double(fn, following, image, order, segments, direct_calls)
                and not referenced(fn)):
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
        if zero_alignment(fn, previous):
            if fn.vram in data_only:
                recovered_data.append(DataSymbol(fn.name,fn.vram,fn.rom,4,'data'))
            else:
                dropped+=1
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
        data.extend(recovered_data)
        log(f'Recovered {len(recovered_data)} referenced words as data between functions.')
    if recovered_shared:
        log(f'Recovered {recovered_shared} closed leaves with independently entered assembly tails.')
    return kept if dropped or markers or recovered_data or recovered_shared else result if changed else functions



def shared_called_leaf(head, tail, image, console, order, segments, entered, data):
    """Keep both called entries when a short prefix falls through into a closed leaf."""
    if (head.size != 4 or tail.size < 8 or head.rom+head.size != tail.rom
            or head.vram+head.size != tail.vram or head.entry_aliases or tail.entry_aliases
            or any(tail.vram < address < tail.vram+tail.size for address in entered)):
        return False
    if sum(s.contains(head.vram,head.size+tail.size) for s in segments) != 1:
        return False
    end = tail.rom+tail.size
    if any(d.rom is not None and d.rom < end and head.rom < d.rom+d.size for d in data):
        return False
    if image[head.rom:end] != b''.join(line.word.to_bytes(4,order) for fn in (head,tail) for line in fn.lines):
        return False
    options = dict(big_endian=console.big_endian, cpu=console.cpu_variant, asm_check=console.asm_reason)
    return (leaf_extent(image,head.rom,end,head.vram,**options) == head.size+tail.size
            and leaf_extent(image,tail.rom,end,tail.vram,**options) == tail.size)


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
    """Recover a segment prefix with a printable string tail and a proven switch table."""
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
        if not re.match(rb'[ -~]{4,60}\x00', raw):
            continue
        offsets = {offset for offset,_ in references.get(fn.vram,())}
        for consumer in functions:
            if not any(consumer.rom<=offset<consumer.rom+consumer.size for offset in offsets):
                continue
            for line in consumer.lines:
                if line.word==JR_RA or line.word&0xfc1fffff!=8:
                    continue
                table = jump_table(image,consumer.rom,consumer.rom+consumer.size,consumer.vram,
                                   line.rom,order,tables,allow_saved_base=True)
                if not table or table['state']!='validated':
                    continue
                start = table['rom_offset']-fn.rom
                if (not 0<start<fn.size or start+table['entry_count']*4!=fn.size
                        or not re.fullmatch(rb'[ -~]{4,60}\x00+', raw[:start])):
                    continue
                data.append(DataSymbol(fn.name,fn.vram,fn.rom,start,'data'))
                data.append(DataSymbol(f"jtbl_{table['table_address']:08X}",table['table_address'],
                                       table['rom_offset'],fn.size-start,'jtbl'))
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
