"""Recover overlapping code banks when their self-calls prove a different placement."""

import collections
import struct

from ...arch.mips import JR_RA, is_frame

_MIN_CALLS = 32
_MAX_PAIRS = 1_000_000


def _unique_closed_mapping(image, lo, hi, targets, scores, mapping, check):
    """Break a close raw-score tie when other well-supported mappings lack closed callees."""
    from ...arch.mips_code_recovery import called_extent

    candidates = [candidate for candidate, score in scores.items() if candidate == mapping or score >= 8]
    if len(candidates) > 32:
        return False  # heavily ambiguous data stays with boundary review
    verdicts = {}
    for candidate in candidates:
        check()
        verdicts[candidate] = sum(
            bool(called_extent(image, at, hi, target, cpu='vr4300'))
            for target in targets if lo <= (at := target-candidate) < hi-8
        )
    return verdicts.get(mapping, 0) >= 12 and all(
        count < 2 for candidate, count in verdicts.items() if candidate != mapping)


def _entry_mapping(image, lo, hi, vram, callers, taken, check):
    """Find a nearby aligned mapping when many calls name complete return-separated entries."""
    old = vram-lo
    if old % 16 == 0 or hi-lo < 4096:
        return None
    words = struct.unpack_from(f'>{(hi-lo)//4}I', image, lo)
    entries = set()
    for i in range(2, len(words)-3):
        a,b,c = words[i:i+3]
        reg = (a >> 16) & 31
        if (words[i-2] == JR_RA and a >> 26 == 15 and reg
                and b >> 26 == 9 and (b >> 21) & 31 == reg and (b >> 16) & 31 == reg
                and c >> 26 == 35 and (c >> 21) & 31 == reg and (c >> 16) & 31):
            entries.add(lo+4*i)
    if len(entries) < 8:
        return None
    targets = [set(), set()]
    for start,end,base in callers:
        check()
        code = struct.unpack_from(f'>{(end-start)//4}I', image, start)
        targets[start == lo and end == hi].update(
            0x80000000 | ((word & 0x03ffffff) << 2) for word in code if word >> 26 == 3)
    previous = sum(len(group & {at+old for at in entries}) for group in targets)
    choices = []
    for delta in range(-15, 16):
        mapping = old+delta
        address = lo+mapping
        if (mapping % 16 or not 0x80000400 <= address < address+hi-lo <= 0x80800000
                or any(base < address+hi-lo and address < base+end-start for start,end,base in taken)):
            continue
        matches = {at+mapping for at in entries}
        outside, inside = (len(group & matches) for group in targets)
        if inside >= 8 and outside >= 1 and inside+outside >= 4*max(1,previous):
            choices.append(address)
    return choices[0] if len(choices) == 1 else None


def _called_mapping(image, lo, hi, vram, callers, taken, check):
    """Correct a nearby alignment only when independent calls reach closed framed functions."""
    from ...arch.mips_code_recovery import called_extent

    old = vram-lo
    if old%16 == 0 or hi-lo < 4096:
        return None
    groups = [set(),set()]
    for start,end,base in callers:
        check()
        if start < 0 or end > len(image) or start >= end or (start|end)%4:
            continue
        words = struct.unpack_from(f'>{(end-start)//4}I',image,start)
        groups[start == lo and end == hi].update(
            0x80000000 | ((word&0x3ffffff)<<2) for word in words if word>>26 == 3)
    def score(mapping):
        counts = []
        for targets in groups:
            total = 0
            for target in targets:
                at = target-mapping
                if lo <= at < hi-8 and called_extent(image,at,hi,target,cpu='vr4300'):
                    total += 1
            counts.append(total)
        return counts
    prior = score(old)
    choices = []
    for delta in range(-15,16):
        mapping = old+delta
        address = lo+mapping
        if (mapping%16 or not 0x80000400 <= address < address+hi-lo <= 0x80800000
                or any(base < address+hi-lo and address < base+end-start for start,end,base in taken)):
            continue
        outside,inside = score(mapping)
        if inside >= 8 and outside >= 3 and inside+outside >= 4*max(1,sum(prior)):
            choices.append(address)
    return choices[0] if len(choices) == 1 else None


