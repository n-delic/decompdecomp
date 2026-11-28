"""Recover adjacent code files from a resident file directory and a runtime mapping."""
import struct

from ...arch.mips import JR_RA, is_frame
from .raw_slots import Slot

_RECORD = struct.Struct('>3I')


def _code(image, start, end):
    if start % 4 or end % 4 or not 0 < end-start <= 1024*1024:
        return None
    words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
    returns = [i for i, word in enumerate(words[:-1]) if word == JR_RA]
    if len(returns) < 4 or sum(is_frame(word) for word in words) < 4:
        return None
    return start + (returns[-1]+2)*4


def _loader_tables(image, taken, target):
    """Find a loader that indexes three adjacent directory fields by 12 bytes."""
    for lo, hi, vram in taken:
        offset = lo + target-vram
        if not lo <= offset < hi or offset+160 > len(image):
            continue
        words = struct.unpack_from('>40I', image, offset)
        # The destination survives in a3 and is passed to the raw transfer
        # with the difference of the two directory bounds as its size.
        if (0x00a03821 not in words or 0x00e02821 not in words
                or not any(w >> 26 == 3 and following == 0x00c43023
                           for w, following in zip(words, words[1:]))):
            continue
        scaled = set()
        for first, add, last in zip(words, words[1:], words[2:]):
            # index * 3 * 4, with the incoming index in a0
            temp = first >> 11 & 31
            if (first >> 26 == 0 and first & 63 == 0 and first >> 16 & 31 == 4
                    and first >> 6 & 31 == 1 and add >> 26 == 0 and add & 63 in (0x20, 0x21)
                    and add >> 11 & 31 == temp and {add >> 21 & 31, add >> 16 & 31} == {temp, 4}
                    and last >> 26 == 0 and last & 63 == 0 and last >> 16 & 31 == temp
                    and last >> 6 & 31 == 2):
                scaled.add(last >> 11 & 31)
        fields = {}
        for lui, add, load in zip(words, words[1:], words[2:]):
            if lui >> 26 != 15 or add >> 26 != 0 or add & 63 not in (0x20, 0x21) or load >> 26 != 0x23:
                continue
            reg = lui >> 16 & 31
            dest = add >> 11 & 31
            if (reg not in (add >> 21 & 31, add >> 16 & 31) or load >> 21 & 31 != dest
                    or not ({add >> 21 & 31, add >> 16 & 31} & scaled)):
                continue
            high, imm = lui & 0xffff, load & 0xffff
            fields.setdefault(high, set()).add(imm)
        for high, loads in fields.items():
            for imm in loads:
                if imm+4 not in loads or imm+8 not in loads:
                    continue
                signed = imm if imm < 0x8000 else imm-0x10000
                table = (high << 16) + signed-vram+lo
                if 0 <= table <= len(image)-12 and table % 4 == 0:
                    yield table


def _loader_calls(image, taken, address):
    """Collect constant file indexes loaded at the witnessed address."""
    cache = {}
    found = {}
    for lo, hi, _ in taken:
        words = struct.unpack_from(f'>{(hi-lo)//4}I', image, lo)
        for i, word in enumerate(words[:-1]):
            if word >> 26 != 3:
                continue
            values = {0: 0}
            for w in (*words[max(0, i-10):i], words[i+1]):
                op, rs, rt, rd = w >> 26, w >> 21 & 31, w >> 16 & 31, w >> 11 & 31
                imm = w & 0xffff
                if op in (2, 3):
                    values = {0: 0}
                elif op == 15:
                    values[rt] = imm << 16
                elif op in (9, 13):
                    if rs in values:
                        values[rt] = ((values[rs] + (imm if imm < 0x8000 else imm-0x10000))
                                      if op == 9 else values[rs] | imm) & 0xffffffff
                    else:
                        values.pop(rt, None)
                elif op == 0 and (w & 63) in (0x21, 0x25):
                    if rs in values and rt in values:
                        values[rd] = (values[rs] + values[rt]) & 0xffffffff
                    else:
                        values.pop(rd, None)
                elif op == 0:
                    values.pop(rd, None)
                elif op not in (1, 4, 5, 6, 7, 0x28, 0x29, 0x2a, 0x2b, 0x2e, 0x2f):
                    values.pop(rt, None)
                values[0] = 0
            if values.get(5) != address or not 0 <= values.get(4, -1) < 8192:
                continue
            target = 0x80000000 | ((word & 0x03ffffff) << 2)
            if target not in cache:
                cache[target] = list(_loader_tables(image, taken, target))
            for table in cache[target]:
                entry = table+values[4]*12
                if entry+12 <= len(image):
                    found.setdefault((target, table), set()).add(entry)
    return found


def read(image, taken, observed, check=lambda: None):
    """Require the runtime witness and a loader call for every recovered bank."""
    floor = max((end for _, end, _ in taken), default=0)
    for start, end, address in observed:
        for entries in _loader_calls(image, taken, address).values():
            records = {}
            for pos in entries:
                a, b, zero = _RECORD.unpack_from(image, pos)
                if zero or not floor <= a < b <= len(image) or address+b-a > 0x80800000:
                    continue
                if (tail := _code(image, a, b)) is not None:
                    records[a] = (b, tail)
            group = sorted(records)
            if (len(group) < 4 or start not in records or end > records[start][0]
                    or any(records[a][0] != b for a, b in zip(group, group[1:]))):
                continue
            check()
            return [Slot(a, records[a][0], address) for a in group], [
                (a, records[a][1], address) for a in group]
    return [], []
