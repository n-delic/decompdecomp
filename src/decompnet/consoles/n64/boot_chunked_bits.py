"""Recover a program from a boot loader's chunked bitstream."""

import hashlib
import struct

from decompassets.n64.chunked_bits import decode

_LIMIT = 4 * 1024 * 1024
# The complete loader was audited against its output in independent RAM captures.
_LOADER = '9f4eee2e007ab9c5a521a86f13eb7590148c4e6607650eccf4092e0660f6ad68'
_PRIMARY = '10d91d2c6916496106b399cc05b6ee0663d10a3ae9fd8003cad4936ad5476b71'
# Directory index, offset, output and stored sizes, runtime address, text bounds,
# and the full image digest. The text was checked against independent RAM captures.
EXTRA_PROGRAMS = (
    (1, 0x33748, 0x909b0, 0x47dd4, 0x8007a860, 0xed8, 0x7f858,
     '59f8ea8457eebd21ff8936094b0ee5fb875158644e46652e6da8ba30d2249b0f'),
    (2, 0x7b51c, 0x90810, 0x49cb8, 0x8007a860, 0x101f0, 0x82c2c,
     'aa61544efd5d4fdead01fe0064c4be710d3675a763e7ec86b2702a4795a8d785'),
    (4, 0xc51d8, 0x33310, 0x1a9b0, 0x8010b210, 0x8, 0x33310,
     '379e415c25f7841e3053233aecc6afc146dee3ec62f31a32502df74f97decc3d'),
    (5, 0xdfb88, 0x337e0, 0x1a6c8, 0x8010b210, 0x1048, 0x2be04,
     '96d59f2c1077f726937795b45ac0a7fa10fa1d5a0942c5fb589ac7dd592eb451'),
)


def drop_impossible_bss(segments):
    """Discard a mistaken size larger than all N64 RDRAM, keeping valid sizes."""
    for segment in segments:
        if (isinstance(segment, dict) and segment.get('type') == 'code'
                and isinstance(segment.get('bss_size'), int) and segment['bss_size'] > 0x800000):
            segment.pop('bss_size')


def code_start(vram, text):
    if vram == 0x80000400:
        return 80
    return next((start for _, _, _, _, address, start, end, _ in EXTRA_PROGRAMS
                 if address == vram and end == len(text)), None)


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
    primary = (0x80000400, data[:end], data[end:])
    if hashlib.sha256(data).hexdigest() != _PRIMARY:
        return (primary,)
    programs = [primary]
    for index, offset, expanded, packed, vram, start, end, digest in EXTRA_PROGRAMS:
        if directory + 12 * (index + 1) > len(rom):
            return (primary,)
        if struct.unpack_from('>III', rom, directory + 12 * index) != (offset, expanded, 0x80000000 | packed):
            return (primary,)
        source = directory + offset
        if source + packed > len(rom):
            return (primary,)
        decoded = decode(rom[source:source + packed], expanded)
        if (decoded is None or not 0 <= start < end <= len(decoded)
                or hashlib.sha256(decoded).hexdigest() != digest):
            return (primary,)
        programs.append((vram, decoded[:end], decoded[end:]))
    return tuple(programs)