def recover(image, found, taken=(), check=lambda: None, *, changed=()):
    """Return corrected inferred ranges and the starts needing independent splat scopes."""
    result = list(found)
    changed = set(changed)
    for index, (lo, hi, vram) in enumerate(found):
        check()
        if lo < 0 or hi - lo < 4096 or hi > len(image) or (lo | hi) % 4:
            continue
        if corrected := (_entry_mapping(image, lo, hi, vram, (*taken, *found), taken, check)
                         or _called_mapping(image, lo, hi, vram, (*taken, *found), taken, check)):
            result[index] = (lo, hi, corrected)
            changed.add(lo)
            continue
        words = struct.unpack_from(f'>{(hi-lo)//4}I', image, lo)
        targets = {0x80000000 | ((word & 0x03ffffff) << 2) for word in words if word >> 26 == 3}
        entries = [lo + i*4 for i, word in enumerate(words)
                   if is_frame(word) or i >= 2 and words[i-2] == JR_RA]
        if (len(targets) < _MIN_CALLS or len(entries) < _MIN_CALLS
                or len(targets) * len(entries) > _MAX_PAIRS):
            continue
        scores = collections.Counter()
        for target in targets:
            check()
            for at in entries:
                mapping = target-at
                if not mapping % 16 and 0x7f000000 < mapping < 0x81000000:
                    scores[mapping] += 1
        ranked = scores.most_common(2)
        if len(ranked) < 2:
            continue
        (mapping, score), (_, second) = ranked
        address = lo+mapping
        if (mapping == vram-lo or score < _MIN_CALLS
                or score < scores[vram-lo]*4
                or not 0x80000400 <= address < address+hi-lo <= 0x80800000):
            continue
        if score < second*6 and not _unique_closed_mapping(image, lo, hi, targets, scores, mapping, check):
            continue
        if any(base < address+hi-lo and address < base+end-start for start,end,base in taken):
            continue
        if not any(other != index and base < address+hi-lo and address < base+end-start
                   for other,(start,end,base) in enumerate(found)):
            continue
        result[index] = (lo,hi,address)
        changed.add(lo)
    if not changed:
        return result, set()
    # Splat's global range spans all global code. Every inferred segment at or
    # above the first overlapping bank needs a scope, even when it lies later.
    scoped = set(changed)
    while True:
        overlap = {lo for lo,hi,vram in result for other,end,base in result
                   if other in scoped and base < vram+hi-lo and vram < base+end-other}
        if overlap <= scoped:
            break
        scoped |= overlap
    first = min(vram for lo,_,vram in result if lo in scoped)
    return result, {lo for lo,_,vram in result if vram >= first}


def _runtime_continuation(image,start,end,address,old,previous,verified,ranges):
    """A witnessed bank can continue into two closed entries called by its verified predecessor."""
    from ...arch.mips_code_recovery import called_extent

    for lo,hi,base in previous:
        if hi!=start or lo not in verified or base-lo!=address-start:
            continue
        if not any(a<=lo and end<=b and v-a==address-start for a,b,v in ranges):
            continue
        words=struct.unpack_from(f'>{(hi-lo)//4}I',image,lo)
        targets={0x80000000|((word&0x03ffffff)<<2) for word in words if word>>26==3}
        # A call to the old placement is contradictory evidence; don't choose a bank.
        if any(old<=target<old+end-start for target in targets):
            continue
        closed={target for target in targets if address<=target<address+end-start
                and called_extent(image,start+target-address,end,target,cpu='vr4300')}
        if len(closed)>=2:
            return True
    return False


