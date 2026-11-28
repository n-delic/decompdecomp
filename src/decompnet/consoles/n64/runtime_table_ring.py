"""Recover a table-indexed ring-compressed bank proven in a RAM snapshot."""

import struct

# The archive header describes the 0x400-entry directory and its data offset.
_HEADER = b'\x00\x00\x20\x08\x00\x00\x04\x00'
_LIMIT = 2 * 1024 * 1024


def decode(source):
    """Decode one length-prefixed 1 KiB ring stream, returning bytes consumed."""
    if len(source) < 4:
        return None
    size = int.from_bytes(source[:4], 'big')
    if not 0 < size <= _LIMIT:
        return None
    ring = bytearray(1024)
    cursor, at, flags = 958, 4, 0
    out = bytearray()
    try:
        while len(out) < size:
            if not flags & 0x100:
                flags = 0xff00 | source[at]
                at += 1
            if flags & 1:
                values = (source[at],)
                at += 1
                count = 1
            else:
                first, second = source[at], source[at + 1]
                at += 2
                offset = ((second & 0xc0) << 2) | first
                count = (second & 0x3f) + 3
                values = None
            if len(out) + count > size:
                return None
            for _ in range(count):
                value = values[0] if values is not None else ring[offset]
                if values is None:
                    offset = (offset + 1) & 1023
                out.append(value)
                ring[cursor] = value
                cursor = (cursor + 1) & 1023
            flags >>= 1
    except IndexError:
        return None
    return bytes(out), at


def find(rom, dumps, occupied=(), check=lambda: None):
    """Require a complete RAM match and several internal calls in the loaded bank."""
    if not dumps:
        return []
    candidates = []
    at = 0
    while (at := rom.find(_HEADER, at)) >= 0:
        check()
        table = at
        at += len(_HEADER)
        if table % 4 or table + 0x2008 > len(rom):
            continue
        data = table + 0x2008
        for index in range(1024):
            check()
            rel, size = struct.unpack_from('>II', rom, table + 8 + index * 8)
            if rel == size == 0xffffffff:
                break
            start = data + rel
            if not 4 <= size <= _LIMIT * 2 or start < data or start + size > len(rom):
                break
            decoded = decode(memoryview(rom)[start:start + size])
            if not decoded:
                continue
            payload, used = decoded
            if len(payload) < 256 or any(rom[start + used:start + size]):
                continue
            for dump in dumps:
                pos = dump.find(payload[:128])
                if pos < 0 or pos % 16 or pos < 0x400 or pos + len(payload) > len(dump):
                    continue
                if dump.find(payload[:128], pos + 1) >= 0 or dump[pos:pos + len(payload)] != payload:
                    continue
                base = 0x80000000 + pos
                if any(v < base + len(payload) and base < v + hi - lo for lo, hi, v in occupied):
                    continue
                words = struct.unpack(f'>{len(payload) // 4}I', payload[:len(payload) // 4 * 4])
                calls = sorted({0x80000000 | ((w & 0x3ffffff) << 2) for w in words if w >> 26 == 3
                                and base <= (0x80000000 | ((w & 0x3ffffff) << 2)) < base + len(payload)})
                if len(calls) >= 4:
                    candidates.append(dict(codec='table_ring', offset=start, size=used,
                                           expanded=len(payload), vram=base, calls=calls))
                    break
    unique = {(row['offset'], row['vram']): row for row in candidates}
    return list(unique.values())
