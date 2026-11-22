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
