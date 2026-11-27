"""Recover RefPack programs only when independent calls and RAM agree."""
import re

from decompassets import formats, refpack

_HEADER = re.compile(b'\x10\xfb')


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    if len(targets) < 3:
        return []
    candidates = []
    for number, match in enumerate(_HEADER.finditer(rom)):
        if number % 128 == 0:
            check()
        offset = match.start()
        if offset % 2 or offset+5 > len(rom):
            continue
        expanded = int.from_bytes(rom[offset+2:offset+5], 'big')
        if not 0x10000 <= expanded <= 0x800000:
            continue
        try:
            payload, used = refpack.unpack(memoryview(rom)[offset:])
        except (formats.AssetError, IndexError):
            continue
        placements = {}
        for dump in dumps:
            for target in targets:
                at = target & 0x1fffffff
                if at+64 > len(dump):
                    continue
                needle = dump[at:at+64]
                where = payload.find(needle)
                if where < 0 or payload.find(needle, where+1) >= 0:
                    continue
                base = target-where
                if base % 16 or not 0x80000400 <= base < base+len(payload) <= 0x80800000:
                    continue
                start = base & 0x1fffffff
                if start+0x10000 > len(dump) or dump[start:start+0x10000] != payload[:0x10000]:
                    continue
                if any(v < base+len(payload) and base < v+end-begin for begin,end,v in occupied):
                    continue
                placements.setdefault(base, set()).add(target)
        for base, calls in placements.items():
            if len(calls) >= 3:
                candidates.append(dict(offset=offset, size=used, vram=base, calls=sorted(calls), codec='refpack',
                                       expanded=len(payload)))
    # A placement must have a unique source, and separate programs may not
    # claim the same runtime bytes. Neither ambiguity proves a mapping.
    for index, row in enumerate(candidates):
        if any(index != other and row['vram'] < item['vram']+item['expanded']
               and item['vram'] < row['vram']+row['expanded'] for other, item in enumerate(candidates)):
            return []
    return candidates
