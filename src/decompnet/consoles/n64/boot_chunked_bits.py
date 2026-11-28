"""Recover a program from a boot loader's chunked bitstream."""

import hashlib
import struct

from decompassets.n64.chunked_bits import decode

_LIMIT = 4 * 1024 * 1024
# The complete loader was audited against its output in independent RAM captures.
_LOADER = '9f4eee2e007ab9c5a521a86f13eb7590148c4e6607650eccf4092e0660f6ad68'


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
