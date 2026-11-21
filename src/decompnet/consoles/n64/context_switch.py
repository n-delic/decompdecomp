"""Handwritten register-context entries with a shared resume routine."""

import struct

_STACK_FRAME = 0xc0
_SAVED = {reg: index * 4 for index, reg in enumerate((*range(4, 8), *range(16, 24), 28, 30, 31))}
_FPRS = {(reg, 0x40 + reg * 4) for reg in range(32)}


def saves_context(code):
    """Recognize the complete save area and the explicit handoff at its end."""
    if len(code) < 4 * 54 or len(code) > 4 * 80 or len(code) % 4:
        return False
    words = tuple(word for word, in struct.iter_unpack('>I', code))
    if words[0] != 0x27bd0000 | (-_STACK_FRAME & 0xffff):
        return False
    if (words[-4:-1] == (0, 0x03e00008, 0x27bd0000 | _STACK_FRAME)
            and words[-5] & 0xfc1fffff == 0x14000003):
        # If another thread is waiting, save SP and fall through to resume.
        link = words[-1]
    elif words[-2] >> 26 == 3 and not words[-1]:
        link, high, low, load = words[-6:-2]
        base = (link >> 21) & 31
        if (not base or high >> 16 != 0x3c00 | base or low >> 16 != 0x2400 | base << 5 | base
                or load != 0x8c000000 | base << 21 | base << 16):
            return False
    else:
        return False
    if link & 0xfc1fffff != 0xac1d0008:
        return False
    stores = {
        (word >> 26, (word >> 16) & 31, word & 0xffff)
        for word in words if (word >> 21) & 31 == 29
    }
    return ({(43, reg, offset) for reg, offset in _SAVED.items()} <= stores
            and {(57, reg, offset) for reg, offset in _FPRS} <= stores
            and (43, 31, 0x3c) in stores)
