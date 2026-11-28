"""Recover a program from a boot loader's chunked bitstream."""

import hashlib
import struct

_LIMIT = 4 * 1024 * 1024
# The complete loader was audited against its output in independent RAM captures.
_LOADER = '9f4eee2e007ab9c5a521a86f13eb7590148c4e6607650eccf4092e0660f6ad68'


class _Truncated(Exception):
    pass


class _Bits:
    def __init__(self, data, offset):
        self.data, self.at = data, offset * 8

    def read(self, count):
        if self.at + count > len(self.data) * 8:
            raise _Truncated
        result = 0
        for _ in range(count):
            result = result * 2 + ((self.data[self.at // 8] >> (7 - self.at % 8)) & 1)
            self.at += 1
        return result


def decode(source, expected):
    """Decode literals and bounded backward copies across 4 KiB input pages."""
    if not 0 < expected <= _LIMIT or not 6 <= len(source) <= _LIMIT or source[:5] != b'\x03\0\0\x10\0':
        return None
    out = bytearray()
    horizon, width = 2, 1
    try:
        for chunk in range(0, len(source), 4096):
            bits = _Bits(source[chunk:chunk + 4096], 5 if not chunk else 0)
            while len(bits.data) * 8 - bits.at >= 8 and len(out) < expected:
                if bits.read(1):
                    length = bits.read(7)
                    if len(out) + length > expected:
                        return None
                    out.extend(bits.read(8) for _ in range(length))
                    continue
                prefix = 0
                while prefix < 7 and not bits.read(1):
                    prefix += 1
                length = ((1 << prefix) | bits.read(prefix)) if prefix else 1
                length += 2
                if len(out) + length > expected or not out:
                    return None
                while len(out) >= horizon:
                    if horizon < 65536:
                        width += 1
                    horizon *= 2
                threshold = (1 << width) - 1 - min(len(out), 65536)
                if threshold == -1:
                    distance = bits.read(width)
                else:
                    distance = bits.read(width - 1)
                    if distance >= threshold:
                        distance = distance * 2 + bits.read(1) - threshold
                begin = len(out) - distance
                if not 0 <= begin < len(out):
                    return None
                for _ in range(length):
                    out.append(out[begin])
                    begin += 1
    except _Truncated:
        return None
    return bytes(out) if len(out) == expected else None


def find(rom, boot_ranges):
    """Require the audited loader, its directory and a closed decoded program."""
    from .code import _looks_like_code

    # Splat may split this contiguous mapping at the entrypoint's call.
    covered = sorted((lo, hi) for lo, hi, base in boot_ranges if base - lo == 0x801ff400)
    cursor = 0x1000
    for lo, hi in covered:
        if lo <= cursor < hi:
            cursor = hi
    if cursor < 0x16e0 or len(rom) < 0x16ec:
        return None
    if hashlib.sha256(rom[0x1000:0x16e0]).hexdigest() != _LOADER:
        return None
    pointer = struct.unpack_from('>I', rom, 0x16e8)[0]
    if not 0xb0000000 <= pointer < 0xb0000000 + len(rom):
        return None
    directory = pointer - 0xb0000000
    if directory % 4 or directory + 16 > len(rom):
        return None
    first, expanded, encoded, following = struct.unpack_from('>4I', rom, directory)
    packed = encoded & 0x7fffffff
    if (not encoded & 0x80000000 or not 16 <= first <= 4096 or first % 12
            or not 0x1000 <= expanded <= _LIMIT or not 0 < packed <= _LIMIT
            or following != (first + packed + 1) & ~1 or directory + following > len(rom)):
        return None
    data = decode(rom[directory + first:directory + first + packed], expanded)
    if not data or any(data[:80]) or not _looks_like_code(data[80:]):
        return None
    returns = [i for i in range(80, len(data) - 7, 4) if data[i:i + 4] == b'\x03\xe0\x00\x08']
    if len(returns) < 16:
        return None
    end = returns[-1] + 8
    return ((0x80000400, data[:end], data[end:]),)
