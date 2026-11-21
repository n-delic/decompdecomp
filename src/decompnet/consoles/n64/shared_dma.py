"""Resident DMA calls that select alternative code for the same RAM slot."""

import struct
from collections import defaultdict
from itertools import pairwise

from ...arch.mips import JR_RA, is_frame
from .raw_slots import Slot, _address


def _valid(image, slot, floor):
    lo, hi, base = slot.start, slot.end, slot.vram
    if (not floor <= lo < hi <= len(image) or (lo | hi | base) % 16
            or not 0x80000400 <= base < base+hi-lo <= 0x80800000):
        return False
    words = [word for word, in struct.iter_unpack('>I', image[lo:min(lo+0x1000, hi)])]
    return JR_RA in words and (is_frame(words[0]) or JR_RA in words[:8] and any(map(is_frame, words)))


def _constants(words):
    high_a, high_b, low_b, low_a, move, subtract, call, delay = words
    a, b = (high_a >> 16) & 31, (high_b >> 16) & 31
    if (high_a >> 21 != 0x1e0 or high_b >> 21 != 0x1e0 or not a or not b or a == b
            or low_a >> 16 != 0x2400 | a << 5 | a or low_b >> 16 != 0x2400 | b << 5 | b
            or move != a << 21 | 4 << 11 | 0x25
            or subtract != b << 21 | a << 16 | 6 << 11 | 0x23
            or call >> 26 != 3 or delay >> 16 != 0x3c05):
        return None
    return call, Slot(_address(high_a, low_a), _address(high_b, low_b), (delay & 65535) << 16)


def _indexed(image, words, lo, base, calls, mappings, floor):
    result = set()
    for index in range(17, len(words)-1):
        if words[index] not in calls or words[index+1] >> 16 != 0x3c05:
            continue
        window = words[index-16:index]
        for high, low, scale, add, last, first in zip(window, window[1:], window[2:], window[3:],
                                                     window[4:], window[5:]):
            table = (high >> 16) & 31
            scaled, row = (scale >> 11) & 31, (add >> 11) & 31
            if (high >> 21 != 0x1e0 or low >> 16 != 0x2400 | table << 5 | table
                    or scale & 0xffe007ff != 0xc0
                    or add != scaled << 21 | table << 16 | row << 11 | 0x21
                    or last >> 21 != 0x460 | row or last & 65535 != 4
                    or first >> 21 != 0x460 | row or first & 65535):
                continue
            address = _address(high, low)
            vram = (words[index+1] & 65535) << 16
            for delta in {base-lo, *mappings}:
                start = address-delta
                if not 0 <= start <= len(image)-16 or start%4:
                    continue
                records = []
                for at in range(start, min(start+64*8, len(image)-7), 8):
                    first, last = struct.unpack_from('>II', image, at)
                    slot = Slot(first, last, vram)
                    if not _valid(image, slot, floor) or records and records[-1].end != first:
                        break
                    records.append(slot)
                if len(records) >= 3:
                    result.update(records)
    return result


def read(image, resident, inferred):
    floor = max(end for _, end, _ in resident)
    groups = defaultdict(set)
    for lo, hi, _ in resident:
        words = [word for word, in struct.iter_unpack('>I', image[lo:hi])]
        for i in range(len(words)-7):
            if match := _constants(words[i:i+8]):
                call, slot = match
                if _valid(image, slot, floor):
                    groups[call, slot.vram].add(slot)
    records = set()
    calls = set()
    for (call, _), slots in groups.items():
        if len(slots) >= 3 and any(a.end == b.start for a, b in pairwise(sorted(slots, key=lambda s: s.start))):
            calls.add(call)
            records.update(slots)
    if not calls:
        return []
    # The indexed loader may itself be in an independently recovered resident range.
    mappings = {base-lo for lo, _, base in resident+inferred}
    for lo, hi, base in resident+inferred:
        words = [word for word, in struct.iter_unpack('>I', image[lo:hi])]
        records.update(_indexed(image, words, lo, base, calls, mappings, floor))
    banks = sorted(records, key=lambda s: s.start)
    return banks if all(a.end <= b.start for a, b in pairwise(banks)) else []


def add(config, image, resident, inferred, records, log):
    from .raw_slots import add as add_slots
    from .raw_slots import recover_registered

    outside = []
    for lo, hi, base in inferred:
        start = lo
        for bank in records:
            if bank.end <= start or hi <= bank.start:
                continue
            if start < bank.start:
                outside.append((start, bank.start, base+start-lo))
            start = max(start, bank.end)
        if start < hi:
            outside.append((start, hi, base+start-lo))
    recovered = recover_registered(image, records, resident+outside, log)
    return add_slots(config, image, sorted(outside+recovered), records, log, preserve_gaps=True)