def runtime_mappings(image, found, ranges, taken=()):
    """Apply RAM-observed placements when internal calls also support them."""
    result, changed = [], set()
    for lo,hi,vram in found:
        evidence = [(start,end,base) for start,end,base in ranges if start < hi and lo < end]
        boundaries = sorted({lo,hi}|{max(lo,start) for start,_,_ in evidence}
                            |{min(hi,end) for _,end,_ in evidence})
        parts, corrected = [], set()
        for start,end in zip(boundaries,boundaries[1:]):
            old = vram+start-lo
            candidates = {base+start-first for first,last,base in evidence if first <= start < end <= last}
            if len(candidates) != 1 or end-start < 128 or (start|end) % 4:
                parts.append((start,end,old))
                continue
            address = candidates.pop()
            if (not 0 <= start < end <= len(image)
                    or not 0x80000400 <= address < address+end-start <= 0x80800000
                    or any(base < address+end-start and address < base+stop-begin for begin,stop,base in taken)):
                parts.append((start,end,old))
                continue
            words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
            offsets = [4*i for i, word in enumerate(words)
                       if is_frame(word) or i >= 2 and words[i-2] == JR_RA]
            calls = {0x80000000 | ((word & 0x03ffffff) << 2) for word in words if word >> 26 == 3}
            if start == lo and address in calls and 0 not in offsets:
                # A bank may start with a called leaf instead of a stack frame.
                # Count it only when its complete body closes at this mapping.
                from ...arch.mips_code_recovery import leaf_extent
                if leaf_extent(image, start, end, address, cpu='vr4300'):
                    offsets.append(0)
            actual = len(calls & {address+offset for offset in offsets})
            previous = len(calls & {old+offset for offset in offsets})
            if address != old and (actual >= 3 and actual > previous or
                                   _runtime_continuation(image,start,end,address,old,result,changed,ranges)):
                parts.append((start,end,address))
                corrected.add(start)
            else:
                parts.append((start,end,old))
        if corrected:
            # Overlapping RAM observations can cut a corrected bank into tiny
            # pieces. Carry its independently corroborated mapping into a
            # neighbouring fragment only when RAM observed that same mapping.
            verified = set(corrected)
            while True:
                extended = False
                for index, (start, end, address) in enumerate(parts):
                    if start in verified or end-start >= 128:
                        continue
                    candidates = {base+start-first for first,last,base in evidence if first <= start < end <= last}
                    neighbours = [parts[j] for j in (index-1, index+1) if 0 <= j < len(parts)
                                  and parts[j][0] in verified]
                    matches = {start+other[2]-other[0] for other in neighbours} & candidates
                    if len(candidates) == 1 and len(matches) == 1:
                        parts[index] = (start, end, matches.pop())
                        verified.add(start)
                        extended = True
                if not extended:
                    break
            merged = []
            for start, end, address in parts:
                if merged and merged[-1][1] == start and merged[-1][2]-merged[-1][0] == address-start:
                    merged[-1] = (merged[-1][0], end, merged[-1][2])
                else:
                    merged.append((start, end, address))
            result.extend(merged)
            changed.update(start for start,end,_ in merged if any(start <= item < end for item in verified))
        else:
            result.append((lo,hi,vram))
    return result, changed


def scopes_fit(found, scoped, taken):
    """Splat's global range spans the gaps between all unscoped code segments."""
    global_ranges = [(base, base + end - start) for start, end, base in taken]
    global_ranges += [(base, base + end - start) for start, end, base in found if start not in scoped]
    if not global_ranges:
        return True
    low = min(start for start, _ in global_ranges)
    high = max(end for _, end in global_ranges)
    return all(not (base < high and low < base + end - start)
               for start, end, base in found if start in scoped)


def safe_scopes(found, scoped, taken):
    """Scope inferred code on both sides of the global range when necessary."""
    if not scoped or scopes_fit(found, scoped, taken):
        return scoped
    all_inferred = {start for start, _, _ in found}
    return all_inferred if scopes_fit(found, all_inferred, taken) else None


def resident_children(config, found, scoped, taken):
    """Keep a recovered child in its resident scope when its mapping permits it."""
    rows = config['segments']
    top = {row['start'] for row in rows if isinstance(row, dict) and row.get('type') == 'code'}
    children = set()
    for lo, hi, address in found:
        if lo not in scoped or lo in top:
            continue
        for index, row in enumerate(rows[:-1]):
            if not isinstance(row, dict) or row.get('type') != 'code' or row.get('exclusive_ram_id'):
                continue
            following = rows[index+1]
            end = following[0] if isinstance(following, list) else following.get('start')
            if (isinstance(end, int) and isinstance(row.get('vram'), int)
                    and row['start'] <= lo < hi <= end
                    and row['vram'] + lo - row['start'] == address):
                children.add(lo)
                break
    reduced = scoped - children
    return reduced if children and scopes_fit(found, reduced, taken) else scoped


def scope(config, found, scoped):
    """Put each overlapping connected set into one exclusive address space."""
    if not scoped:
        return True
    segments = {seg['start']: seg for seg in config['segments']
                if isinstance(seg, dict) and seg.get('type') == 'code'}
    remaining = {lo for lo,_,_ in found if lo in scoped}
    # A recovered range may have been inserted as a child of resident code,
    # rather than as a separate bank. Scoping its parent would also scope other
    # code; retain the earlier mappings instead of guessing or raising KeyError.
    if not remaining <= segments.keys():
        return False
    ranges = {lo:(vram,vram+hi-lo) for lo,hi,vram in found if lo in scoped}
    while remaining:
        pending = [min(remaining)]
        group = set()
        while pending:
            lo = pending.pop()
            if lo in group:
                continue
            group.add(lo)
            a,b = ranges[lo]
            pending.extend(other for other in remaining-group if ranges[other][0] < b and a < ranges[other][1])
        remaining -= group
        ident = f'inferred_slot_{min(group):X}'
        for lo in group:
            segment = segments[lo]
            name = segment['name']
            segment.update(exclusive_ram_id=ident, symbol_name_format=f'$VRAM_{name}',
                           symbol_name_format_no_rom=f'$VRAM_{name}')
    return True


