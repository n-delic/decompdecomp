"""Find executable streams split into loader-sized MIO0 chunks without their headers."""

import struct

_CHUNK = 0x2000
_MAX_OUTPUT = 0x400000


def decode(rom, row):
    """Recreate a table entry's output, including references into previous chunks."""
    if not isinstance(row, dict):
        return None
    table, start, end, expected = (row.get(k) for k in ('table_offset', 'offset', 'end', 'size'))
    if (any(type(x) is not int for x in (table, start, end, expected)) or table < 0
            or table + 24 > len(rom) or start < 0 or start >= end or end > len(rom)
            or not 0 < expected <= _MAX_OUTPUT):
        return None
    first, last, size, next_start, next_end, next_size = struct.unpack_from('>6I', rom, table)
    if ((first, last, size) != (start, end, expected) or next_start != end
            or not end < next_end <= len(rom) or not 0 < next_size <= _MAX_OUTPUT):
        return None
    output = bytearray()
    for pos in range(start, end, _CHUNK):
        chunk = memoryview(rom)[pos:min(pos + _CHUNK, end)]
        if len(chunk) < 8:
            return None
        size, links, literals = struct.unpack_from('>HHH', chunk, 2)
        limit = len(output) + size
        if not size or limit > expected or not 8 <= links <= literals < len(chunk):
            return None
        link_end = literals
        mask = 8
        bits = 0
        while len(output) < limit:
            if not bits:
                if mask + 4 > links:
                    return None
                control = int.from_bytes(chunk[mask:mask + 4], 'big')
                mask += 4
                bits = 32
            if control & 0x80000000:
                if literals >= len(chunk):
                    return None
                output.append(chunk[literals])
                literals += 1
            else:
                if links + 2 > link_end:
                    return None
                pair = int.from_bytes(chunk[links:links + 2], 'big')
                links += 2
                distance, length = (pair & 0xfff) + 1, (pair >> 12) + 3
                if distance > len(output) or len(output) + length > limit:
                    return None
                for _ in range(length):
                    output.append(output[-distance])
            control = (control << 1) & 0xffffffff
            bits -= 1
    return bytes(output) if len(output) == expected else None


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    """Require a cartridge table, an independently called entry, and 64 KiB of identical RAM."""
    if not targets or not dumps:
        return []
    found = []
    for at in range(0x1000, len(rom) - 24, 4):
        if not at % 0x10000:
            check()
        start, end, size = struct.unpack_from('>3I', rom, at)
        if not (0x1000 <= start < end <= len(rom) and 0x10000 <= size <= _MAX_OUTPUT
                and _CHUNK <= end - start <= _MAX_OUTPUT):
            continue
        next_start, next_end, next_size = struct.unpack_from('>3I', rom, at + 12)
        if next_start != end or not end < next_end <= len(rom) or not 0 < next_size <= _MAX_OUTPUT:
            continue
        row = {'codec': 'chunked_mio0', 'table_offset': at, 'offset': start, 'end': end, 'size': size}
        payload = decode(rom, row)
        if payload is None:
            continue
        placements = {}
        for ram in dumps:
            for target in targets:
                offset = target & 0x1fffffff
                if offset + 64 > len(ram):
                    continue
                needle = ram[offset:offset + 64]
                where = payload.find(needle)
                if where < 0 or payload.find(needle, where + 1) >= 0:
                    continue
                base = target - where
                physical = base & 0x1fffffff
                if (base % 16 or not 0x80000400 <= base < base + len(payload) <= 0x80800000
                        or physical + 0x10000 > len(ram) or ram[physical:physical + 0x10000] != payload[:0x10000]
                        or any(v < base + len(payload) and base < v + hi - lo for lo, hi, v in occupied)):
                    continue
                placements.setdefault(base, set()).add(target)
        for base, calls in placements.items():
            found.append({**row, 'vram': base, 'calls': sorted(calls)})
    return found if len(found) == 1 else []
