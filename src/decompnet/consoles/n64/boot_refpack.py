"""Recover an OVLN program loaded and expanded by the boot code."""

import re
import struct

from decompassets.formats import AssetError
from decompassets.refpack import unpack

from ...arch.mips_assets import read


def find(rom, boot_ranges):
    """Bind the ROM directory pointer to a boot call and validate its stream."""
    from .code import _looks_like_code

    sources = set()
    indirect = False
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom):
            continue
        for event in read(base, rom[lo:hi])['events']:
            if event['kind'] != 'call':
                continue
            if event.get('target') is None:
                indirect = True
            args = event.get('args') or []
            if len(args) >= 3 and args[0] and args[0].get('kind') == 'constant' and args[2] and args[2].get('value') == 32:
                sources.add(args[0]['value'] & 0xffffffff)
    if not indirect:
        return None
    matches = []
    for match in re.finditer(b'OVLN', rom):
        at = match.start()
        if at not in sources or at % 4 or at + 21 > len(rom):
            continue
        stored, target, entry = struct.unpack_from('>3I', rom, at+4)
        if not 21 <= stored <= 4*1024*1024 or at + stored > len(rom) or target % 16:
            continue
        try:
            data, consumed = unpack(rom[at+16:at+stored])
        except AssetError:
            continue
        if (consumed != stored-16 or not 0x80000400 <= target < entry < target+len(data) <= 0x80800000
                or any(target < base+end-lo and base < target+len(data) for lo, end, base in boot_ranges)
                or not _looks_like_code(data)):
            continue
        returns = [i for i in range(0, len(data)-7, 4) if data[i:i+4] == b'\x03\xe0\x00\x08']
        if returns:
            end = returns[-1]+8
            matches.append(((target, data[:end], data[end:]),))
    return matches[0] if len(matches) == 1 else None