def apply_scopes(config, found, scoped):
    """Scope inferred banks and check every resident BSS they can touch."""
    return scope(config, found, scoped) and scope_resident_bss(config)


def scope_resident_bss(config):
    """Share an overlay scope when its RAM overlaps only a resident segment's BSS.

    Return false instead of making a guessed scope when a resident code range
    overlaps or its BSS touches more than one independently scoped group.
    """
    rows = config['segments']
    scoped = []
    for index, segment in enumerate(rows[:-1]):
        if not isinstance(segment, dict) or segment.get('type') != 'code':
            continue
        if not segment.get('exclusive_ram_id') or not isinstance(segment.get('vram'), int):
            continue
        following = rows[index+1]
        end = following[0] if isinstance(following, list) else following.get('start')
        if isinstance(end, int):
            scoped.append((segment['vram'], segment['vram']+end-segment['start'], segment['exclusive_ram_id']))
    updates = []
    for index, segment in enumerate(rows[:-1]):
        if (not isinstance(segment, dict) or segment.get('type') != 'code' or segment.get('exclusive_ram_id')
                or not segment.get('bss_size') or not isinstance(segment.get('vram'), int)):
            continue
        following = rows[index+1]
        end = following[0] if isinstance(following, list) else following.get('start')
        if not isinstance(end, int):
            return False
        code_start = segment['vram']
        code_end = code_start+end-segment['start']
        if not isinstance(segment['bss_size'], int) or segment['bss_size'] < 0:
            return False
        bss_end = code_end+segment['bss_size']
        touching = [(a,b,ident) for a,b,ident in scoped if a < bss_end and code_start < b]
        if any(a < code_end and code_start < b for a,b,_ in touching):
            return False
        identities = {ident for _,_,ident in touching}
        if len(identities) > 1:
            return False
        if identities:
            updates.append((segment, identities.pop()))
    # Splat treats all remaining global code as one range, including gaps.
    # Scoping a resident BSS also scopes its text; an earlier global startup
    # or a later global bank can make that text overlap the global envelope.
    # Leave the original mappings in place when that would make splitting fail.
    changed = {id(segment) for segment, _ in updates}
    global_ranges = []
    for index, segment in enumerate(rows[:-1]):
        if (not isinstance(segment, dict) or segment.get('type') != 'code'
                or segment.get('exclusive_ram_id') or id(segment) in changed
                or not isinstance(segment.get('vram'), int)):
            continue
        following = rows[index+1]
        end = following[0] if isinstance(following, list) else following.get('start')
        if not isinstance(end, int):
            return False
        start = segment['vram']
        bss = segment.get('bss_size') or 0
        if not isinstance(bss, int) or bss < 0:
            return False
        global_ranges.append((start, start+end-segment['start']+bss))
    if global_ranges:
        low = min(start for start, _ in global_ranges)
        high = max(end for _, end in global_ranges)
        for segment, _ in updates:
            index = next(i for i, row in enumerate(rows) if row is segment)
            following = rows[index+1]
            end = following[0] if isinstance(following, list) else following['start']
            start = segment['vram']
            if start < high and low < start+end-segment['start']+segment['bss_size']:
                return False
    for segment, ident in updates:
        segment['exclusive_ram_id'] = ident
    return True


