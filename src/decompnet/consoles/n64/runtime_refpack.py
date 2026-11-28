"""Recover RefPack programs only when independent calls and RAM agree."""
import re

from decompassets import formats, refpack

_HEADER = re.compile(b'\x10\xfb')


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    from ...arch.mips_code_recovery import called_extent, leaf_extent

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
        if not 0x1000 <= expanded <= 0x800000:
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
                if any(v < base+len(payload) and base < v+end-begin for begin,end,v in occupied):
                    continue
                # Loaded programs can change their data after decompression.
                # Prove each called body in full in one snapshot instead of
                # requiring an immutable prefix containing unrelated data.
                length = (leaf_extent(payload, where, len(payload), target, cpu='vr4300')
                          or called_extent(payload, where, len(payload), target, cpu='vr4300'))
                if not length or at+length > len(dump) or dump[at:at+length] != payload[where:where+length]:
                    continue
                placements.setdefault((base, id(dump)), set()).add(target)
        combined = {}
        for (base, _), calls in placements.items():
            if len(calls) >= 3:
                combined.setdefault(base, set()).update(calls)
        for base, calls in combined.items():
            candidates.append(dict(offset=offset, size=used, vram=base, calls=sorted(calls), codec='refpack',
                                   expanded=len(payload)))
    # A placement must have a unique source, and separate programs may not
    # claim the same runtime bytes. Neither ambiguity proves a mapping.
    for index, row in enumerate(candidates):
        if any(index != other and row['vram'] < item['vram']+item['expanded']
               and item['vram'] < row['vram']+row['expanded'] for other, item in enumerate(candidates)):
            return []
    return candidates
