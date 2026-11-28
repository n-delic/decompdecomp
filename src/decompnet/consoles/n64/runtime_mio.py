"""Find ordinary MIO0 programs corroborated by loaded code and cartridge bytes."""
import struct


def unpack(data, offset):
    """Decode one bounded stream and return its exact consumed size."""
    if offset < 0 or offset + 16 > len(data) or data[offset:offset+4] != b'MIO0':
        return None
    size, links, literals = struct.unpack_from('>III', data, offset+4)
    if not 0 < size <= 0x400000 or not 16 <= links <= literals <= len(data)-offset:
        return None
    mask, link, literal = offset+16, offset+links, offset+literals
    out = bytearray()
    bits = 0
    flags = 0
    while len(out) < size:
        if not bits:
            if mask >= offset+links:
                return None
            flags = data[mask]
            mask += 1
            bits = 8
        if flags & 0x80:
            if literal >= len(data):
                return None
            out.append(data[literal])
            literal += 1
        else:
            if link+2 > offset+literals:
                return None
            token = int.from_bytes(data[link:link+2], 'big')
            link += 2
            distance, count = (token & 0xfff)+1, (token >> 12)+3
            if distance > len(out) or len(out)+count > size:
                return None
            for _ in range(count):
                out.append(out[-distance])
        flags = (flags << 1) & 0xff
        bits -= 1
    return bytes(out), max(mask, link, literal)-offset


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    """Require three distinct called targets and 64 KiB of identical loaded data."""
    if len(targets) < 3 or not dumps:
        return []
    candidates = []
    offset = rom.find(b'MIO0')
    while offset >= 0:
        check()
        decoded = unpack(rom, offset)
        if decoded:
            payload, size = decoded
            if len(payload) >= 0x10000:
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
                        physical = base & 0x1fffffff
                        if (base % 16 or not 0x80000400 <= base < base+len(payload) <= 0x80800000
                                or physical+0x10000 > len(dump)
                                or dump[physical:physical+0x10000] != payload[:0x10000]
                                or any(v < base+len(payload) and base < v+hi-lo for lo, hi, v in occupied)):
                            continue
                        placements.setdefault(base, set()).add(target)
                for base, calls in placements.items():
                    if len(calls) >= 3:
                        candidates.append(dict(codec='mio0', offset=offset, size=size, expanded=len(payload),
                                               vram=base, calls=sorted(calls)))
        offset = rom.find(b'MIO0', offset+1)
    return candidates if len(candidates) == 1 else []
