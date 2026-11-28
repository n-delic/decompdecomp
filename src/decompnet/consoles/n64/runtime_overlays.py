"""Recover overlapping code only from corroborated runtime caller and target bytes."""
import struct
from collections import defaultdict

from ...arch.mips import is_frame
from .loaded_code import _code, _region


def ambiguous(rom, known, *, check=lambda: None):
    """Whether many cross-bank calls land inside framed bodies in another mapping.

    This only decides whether a longer observation is worthwhile; it never
    classifies an entry or changes a mapping.
    """
    found = set()
    tested = set()
    for start, end, base in known:
        if not 0 <= start < end <= len(rom) or (start | end) % 4:
            continue
        for offset in range(start, end-3, 4):
            if not offset & 0xffff:
                check()
            word, = struct.unpack_from('>I', rom, offset)
            if word >> 26 != 3:
                continue
            pc = base+offset-start
            target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
            if target in tested:
                continue
            for lo, hi, vram in known:
                if (not vram <= target < vram+hi-lo or lo <= offset < hi
                        or base-start == vram-lo):
                    continue
                tested.add(target)
                at = lo+target-vram
                if at+8 > hi or is_frame(int.from_bytes(rom[at:at+4], 'big')):
                    continue
                for frame in range(max(lo, at-128), at, 4):
                    first = int.from_bytes(rom[frame:frame+4], 'big')
                    if not is_frame(first):
                        continue
                    pop = 0x27bd0000 | ((-first) & 0xffff)
                    if any(int.from_bytes(rom[end:end+4], 'big') == 0x03e00008
                           and (int.from_bytes(rom[end+4:end+8], 'big') == pop
                                or int.from_bytes(rom[end-4:end], 'big') == pop)
                           for end in range(at, min(hi-8, at+512), 4)):
                        found.add(target)
                        break
                if len(found) >= 2:
                    return True
    return False


def regions(rom, dumps, known, *, check=lambda: None, moved=None):
    """Find missing ROM code with a runtime-observed mapping and independent calls.

    A caller must match its known ROM mapping and each target must match a
    unique contiguous ROM/RAM region in the same snapshot. Several distinct
    targets corroborate the new mapping, including calls from resident code.
    Existing ROM ranges with different mappings are never displaced.
    """
    groups = defaultdict(list)
    for dump in dumps:
        checked = set()
        verified_calls = []
        candidates = []
        for start, end, base in known:
            if not 0 <= start < end <= len(rom) or (start | end) % 4:
                continue
            for offset in range(start, end-15, 4):
                if not offset & 0xffff:
                    check()
                word, = struct.unpack_from('>I', rom, offset)
                if word >> 26 != 3:
                    continue
                pc = base+offset-start
                target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                at, destination = pc & 0x1fffffff, target & 0x1fffffff
                if (target in checked or not 0x80000400 <= target < 0x80800000
                        or at+16 > len(dump) or destination+128 > len(dump)
                        or dump[at:at+16] != rom[offset:offset+16]):
                    continue
                verified_calls.append((target, offset))
                mapped = [(a,b,v) for a,b,v in known if v <= target < v+b-a]
                if mapped and any(dump[destination:destination+32] == rom[a+target-v:a+target-v+32]
                                     for a,b,v in mapped):
                    continue
                found = _region(rom, dump, target)
                if not found:
                    continue
                lo, hi, vram = found
                if not mapped and not any(vram < v+b-a and v < vram+hi-lo and v-a != vram-lo
                                          for a,b,v in known):
                    continue
                candidates.append((lo, hi, vram))
                # Resident code may call an overlay that replaces another bank
                # at the same RAM address. The caller and target are both
                # byte-verified in this snapshot; distinct calls corroborate it.
                checked.add(target)
                groups[vram-lo].append((lo, hi, target, offset))
        # A verified contiguous bank may start before the already mapped part
        # of a slot. Calls into its frameless interior still corroborate the
        # mapping if their callers and target bytes match this same snapshot.
        for lo, hi, vram in set(candidates):
            delta = vram-lo
            for target, offset in verified_calls:
                at = target-delta
                if (lo <= at and at+128 <= hi and rom[at:at+128] == dump[(target & 0x1fffffff):(target & 0x1fffffff)+128]):
                    groups[delta].append((lo, hi, target, offset))
    result = []
    for delta, observations in groups.items():
        if len({target for _,_,target,_ in observations}) < 3:
            continue
        intersection = max(row[0] for row in observations)
        lo = min(row[0] for row in observations)
        hi = min(row[1] for row in observations)
        # Every observed span has the same mapping and must overlap. Their
        # block-aligned starts can differ by up to 15 bytes.
        if intersection >= hi:
            continue
        if any(a < hi and lo < b and v-a != delta for a,b,v in known):
            # The same ROM bytes may already have an inferred placement.
            # Keep them out of the new-segment list, but pass the observed
            # mapping to the existing correction pass, which independently
            # checks internal calls and leaves ambiguous placements unchanged.
            if moved is not None:
                moved.add((lo, hi, lo+delta))
            continue
        cursor = lo
        for a,b,v in sorted(known):
            if b <= cursor or a >= hi:
                continue
            if cursor < a and (code := _code(rom, cursor, min(a, hi),
                                             [target-delta for _,_,target,_ in observations])):
                result.append((*code, code[0]+delta))
            cursor = max(cursor, b)
        if cursor < hi and (code := _code(rom, cursor, hi, [target-delta for _,_,target,_ in observations])):
            result.append((*code, code[0]+delta))
    return sorted(set(result))
