"""Recover a program described by the boot loader's compressed directory."""
import struct

from decompassets.formats import AssetError
from decompassets.two_bit_lz import unpack

from ...arch.mips import JR_RA, is_frame
from ...arch.mips_assets import read


def code_start(data):
    """Skip resident graphics microcode before the first CPU stack frame."""
    for pos in range(0, len(data)-4, 4):
        if is_frame(int.from_bytes(data[pos:pos+4], 'big')):
            return pos
    return None


def find(rom, boot_ranges):
    from .code import _looks_like_code

    matches = []
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom):
            continue
        events = read(base, rom[lo:hi])['events']
        calls = [e for e in events if e['kind'] == 'call']
        targets = {e.get('target') for e in calls}
        for event in calls:
            args = event.get('args') or []
            if (len(args) < 3 or any(not args[i] or args[i].get('kind') != 'constant' for i in (0, 1, 2))
                    or args[2]['value'] != 80):
                continue
            directory = args[0]['value'] & 0xffffffff
            if directory % 4 or directory+80 > len(rom):
                continue
            rows = list(struct.iter_unpack('>4I', rom[directory:directory+80]))
            source, target, size, flags = rows[0]
            if (source != directory+80 or flags or target % 8 or not 0x80000400 <= target < 0x80800000
                    or not 0x1000 <= size <= 4*1024*1024 or target+size > 0x80800000
                    or not source < rows[1][0] <= len(rom)
                    or int.from_bytes(rom[source:source+4], 'big') != size
                    or not any(t is not None and target <= t < target+size for t in targets)):
                continue
            try:
                data, used = unpack(memoryview(rom)[source:rows[1][0]])
            except AssetError:
                continue
            if not _looks_like_code(data) or source+used > rows[1][0]:
                continue
            if any(target < b+e-s and b < target+len(data) for s, e, b in boot_ranges):
                continue
            start = code_start(data)
            returns = [i for i in range(start or 0, len(data)-7, 4)
                       if int.from_bytes(data[i:i+4], 'big') == JR_RA]
            if start is not None and returns and start < returns[-1]:
                end = returns[-1]+8
                matches.append(((target, data[:end], data[end:]),))
    return matches[0] if len(matches) == 1 else None
