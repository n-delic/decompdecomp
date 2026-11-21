"""Recover overlapping code banks when their self-calls prove a different placement."""

import collections
import struct

from ...arch.mips import JR_RA, is_frame

_MIN_CALLS = 32
_MAX_PAIRS = 1_000_000


def recover(image, found, taken=(), check=lambda: None, *, changed=()):
    """Return corrected inferred ranges and the starts needing independent splat scopes."""
    result = list(found)
    changed = set(changed)
    for index, (lo, hi, vram) in enumerate(found):
        check()
        if lo < 0 or hi - lo < 4096 or hi > len(image) or (lo | hi) % 4:
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
