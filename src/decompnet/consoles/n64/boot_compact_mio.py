"""Recover a boot program stored as compact 8 KiB MIO-style chunks."""
import struct

_LIMIT = 4 * 1024 * 1024
_CHUNK = 0x2000


def unpack(image, start, end, expected):
    """Expand chunks whose back-references can reach earlier chunks."""
    if not (0 <= start < end <= len(image) and 0 < expected <= _LIMIT):
        return None
    output = bytearray()
    try:
        for pos in range(start, end, _CHUNK):
            chunk = image[pos:min(pos+_CHUNK, end)]
            if len(chunk) < 8:
                return None
            _, size, links, literals = struct.unpack_from('>4H', chunk)
            if not (0 < size <= expected-len(output) and 8 <= links <= literals < len(chunk)):
                return None
            mask, flags, bits = 8, 0, 0
            link_end = literals
            target = len(output)+size
            while len(output) < target:
                if not bits:
                    if mask >= links:
                        return None
                    flags = chunk[mask]; mask += 1; bits = 8
                bits -= 1
                if flags & (1 << bits):
                    if literals >= len(chunk):
                        return None
                    output.append(chunk[literals]); literals += 1
                else:
                    if links+2 > link_end:
                        return None
                    word = int.from_bytes(chunk[links:links+2], 'big'); links += 2
                    distance, count = (word & 0xfff)+1, (word >> 12)+3
                    if distance > len(output) or len(output)+count > target:
                        return None
                    for _ in range(count):
                        output.append(output[-distance])
    except (IndexError, struct.error):
        return None
    return bytes(output) if len(output) == expected else None


def find(rom, boot_ranges):
    """Tie a loader's MIO header reconstruction, directory and call to a program."""
    from ...arch.mips import jal_targets
    from .code import _looks_like_code

    boot = b''.join(rom[start:end] for start, end, _ in boot_ranges)
    # The loader constructs MIO0 on the stack and reads compact halfword offsets.
    words = struct.unpack(f'>{len(boot)//4}I', boot[:len(boot)//4*4])
    if not any(first >> 26 == 15 and first & 0xffff == 0x4d49 and second >> 26 == 13
               and (first >> 16) & 31 == (second >> 21) & 31 == (second >> 16) & 31
               and second & 0xffff == 0x4f30 for first, second in zip(words, words[1:])):
        return None
    calls = set()
    for start, end, vram in boot_ranges:
        calls.update(jal_targets(struct.unpack(f'>{(end-start)//4}I', rom[start:end]), vram & 0xf0000000))
    records = []
    bound = min(len(rom)-40, 0x10000)
    for at in range(0x1000, bound, 4):
        name = rom[at:at+16].split(b'\0', 1)[0]
        if not name or any(c < 32 or c > 126 for c in name) or b'\0' not in rom[at:at+16]:
            continue
        code_index, data_index, begin, text_end, data_end, bss_end = struct.unpack_from('>6I', rom, at+16)
        if (code_index != 0 or data_index != 0 or not 0x80000400 <= begin < text_end < data_end <= bss_end <= 0x80800000
                or not 0x10000 <= data_end-begin <= _LIMIT or not any(begin <= call < text_end for call in calls)):
            continue
        for table in range(0x1000, bound, 4):
            first, last, size = struct.unpack_from('>3I', rom, table)
            if (size != data_end-begin or first % 16 or last % 16 or not 0 < first < last <= len(rom)
                    or last-first < _CHUNK):
                continue
            decoded = unpack(rom, first, last, size)
            if not decoded or not _looks_like_code(decoded[:min(0x10000, text_end-begin)]):
                continue
            programs = [(begin, decoded[:text_end-begin], decoded[text_end-begin:])]
            # Following module records name independently compressed text and
            # data entries in the same directory. They may share runtime RAM.
            for other in range(at+0x30, min(at+0x1000, bound), 0x30):
                label = rom[other:other+16].split(b'\0', 1)[0]
                if not label or any(c < 32 or c > 126 for c in label) or b'\0' not in rom[other:other+16]:
                    break
                ci, di, base, code_end, last_data, last_bss = struct.unpack_from('>6I', rom, other+16)
                if (ci == di or ci >= 256 or di >= 256 or not 0x80000400 <= base < code_end < last_data <= last_bss <= 0x80800000
                        or last_data-base > _LIMIT or table+12*max(ci,di)+12 > bound):
                    continue
                parts = []
                for index, expected in ((ci, code_end-base), (di, last_data-code_end)):
                    lo, hi, size = struct.unpack_from('>3I', rom, table+12*index)
                    if size != expected or lo % 16 or hi % 16:
                        break
                    part = unpack(rom, lo, hi, size)
                    if part is None:
                        break
                    parts.append(part)
                if len(parts) == 2 and _looks_like_code(parts[0][:min(0x10000, len(parts[0]))]):
                    programs.append((base, *parts))
            records.append(tuple(programs))
    return records[0] if len(records) == 1 else None