def drop_pointer_fragments(image, found, taken=()):
    """Leave a terminated pointer table outside text when discovery split it into one-word scopes."""
    ordered = sorted(found)
    remove = set()
    for i in range(1, len(ordered)-2):
        prev = ordered[i-1]
        group = ordered[i:i+3]
        if (any(end-start != 4 for start,end,_ in group)
                or prev[1] != group[0][0]
                or group[0][1] != group[1][0] or group[1][1] != group[2][0]
                or group[1][2] != group[0][2]+4 or group[2][2] != group[0][2]+8):
            continue
        first, second, zero = struct.unpack_from('>3I', image, group[0][0])
        if (zero or first % 4 or second % 4 or first == second
                or not all(prev[2] <= v < prev[2]+prev[1]-prev[0] for v in (first, second))):
            continue
        targets = {v for _,_,v in group}
        referenced = False
        for start,end,base in (*taken, *found):
            for at in range(start, end, 4):
                word = struct.unpack_from('>I', image, at)[0]
                op = word >> 26
                if op in (2,3):
                    dest = ((base+at-start+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                elif op in (1,4,5,6,7,20,21,22,23) or op == 17 and (word >> 21) & 31 == 8:
                    imm = (word & 0xffff) ^ 0x8000
                    dest = base+at-start+4+(imm-0x8000)*4
                else:
                    continue
                if dest in targets:
                    referenced = True
                    break
            if referenced:
                break
        if not referenced:
            remove.update(start for start,_,_ in group)
    return [segment for segment in found if segment[0] not in remove]


def split_concatenated(image, found, check=lambda: None, *, taken=()):
    """Split adjacent banks when two called entries share an address and a data gap."""
    from ...arch.mips_code_recovery import called_extent

    result, changed = [], set()
    for lo, hi, vram in found:
        check()
        if lo < 0 or hi > len(image) or hi-lo < 8192 or (lo | hi) % 4:
            result.append((lo, hi, vram))
            continue
        words = struct.unpack_from(f'>{(hi-lo)//4}I', image, lo)
        targets = {0x80000000 | ((word & 0x3ffffff) << 2) for word in words if word >> 26 == 3}
        entries = [lo+i*4 for i, word in enumerate(words)
                   if is_frame(word) or i >= 2 and words[i-2] == JR_RA]
        candidates = []
        for target in targets:
            delta = target - vram
            boundary = lo + delta
            if delta < 4096 or boundary + 4096 > hi or delta % 16:
                continue
            if not called_extent(image, lo, hi, target, cpu='vr4300'):
                continue
            stops = [at+8 for at in range(max(lo, boundary-0x2000), boundary-72, 4)
                     if image[at:at+4] == b'\x03\xe0\x00\x08' and not any(image[at+8:at+72])]
            if not stops:
                continue
            end = stops[-1]
            if end <= lo or boundary - end < 64:
                continue
            early = [at for at in entries if at < end]
            late = [at for at in entries if at >= boundary]
            new_shift, old_shift = target-lo, vram-lo
            first_words = struct.unpack_from(f'>{(end-lo)//4}I', image, lo)
            second_words = struct.unpack_from(f'>{(hi-boundary)//4}I', image, boundary)
            first_calls = {0x80000000 | ((word & 0x3ffffff) << 2) for word in first_words if word >> 26 == 3}
            second_calls = {0x80000000 | ((word & 0x3ffffff) << 2) for word in second_words if word >> 26 == 3}
            before = sum(at + new_shift in first_calls for at in early)
            before_old = sum(at + old_shift in first_calls for at in early)
            after = sum(at + old_shift in second_calls for at in late)
            if before >= 8 and before >= 4*max(1, before_old) and after >= 8:
                candidates.append((end, boundary, target))
        if len(candidates) != 1:
            separate = _separated_banks(image, lo, hi, targets, entries, check) if not candidates else None
            if separate:
                left, right = separate
                gap_start, gap_end = left[1], right[0]
                shifts = (left[2]-left[0], right[2]-right[0])
                for start, end, base in (*taken, *found):
                    check()
                    if start < 0 or end > len(image) or start >= end or (start | end) % 4:
                        continue
                    words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
                    calls = (0x80000000 | ((word & 0x3ffffff) << 2) for word in words if word >> 26 == 3)
                    if any(not (base <= target < base+end-start
                                and not gap_start <= start+target-base < gap_end)
                           and any(gap_start <= target-shift < gap_end for shift in shifts) for target in calls):
                        separate = None
                        break
            if separate is None:
                result.append((lo, hi, vram))
            else:
                result.extend(separate)
                changed.update(start for start, _, _ in separate)
            continue
        end, boundary, target = candidates[0]
        result.extend(((lo, end, target), (boundary, hi, vram + boundary-lo)))
        changed.update((lo, boundary))
    return sorted(result), changed


def _separated_banks(image, lo, hi, targets, entries, check):
    """Separate two independently called banks around a frame-free data gap."""
    from ...arch.mips_code_recovery import called_extent

    if len(targets)*len(entries) > _MAX_PAIRS:
        return None
    scores = collections.Counter(target-at for target in targets for at in entries
                                 if not (target-at) % 16 and 0x7f000000 < target-at < 0x81000000)
    mappings = [mapping for mapping, count in scores.items() if count >= 8]
    if len(mappings) != 2:
        return None
    called = {}
    for mapping in mappings:
        check()
        called[mapping] = sorted(at for target in targets if lo <= (at := target-mapping) < hi-8
                                 and called_extent(image, at, hi, target, cpu='vr4300'))
    if any(len(called[mapping]) < 8 for mapping in mappings):
        return None
    frames = [at for at in entries if is_frame(int.from_bytes(image[at:at+4], 'big'))]
    candidates = []
    for first, second in (mappings, mappings[::-1]):
        ends = set()
        for at in frames:
            check()
            size = called_extent(image, at, hi, at+first, cpu='vr4300')
            if size:
                ends.add(at+size)
        for start in frames:
            if not called_extent(image, start, hi, start+second, cpu='vr4300'):
                continue
            for end in ends:
                if (not 32 <= start-end <= 8192 or not any(image[end:start])
                        or any(end <= at < start for at in frames)):
                    continue
                if (any(int.from_bytes(image[at:at+4], 'big') == JR_RA for at in range(end, start, 4))
                        or any(end <= target-mapping < start for target in targets for mapping in mappings)):
                    continue
                early = sum(at < end for at in called[first])
                late = sum(at >= start for at in called[second])
                if (early < 8 or late < 8 or len(called[first])-early > early//4
                        or len(called[second])-late > late//4):
                    continue
                if not (0x80000400 <= lo+first < end+first <= 0x80800000
                        and 0x80000400 <= start+second < hi+second <= 0x80800000):
                    continue
                candidates.append(((lo, end, lo+first), (start, hi, start+second)))
    return candidates[0] if len(candidates) == 1 else None


def extend_adjacent_frame(image, found):
    """Extend an inferred bank through a closed frame cut at its tail."""
    from ...arch.mips_code_recovery import called_extent

    result = list(sorted(found))
    for index, (lo, hi, vram) in enumerate(result[:-1]):
        next_start = result[index + 1][0]
        if not hi < next_start <= len(image) or (hi | next_start) % 4:
            continue
        candidates = []
        for at in range(max(lo + 8, hi - 32), hi, 4):
            if (int.from_bytes(image[at-8:at-4], 'big') != JR_RA
                    or not is_frame(int.from_bytes(image[at:at+4], 'big'))):
                continue
            length = called_extent(image, at, next_start, vram + at-lo, cpu='vr4300')
            if length and at + length == next_start:
                candidates.append(at)
        if len(candidates) == 1:
            result[index] = (lo, next_start, vram)
    return result


def extend_tail_wrapper(image, found, taken=()):
    """Keep a closed, straight-line tail wrapper split by an inferred bank end."""
    result = list(sorted(found))
    mapped = (*taken, *found)
    for index, (lo, hi, vram) in enumerate(result):
        following = [start for start, _, _ in (*taken, *result) if start >= hi]
        limit = min(len(image), min(following, default=len(image)), hi+64)
        start = hi-8
        if start < lo+8 or limit < hi+8 or (hi | limit) % 4:
            continue
        # A return and its delay slot separate this entry from its predecessor.
        if int.from_bytes(image[start-8:start-4], 'big') != JR_RA:
            continue
        first = int.from_bytes(image[start:start+4], 'big')
        second = int.from_bytes(image[start+4:start+8], 'big')
        # This wrapper begins by adjusting an argument and scaling the result.
        if first >> 26 != 9 or not 4 <= (first >> 21) & 31 <= 7 or second >> 26 or second & 63 != 0:
            continue
        for at in range(hi, limit-4, 4):
            word = int.from_bytes(image[at:at+4], 'big')
            op = word >> 26
            if op == 2:
                target = ((vram+at-lo+4) & 0xf0000000) | ((word & 0x03ffffff) << 2)
                delay = int.from_bytes(image[at+4:at+8], 'big')
                if any(base <= target < base+end-begin for begin, end, base in mapped) and delay >> 26 not in (1,2,3,4,5,6,7):
                    result[index] = (lo, at+8, vram)
                break
            if op not in (0, 8, 9, 10, 11, 12, 13, 14, 15, 32, 33, 35, 36, 37, 40, 41, 43):
                break
            if op == 0 and word & 63 not in (0, 2, 3, 4, 6, 7, 32, 33, 34, 35, 36, 37, 38, 39, 42, 43):
                break
    return result
