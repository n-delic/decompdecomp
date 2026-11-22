"""Recover a boot executable stored in a bounded ring-buffer LZSS stream."""

import struct

_LIMIT = 4 * 1024 * 1024


def decode(source, expected):
    if not 0 < expected <= _LIMIT:
        return None
    ring = bytearray(4096)
    position, at, flags = 0xfee, 0, 0
    output = bytearray()
    try:
        while len(output) < expected:
            flags >>= 1
            if not flags & 0x100:
                flags = source[at] | 0xff00
                at += 1
            if flags & 1:
                values = (source[at],)
                at += 1
            else:
                x, y = source[at], source[at + 1]
                at += 2
                offset, count = x | (y & 0xf0) << 4, (y & 15) + 3
                if len(output) + count > expected:
                    return None
                values = None
            for i in range(1 if values else count):
                value = values[0] if values else ring[(offset + i) & 0xfff]
                output.append(value)
                ring[position] = value
                position = (position + 1) & 0xfff
    except IndexError:
        return None
    return bytes(output) if at == len(source) else None



def _indirect_call(rom, start, end, target):
    """A boot call materializes the output address across a helper call."""
    for pos in range(start, end - 19, 4):
        load, call, low, jump, delay = struct.unpack_from('>5I', rom, pos)
        register = (load >> 16) & 31
        if (load >> 26 != 15 or not register or call >> 26 != 3 or low >> 26 != 9
                or (low >> 21) & 31 != register or (low >> 16) & 31 != register
                or jump != register << 21 | 31 << 11 | 9 or delay):
            continue
        value = ((load & 0xffff) << 16) + ((low & 0xffff) - (0x10000 if low & 0x8000 else 0))
        if value & 0xffffffff == target:
            return True
    return False

def find(rom, boot_ranges):
    """Require the mapped header and a boot call to its expanded address."""
    from .code import _looks_like_code

    matches = []
    at = rom.find(b'LZSS')
    while at >= 0:
        start = at - 12
        if start >= 0 and start % 4 == 0 and at + 12 <= len(rom):
            one, zero, header, _, size, packed = struct.unpack_from('>III4sII', rom, start)
            if (one, zero, header) == (1, 0, 24) and 0 < packed <= _LIMIT and start + 24 + packed <= len(rom):
                for lo, hi, base in boot_ranges:
                    if lo != 0x1000 or start < hi:
                        continue
                    target = base + start - lo
                    if target + size > 0x80800000:
                        continue
                    call = 0x0c000000 | ((target >> 2) & 0x03ffffff)
                    calls = any(struct.unpack_from('>I', rom, pos)[0] == call
                                for begin, end, _ in boot_ranges for pos in range(begin, end - 3, 4))
                    if not calls and not _indirect_call(rom, lo, start, target):
                        continue
                    data = decode(rom[start + 24:start + 24 + packed], size)
                    if not data or not _looks_like_code(data):
                        continue
                    returns = [i for i in range(0, len(data) - 7, 4) if data[i:i + 4] == b'\x03\xe0\x00\x08']
                    if returns:
                        end = returns[-1] + 8
                        matches.append(((target, data[:end], data[end:]),))
        at = rom.find(b'LZSS', at + 1)
    return matches[0] if len(matches) == 1 else None


def startup_sections(text, base):
    """Separate a nonreturning entry and its tables from the first called frame."""
    from ...arch.mips import is_frame, jal_targets

    if len(text) < 16 or struct.unpack_from('>I', text)[0] & 0xffff0000 != 0x3c1d0000:
        return None
    words = [word for word, in struct.iter_unpack('>I', text[:len(text) & ~3])]
    calls = set(jal_targets(words, base))
    for i in range(2, min(len(words) - 1, 0x400 // 4)):
        if (words[i] & 0xfc00003f != 0xd or words[i + 1]
                or words[i - 2] >> 26 != 3 or words[i - 1]):
            continue
        entries = [j for j in range(i + 2, min(len(words), 0x4000 // 4))
                   if is_frame(words[j]) and base + j * 4 in calls]
        if entries and entries[0] * 4 > (i + 2) * 4:
            return (i + 2) * 4, entries[0] * 4
    return None


def extend_boot(config, rom, log):
    """Recover a directly called loader beyond the initial boot code split."""
    import yaml

    from ...arch.mips import is_frame, jal_targets

    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = cfg['segments']
    entry = next((row for row in segments if isinstance(row, dict) and row.get('name') == 'entry'), None)
    main = next((row for row in segments if isinstance(row, dict) and row.get('name') == 'main'), None)
    if not entry or not main or main.get('follows_vram') != 'entry':
        return False
    index = segments.index(main)
    if index + 1 >= len(segments) or not isinstance(segments[index + 1], dict):
        return False
    tail = segments[index + 1]
    if tail.get('type') != 'bin' or tail.get('follows_vram') != 'main':
        return False
    lo, start, end = entry['start'], main['start'], tail['start']
    if not 0x1000 <= lo < start < end < 0x2000 or entry['vram'] - lo != main['vram'] - start:
        return False
    words = [word for word, in struct.iter_unpack('>I', rom[lo:start])]
    mapping = entry['vram'] - lo
    for address in jal_targets(words, entry['vram']):
        frame = address - mapping
        if not end <= frame < min(end + 64, len(rom) - 4) or not is_frame(struct.unpack_from('>I', rom, frame)[0]):
            continue
        instruction = struct.unpack_from('>I', rom, frame)[0]
        size = 0x10000 - (instruction & 0xffff)
        if not size or size % 8:
            continue
        pop = 0x27bd0000 | size
        for at in range(frame + 4, min(frame + 0x1000, len(rom) - 8), 4):
            if struct.unpack_from('>2I', rom, at) == (0x03e00008, pop):
                tail['start'] = at + 8
                config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
                log('Recovered the directly called compressed-program loader.')
                return True
    return False
