"""Recover an RNC method-2 program referenced by its boot loader."""
import struct

from decompassets.rnc import RncError, unpack_m2

from ...arch.mips import JR_RA
from ...arch.mips_assets import read
from .boot_two_bit import code_start

_LIMIT = 4 * 1024 * 1024


def _constants(code):
    """Constants built from a LUI and a nearby signed ADDIU on the same register."""
    words = [w for w, in struct.iter_unpack('>I', code[:len(code) & ~3])]
    values = set()
    for i, word in enumerate(words):
        if word >> 26 != 15:
            continue
        reg = (word >> 16) & 31
        for following in words[i+1:i+9]:
            if following >> 26 == 9 and (following >> 21) & 31 == reg and (following >> 16) & 31 == reg:
                low = following & 0xffff
                values.add((((word & 0xffff) << 16) + (low-0x10000 if low & 0x8000 else low)) & 0xffffffff)
                break
            if (following >> 16) & 31 == reg and following >> 26 in (9, 13, 15, 35):
                break
    return values


def find(rom, boot_ranges):
    """Require the boot's source constant, output argument and calls into the decoded program."""
    from .code import _looks_like_code

    matches = {}
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom):
            continue
        code = rom[lo:hi]
        constants = _constants(code)
        events = read(base, code)['events']
        calls = [event for event in events if event['kind'] == 'call']
        destinations = {arg['value'] & 0xffffffff for event in calls for arg in event.get('args', ())
                        if arg and arg.get('kind') == 'constant' and 0x80000400 <= arg['value'] < 0x80800000}
        targets = {event['target'] for event in calls if event.get('target') is not None}
        for source in constants:
            if source % 4 or rom[source:source+4] != b'RNC\x02':
                continue
            try:
                data = unpack_m2(rom[source:], max_output=_LIMIT)
            except RncError:
                continue
            start = code_start(data)
            if start is None or not _looks_like_code(data):
                continue
            ends = [i+8 for i in range(start, len(data)-7, 4)
                    if int.from_bytes(data[i:i+4], 'big') == JR_RA]
            if not ends:
                continue
            end = ends[-1]
            for dest in destinations:
                if (dest % 16 or dest+len(data) > 0x80800000
                        or sum(dest+start <= target < dest+end for target in targets) < 2
                        or any(dest < b+e-s and b < dest+len(data) for s, e, b in boot_ranges)):
                    continue
                matches[(source, dest)] = ((dest, data[:end], data[end:]),)
    return next(iter(matches.values())) if len(matches) == 1 else None
