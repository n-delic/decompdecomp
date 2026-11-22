"""Recover a boot executable stored in a bounded ring-buffer LZSS stream."""

import struct

_LIMIT = 4 * 1024 * 1024


def decode(source, expected):
    if not 0 < expected <= _LIMIT:
        return None
    ring = bytearray(4096)
    position, at, flags = 0xfee, 0, 0
    output = bytearray()
    try:
        while len(output) < expected:
            flags >>= 1
            if not flags & 0x100:
                flags = source[at] | 0xff00
                at += 1
            if flags & 1:
                values = (source[at],)
                at += 1
            else:
                x, y = source[at], source[at + 1]
                at += 2
                offset, count = x | (y & 0xf0) << 4, (y & 15) + 3
                if len(output) + count > expected:
                    return None
                values = None
            for i in range(1 if values else count):
                value = values[0] if values else ring[(offset + i) & 0xfff]
                output.append(value)
                ring[position] = value
                position = (position + 1) & 0xfff
    except IndexError:
        return None
    return bytes(output) if at == len(source) else None


def find(rom, boot_ranges):
    """Require the mapped header and a boot call to its expanded address."""
    from .code import _looks_like_code

    matches = []
    at = rom.find(b'LZSS')
    while at >= 0:
        start = at - 12
        if start >= 0 and start % 4 == 0 and at + 12 <= len(rom):
            one, zero, header, _, size, packed = struct.unpack_from('>III4sII', rom, start)
            if (one, zero, header) == (1, 0, 24) and 0 < packed <= _LIMIT and start + 24 + packed <= len(rom):
                for lo, hi, base in boot_ranges:
                    if lo != 0x1000 or start < hi:
                        continue
                    target = base + start - lo
                    if target + size > 0x80800000:
                        continue
                    call = 0x0c000000 | ((target >> 2) & 0x03ffffff)
                    calls = any(struct.unpack_from('>I', rom, pos)[0] == call
                                for begin, end, _ in boot_ranges for pos in range(begin, end - 3, 4))
                    if not calls:
                        continue
                    data = decode(rom[start + 24:start + 24 + packed], size)
                    if not data or not _looks_like_code(data):
                        continue
                    returns = [i for i in range(0, len(data) - 7, 4) if data[i:i + 4] == b'\x03\xe0\x00\x08']
                    if returns:
                        end = returns[-1] + 8
                        matches.append(((target, data[:end], data[end:]),))
        at = rom.find(b'LZSS', at + 1)
    return matches[0] if len(matches) == 1 else None
