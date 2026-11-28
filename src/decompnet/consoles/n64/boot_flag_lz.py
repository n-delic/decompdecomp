"""Recover an audited boot loader's flag-byte compressed program."""

import hashlib
import struct

_DECODER_SHA256 = 'd36f74995294c1ff63104fa705c99f2f735bc29274087d109e28c56d314387e9'
_LIMIT = 4 * 1024 * 1024


def decode(source):
    """Decode bounded flag-byte literals and backward copies, requiring the end marker and declared size."""
    if len(source) < 5 or source[0] != 1:
        return None
    expected = int.from_bytes(source[1:4], 'big')
    if not 0 < expected <= _LIMIT:
        return None
    at, out = 4, bytearray()
    while at < len(source) and len(out) <= expected:
        flags = source[at]
        at += 1
        if flags == 0:
            if at + 8 > len(source) or len(out) + 8 > expected:
                return None
            out.extend(source[at:at+8])
            at += 8
            continue
        for bit in range(7, -1, -1):
            if not flags & (1 << bit):
                if at >= len(source) or len(out) >= expected:
                    return None
                out.append(source[at])
                at += 1
                continue
            if at + 2 > len(source):
                return None
            hi, lo = source[at:at+2]
            at += 2
            distance = ((hi & 0xf0) << 4) | lo
            if not distance:
                return bytes(out) if len(out) == expected else None
            count = hi & 15
            if not count:
                if at >= len(source):
                    return None
                count = source[at] + 17
                at += 1
            else:
                count += 1
            if distance > len(out) or len(out) + count > expected:
                return None
            for _ in range(count):
                out.append(out[-distance])
    return None


def find(rom, boot_ranges):
    """Accept the exact audited loader, its source argument and its direct jump to the decoded program."""
    from .code import _looks_like_code

    matches = []
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom) or hi-lo < 0x298:
            continue
        entry = int.from_bytes(rom[8:12], 'big')
        # This loader's short entry trampoline clears memory and jumps to the
        # decoding routine's caller 0x50 bytes into the same boot image.
        at = lo + entry - base + 0x50
        if not lo <= at <= hi - 0x28:
            continue
        words = struct.unpack_from('>10I', rom, at)
        if (words[0] >> 16 != 0x3c04 or words[1] >> 16 != 0x2484
                or words[2] != 0x40124800 or words[4] != 0
                or words[7] != 0x40064800 or words[8] != 0x0800c400 or words[9] != 0x02402821
                or words[3] >> 26 != 3):
            continue
        source = ((words[0] & 0xffff) << 16) + struct.unpack('>h', struct.pack('>H', words[1] & 0xffff))[0]
        decoder = ((entry+0x5c) & 0xf0000000) | ((words[3] & 0x3ffffff) << 2)
        pos = lo + decoder - base
        if not lo <= pos <= hi-0x218 or hashlib.sha256(rom[pos:pos+0x218]).hexdigest() != _DECODER_SHA256:
            continue
        start = lo + source - base
        if start < lo or start >= len(rom):
            continue
        data = decode(rom[start:min(len(rom), start+_LIMIT)])
        if not data or not _looks_like_code(data):
            continue
        returns = [i for i in range(0, len(data)-7, 4) if data[i:i+4] == b'\x03\xe0\x00\x08']
        if returns:
            end = returns[-1] + 8
            matches.append(((0x80031000, data[:end], data[end:]),))
    return matches[0] if len(matches) == 1 else None
