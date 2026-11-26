"""Instruction evidence for small MIPS literal changes."""

import hashlib

# Arithmetic/logical immediates and explicit load/store offsets; never branches.
IMMEDIATE_OPS = {8, 9, 10, 11, 12, 13, 14, 32, 33, 35, 36, 37, 40, 41, 43}
UNSIGNED_OPS = {12, 13, 14}



def shape(code, big):
    """Group instruction-aligned bodies with the same register and opcode fields."""
    order = 'big' if big else 'little'
    words = bytearray(code)
    for at in range(0, len(words), 4):
        opcode = int.from_bytes(code[at:at + 4], order) >> 26
        if opcode in (2, 3):
            word = int.from_bytes(code[at:at + 4], order) & 0xfc000000
            words[at:at + 4] = word.to_bytes(4, order)
        elif opcode in IMMEDIATE_OPS or opcode == 15:
            start = at + (2 if big else 0)
            words[start:start + 2] = b'\0\0'
    return hashlib.sha256(words).digest()


def changes(left, right, relocated, big):
    """Read up to two consistent immediate substitutions from aligned instructions."""
    if len(left) != len(right) or len(left) % 4:
        return {}, {}
    changes, offset_ops = {}, {}
    order = 'big' if big else 'little'
    for off in range(0, len(left), 4):
        x, y = int.from_bytes(left[off:off + 4], order), int.from_bytes(right[off:off + 4], order)
        if x == y or off in relocated:
            continue
        op = x >> 26
        if x >> 16 != y >> 16 or op not in IMMEDIATE_OPS:
            return {}, {}
        old, new = x & 0xffff, y & 0xffff
        if op not in UNSIGNED_OPS and (old >= 0x8000 or new >= 0x8000):
            return {}, {}
        if old in changes and changes[old] != new:
            return {}, {}
        changes[old] = new
        offset_ops.setdefault(old, set()).add(op)
    if not changes or len(changes) > 2:
        return {}, {}
    return changes, offset_ops



LUI, ORI, ADDIU = 15, 13, 9


def _low(word, signed):
    value = word & 0xffff
    return value - 0x10000 if signed and value >= 0x8000 else value


def value_pairs(compiled, wanted, relocated, big):
    """[(compiled value, wanted value)], each once: `value_sites` without the places."""
    sites = value_sites(compiled, wanted, relocated, big)
    return list(dict.fromkeys((old, new) for _, old, new in sites)) if sites else []


def value_sites(compiled, wanted, relocated, big):
    """[(instruction index, compiled value, wanted value)] for each constant the aligned instructions of two same-shaped bodies
    disagree on, for a compile-guided literal search (app/lookalike_profiles.py): signed immediates read as
    signed, a LUI with the ORI/ADDIU completing it in the same register as one 32-bit constant, a LUI alone as
    its upper half. Unlike `changes`, values may repeat and swap (one look-alike's +1/+2 are another's +2/+1):
    the search tries each C occurrence and keeps only what the compiler confirms. [] when the bodies differ in
    anything but immediates (lengths, opcodes, registers) or only where relocations are."""
    if len(compiled) != len(wanted) or len(compiled) % 4:
        return []
    order = 'big' if big else 'little'
    xs = [int.from_bytes(compiled[o:o + 4], order) for o in range(0, len(compiled), 4)]
    ys = [int.from_bytes(wanted[o:o + 4], order) for o in range(0, len(wanted), 4)]
    out, done = [], set()

    def completes(index, register):
        return (index < len(xs) and index * 4 not in relocated and xs[index] >> 26 in (ORI, ADDIU)
                and (xs[index] >> 21) & 31 == register and ys[index] >> 16 == xs[index] >> 16)

    for index, (x, y) in enumerate(zip(xs, ys)):
        if x == y or index * 4 in relocated or index in done:
            continue
        op = x >> 26
        if x >> 16 != y >> 16:
            return []
        if op == LUI or (op in (ORI, ADDIU) and index and xs[index - 1] >> 26 == LUI
                         and (index - 1) * 4 not in relocated and (xs[index - 1] >> 16) & 31 == (x >> 21) & 31
                         and xs[index - 1] >> 16 == ys[index - 1] >> 16):
            upper = index if op == LUI else index - 1
            register = (xs[upper] >> 16) & 31
            low = upper + 1 if completes(upper + 1, register) else None
            done.update({upper, low} - {None})
            pair = []
            for words in (xs, ys):
                value = (words[upper] & 0xffff) << 16
                if low is not None:
                    value += _low(words[low], words[low] >> 26 == ADDIU)
                pair.append(value & 0xffffffff)
            out.append((upper, *pair))
        elif op in IMMEDIATE_OPS:
            signed = op not in UNSIGNED_OPS
            out.append((index, _low(x, signed), _low(y, signed)))
        else:
            return []
    return out


def split_immediates(code: bytes, big_endian: bool, relocated: set[int]) -> set[int]:
    """Read adjacent LUI/ORI constants built in one register, excluding relocations."""
    order = 'big' if big_endian else 'little'
    words = [int.from_bytes(code[offset:offset+4], order) for offset in range(0, len(code) - 3, 4)]
    constants = set()
    for index, (upper, lower) in enumerate(zip(words, words[1:])):
        register = (upper >> 16) & 31
        if (upper >> 21 == 0x0f << 5 and register
                and lower >> 16 == (0x0d << 10 | register << 5 | register)
                and index * 4 not in relocated and (index + 1) * 4 not in relocated):
            constants.add(((upper & 0xffff) << 16) | (lower & 0xffff))
    return constants
