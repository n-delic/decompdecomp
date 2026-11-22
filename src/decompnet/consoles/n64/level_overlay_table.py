"""Identify level directories whose loader copies many ROM banks to one RAM slot."""
import struct

from .raw_slots import Slot


def read(image, taken):
    """Require the indexed loader and a contiguous directory of independently bounded banks."""
    floor = max((end for _, end, _ in taken), default=0)
    for start, end, vram in taken:
        words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
        for i in range(len(words)-20):
            hi1, add1, load1, hi2, add2, load2 = words[i:i+6]
            r1, r2 = (hi1 >> 16) & 31, (hi2 >> 16) & 31
            index = (add1 >> 16) & 31
            if not (hi1 >> 26 == hi2 >> 26 == 15 and r1 and r2 and index
                    and add1 == (r1 << 21 | index << 16 | r1 << 11 | 0x21)
                    and add2 == (r2 << 21 | index << 16 | r2 << 11 | 0x21)
                    and load1 >> 16 == 0x8c00 | r1 << 5 | r1
                    and load2 >> 16 == 0x8c00 | r2 << 5 | r2
                    and (hi1 & 0xffff) == (hi2 & 0xffff)
                    and (load2 & 0xffff) == (load1 & 0xffff) + 4):
                continue
            imm = load1 & 0xffff
            addr = ((hi1 & 0xffff) << 16) + (imm if imm < 0x8000 else imm - 0x10000)
            at = start + addr - vram
            stride32 = any((w >> 26) == 0 and (w & 0x3f) == 0 and ((w >> 11) & 31) == index
                           and ((w >> 6) & 31) == 5 for w in words[max(0,i-12):i])
            stride = 0x20 if stride32 else 0x54
            for j in range(i+6, min(i+18, len(words)-1)):
                high, low = words[j:j+2]
                reg = (high >> 16) & 31
                if high >> 26 != 15 or not reg or low >> 26 != 9 or (low >> 21) & 31 != reg or (low >> 16) & 31 != reg:
                    continue
                imm = low & 0xffff
                target = ((high & 0xffff) << 16) + (imm if imm < 0x8000 else imm - 0x10000)
                if not 0x80000400 <= target < 0x80800000 or target % 16:
                    continue
                records = []
                for pos in range(at, min(at+64*stride, len(image)-15), stride):
                    if pos < 0 or pos % 4:
                        break
                    lo, hi, ram_end = struct.unpack_from('>3I', image, pos)
                    entry = target if stride32 else struct.unpack_from('>I', image, pos+12)[0]
                    if not (floor <= lo < hi <= len(image) and not (lo | hi) % 16
                            and target+hi-lo <= ram_end <= 0x80800000
                            and target <= entry < target+hi-lo and not entry % 4
                            and b'\x03\xe0\x00\x08' in image[lo:hi]):
                        break
                    records.append(Slot(lo, hi, target))
                records = sorted(set(records), key=lambda r: r.start)
                if len(records) >= 4 and all(a.end == b.start for a, b in zip(records, records[1:])):
                    return records
    return []


def companion(image, taken, slots):
    """Find the separately copied helper immediately before the bank directory's ROM range."""
    first = min(slot.start for slot in slots)
    last = max(slot.end for slot in slots)
    for start, end, vram in taken:
        words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
        for i in range(len(words)-10):
            h1, l1, h2, l2, sub = words[i:i+5]
            r1, r2 = (h1 >> 16) & 31, (h2 >> 16) & 31
            if not (h1 >> 26 == h2 >> 26 == 15 and r1 and r2 and r1 != r2
                    and l1 >> 16 == 0x2400 | r1 << 5 | r1
                    and l2 >> 16 == 0x2400 | r2 << 5 | r2
                    and sub == (r1 << 21 | r2 << 16 | 6 << 11 | 0x23)):
                continue
            def value(high, low):
                imm = low & 0xffff
                return ((high & 0xffff) << 16) + (imm if imm < 0x8000 else imm - 0x10000)
            hi, lo = value(h1, l1), value(h2, l2)
            if not (max(x[1] for x in taken) <= lo < hi <= len(image) and (hi == first or lo == last)
                    and not (lo | hi) % 16):
                continue
            for j in range(i+5, min(i+14, len(words)-2)):
                high, call, low = words[j:j+3]
                if high >> 16 != 0x3c05 or call >> 26 != 3 or low >> 16 != 0x24a5:
                    continue
                target = value(high, low)
                if (0x80000400 <= target < target+hi-lo <= 0x80800000 and not target % 16
                        and b'\x03\xe0\x00\x08' in image[lo:hi]):
                    return Slot(lo, hi, target)
    return None
