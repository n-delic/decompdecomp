"""Recover code decoded in place by a backwards byte-XOR boot loop."""

import struct

_LIMIT = 4 * 1024 * 1024


def _signed(value):
    return value - 0x10000 if value & 0x8000 else value


def _loop(words):
    """Read the two bounds only when the complete loop and both branches agree."""
    if len(words) != 13:
        return None
    a, b, c, d, guard, nop, load, previous, xor, store, step, branch, delay = words
    end_reg, start_reg = (a >> 16) & 31, (c >> 16) & 31
    x, y = (load >> 16) & 31, (previous >> 16) & 31
    if (len({0, end_reg, start_reg, x, y}) != 5
            or a >> 21 != 0x1e0 or c >> 21 != 0x1e0
            or b >> 16 != (9 << 10 | end_reg << 5 | end_reg)
            or d >> 16 != (9 << 10 | start_reg << 5 | start_reg)
            or guard != (4 << 26 | start_reg << 21 | end_reg << 16 | 8)
            or nop or delay
            or load != (36 << 26 | end_reg << 21 | x << 16)
            or previous != (36 << 26 | end_reg << 21 | y << 16 | 0xffff)
            or xor != (x << 21 | y << 16 | x << 11 | 0x26)
            or store != (40 << 26 | end_reg << 21 | x << 16)
            or step != (9 << 26 | end_reg << 21 | end_reg << 16 | 0xffff)
            or branch != (5 << 26 | start_reg << 21 | end_reg << 16 | 0xfffa)):
        return None
    end = ((a & 0xffff) << 16) + _signed(b & 0xffff)
    start = ((c & 0xffff) << 16) + _signed(d & 0xffff)
    return start, end + 1


def find(rom, boot_ranges):
    """Require the loader's exact in-place loop, its mapped source and decoded code."""
    from .code import _looks_like_code

    found = []
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or not 0 <= lo < hi <= len(rom):
            continue
        for at in range(lo, min(hi, 0x10000) - 51, 4):
            bounds = _loop(struct.unpack_from('>13I', rom, at))
            if bounds is None:
                continue
            start, end = bounds
            size = end - start
            source = lo + start - base
            if (not 0x80000400 <= start < end <= 0x80800000 or start % 4 or size % 4
                    or not 0x1000 <= size <= _LIMIT or source < hi or source + size > len(rom)
                    or any(a < source + size and source < b for a, b, _ in boot_ranges)):
                continue
            encoded = rom[source:source + size]
            decoded = bytes([encoded[0]]) + bytes(a ^ b for a, b in zip(encoded[1:], encoded))
            if not _looks_like_code(decoded):
                continue
            returns = [i for i in range(0, size - 7, 4) if decoded[i:i + 4] == b'\x03\xe0\x00\x08']
            if returns:
                boundary = returns[-1] + 8
                found.append(((start, decoded[:boundary], decoded[boundary:]),))
    return found[0] if len(found) == 1 else None
