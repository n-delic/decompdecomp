"""Read chunked boot executables compressed by a bitstream LZ loader."""

import struct

_LIMIT = 4 * 1024 * 1024


def _decode(source, expected):
    """Decode one bounded stream; reject invalid backreferences and extra output."""
    if not 0 < expected <= _LIMIT:
        return None
    at = 0
    flags = 1
    out = bytearray()

    def byte():
        nonlocal at
        if at >= len(source):
            raise ValueError
        result = source[at]
        at += 1
        return result

    def bit():
        nonlocal flags
        if flags == 1:
            flags = byte() | 0x100
        result = flags & 1
        flags >>= 1
        return result

    def bits(count, value=0):
        for _ in range(count):
            value = value * 2 + bit()
        return value

    try:
        while True:
            if not bit():
                out.append(byte())
            else:
                length = 0
                if bit():
                    length = bits(2)
                    if not length:
                        length = bits(4) + 3
                        if length == 3:
                            value = byte()
                            if not value:
                                break
                            length = value + 18
                length += 2
                count, base = ((5, 1), (7, 33), (9, 161), (10, 673))[bits(2)]
                initial = byte() if count >= 8 else 0
                if count >= 8:
                    count -= 8
                distance = base + bits(count, initial)
                if distance > len(out) or len(out) + length > expected:
                    return None
                for _ in range(length):
                    out.append(out[-distance])
            if len(out) > expected:
                return None
    except (IndexError, ValueError):
        return None
    if len(out) != expected or len(source) - at > 3 or any(value not in (0, 255) for value in source[at:]):
        return None
    return bytes(out)


def unpack(rom, start):
    """Decode the four independently sized chunks of a boot executable."""
    if rom[start:start + 8] != b'BRSOHYES' or start < 0 or start + 40 > len(rom):
        return None
    sizes = struct.unpack_from('>8I', rom, start + 8)
    if any(not 0 < size <= _LIMIT for size in sizes) or sum(sizes[::2]) > _LIMIT:
        return None
    pos = start + 40
    blocks = []
    for expected, stored in zip(sizes[::2], sizes[1::2]):
        if pos + stored > len(rom) or not (block := _decode(rom[pos:pos + stored], expected)):
            return None
        blocks.append(block)
        pos += stored
    return b''.join(blocks)


def replacement_entry(boot, program):
    """The loader swaps its two entry marker halves before replacing the body."""
    if len(boot) < 16 or len(program) < 16 or boot[8:16] != program[8:16]:
        return False
    first, second = struct.unpack_from('>2I', boot)
    new_first, new_second = struct.unpack_from('>2I', program)
    return (first & 0xffff0000 == new_first & 0xffff0000 == 0x3c080000
            and second & 0xffff0000 == new_second & 0xffff0000 == 0x35080000
            and first & 0xffff == new_second & 0xffff
            and second & 0xffff == new_first & 0xffff
            and first & 0xffff != second & 0xffff)


def find(rom, boot_ranges):
    """Identify a replacement executable whose entry repeats the boot stack setup."""
    from .code import _looks_like_code

    matches = []
    start = rom.find(b'BRSOHYES')
    while start >= 0:
        program = unpack(rom, start) if start % 4 == 0 else None
        if program and _looks_like_code(program):
            for lo, hi, vram in boot_ranges:
                if (lo == 0x1000 and hi - lo >= 16 and replacement_entry(rom[lo:lo + 16], program) and vram + len(program) <= 0x80800000):
                    returns = [i for i in range(0, len(program) - 7, 4)
                               if program[i:i + 4] == b'\x03\xe0\x00\x08']
                    if returns:
                        end = returns[-1] + 8
                        matches.append(((vram, program[:end], program[end:]),))
        start = rom.find(b'BRSOHYES', start + 1)
    return matches[0] if len(matches) == 1 else None
