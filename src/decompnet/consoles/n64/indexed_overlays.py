"""Shared overlays described by parallel start, end and destination arrays."""

import re
import struct
from itertools import pairwise

from ...arch.mips import is_frame
from .copied_overlays import Bank

_SHARED = re.compile(rb'(?P<base>\x80[\x00-\x7f]..)(?P=base){2,15}', re.DOTALL)


def _loader_references(words, addresses):
    """Look for all three array addresses materialized in the loader’s setup."""
    matches = {}
    for index, word in enumerate(words):
        if word >> 26 != 15:
            continue
        register = (word >> 16) & 31
        for nearby in words[index + 1:index + 7]:
            if nearby >> 16 != 0x2400 | (register << 5) | register:
                continue
            low = nearby & 0xffff
            result = ((word & 0xffff) << 16) + (low if low < 0x8000 else low - 0x10000)
            if result in addresses:
                matches.setdefault(result, []).append(index)
    return any(all(any(abs(start - near) < 32 for near in matches.get(address, ()))
                   for address in addresses[1:])
               for start in matches.get(addresses[0], ()))


def read(image, taken):
    """Require the indexed loader, overlay code entries and both ROM and RAM extents to agree."""
    for start, end, vram in taken:
        if start >= 0x10000:
            continue
        words = [word for word, in struct.iter_unpack('>I', image[start:end])]
        delta = vram - start
        for match in _SHARED.finditer(image, start, min(len(image), 0x100000)):
            if match.start() % 4:
                continue
            count = (match.end() - match.start()) // 4
            size = 4 * count
            record = match.start() - 2 * size
            if record <= end or match.end() + size > len(image):
                continue
            starts = struct.unpack_from(f'>{count}I', image, record)
            ends = struct.unpack_from(f'>{count}I', image, record + size)
            ram_ends = struct.unpack_from(f'>{count}I', image, match.end())
            base = int.from_bytes(match['base'], 'big')
            if base % 16 or any(left >= right for left, right in pairwise(starts)):
                continue
            records = []
            for first, last, ram_end in zip(starts, ends, ram_ends):
                if (not record + 4*size <= first < last <= len(image)
                        or (first | last | ram_end) % 16 or not 0 <= ram_end - base - (last-first) <= 128
                        or not 0x80000400 <= base < ram_end <= 0x80800000
                        or not is_frame(struct.unpack_from('>I', image, first)[0])):
                    break
                records.append(Bank(first, last, base, first, last, ram_end))
            if (len(records) == count and not any(left.end > right.start for left, right in pairwise(records))
                    and _loader_references(words, tuple(record + index*size + delta for index in range(3)))):
                return records
    return []


def _constant(high, low, destination):
    source = (high >> 16) & 31
    if high >> 21 != 0x1e0 or low >> 16 != 0x2400 | (source << 5) | destination:
        return None
    half = low & 0xffff
    return ((high & 0xffff) << 16) + (half if half < 0x8000 else half - 0x10000)


def _cache_span(words, *, saved_return=False):
    high_start, high_end, low_start = words[:3]
    low_end, call, delay = words[4:] if saved_return else words[3:]
    register = (high_end >> 16) & 31
    if call >> 26 != 3 or delay != 0x00042823 | (register << 21):
        return None
    start = _constant(high_start, low_start, 4)
    end = _constant(high_end, low_end, register)
    if start is not None and end is not None and start < end:
        return start, end
    return None


def resident_banks(image, taken, *, before):
    """Follow constant DMA bounds preceded by adjoining text and data cache spans."""
    records = {}
    for start, end, _ in taken:
        if start >= 0x10000:
            continue
        words = [word for word, in struct.iter_unpack('>I', image[start:end])]
        for i in range(13, len(words)-7):
            block = words[i:i + 8]
            register = (block[1] >> 16) & 31
            if block[6] >> 26 != 3 or block[7] != 0x00053023 | (register << 21):
                continue
            first = _constant(block[0], block[2], 5)
            last = _constant(block[1], block[3], register)
            base = _constant(*block[4:6], 4)
            if (None in (first, last, base) or not end <= first < last <= before
                    or (first | last | base) % 16 or not 0x80000400 <= base < base+last-first <= 0x80800000):
                continue
            text = _cache_span(words[i-13:i-6], saved_return=True)
            data = _cache_span(words[i-6:i])
            if not (text and data and text[0] == base and text[1] == data[0] and data[1] == base+last-first):
                continue
            target = block[6]
            records[first] = Bank(first, last, base, first, first+text[1]-base, data[1]), target
    identified = sorted(records.values(), key=lambda entry: entry[0].start)
    if len(identified) < 2:
        return []
    first, loader = identified[0]
    if (identified[-1][0].end != before or any(bank.vram != first.vram or target != loader for bank,target in identified)
            or any(left[0].end != right[0].start for left,right in pairwise(identified))):
        return []
    return [bank for bank, _ in identified]
