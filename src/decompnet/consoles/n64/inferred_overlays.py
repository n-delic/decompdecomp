"""Recover overlapping code banks when their self-calls prove a different placement."""

import collections
import struct

from ...arch.mips import JR_RA, is_frame

_MIN_CALLS = 32
_MAX_PAIRS = 1_000_000


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
        if (mapping == vram-lo or score < _MIN_CALLS or score < second*6
                or score < scores[vram-lo]*4
                or not 0x80000400 <= address < address+hi-lo <= 0x80800000):
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
            actual = len(calls & {address+offset for offset in offsets})
            previous = len(calls & {old+offset for offset in offsets})
            if address != old and actual >= 3 and actual > previous:
                parts.append((start,end,address))
                corrected.add(start)
            else:
                parts.append((start,end,old))
        if corrected:
            result.extend(parts)
            changed.update(corrected)
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


def scope(config, found, scoped):
    """Put each overlapping connected set into one exclusive address space."""
    if not scoped:
        return
    segments = {seg['start']: seg for seg in config['segments']
                if isinstance(seg, dict) and seg.get('type') == 'code'}
    remaining = {lo for lo,_,_ in found if lo in scoped}
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
