"""What a MIPS target's own instructions say about the compiler profile it was built with (app/profile_probe.py).

Words only: no diff, no compile. The rules that read them are the profile probe's and the console's.
"""
from __future__ import annotations

import struct

JR_RA = 0x03E00008
NOP = 0


def words_of(code: bytes, big_endian: bool = True) -> list[int]:
    count = len(code) // 4
    if not count:
        return []
    return list(struct.unpack(('>' if big_endian else '<') + f'{count}I', code[:count * 4]))


def float_pool_through_at(words) -> bool:
    """A float read through the assembler's own temporary: `lui $at, %hi(X)` and then `lwc1 $fN, %lo(X)($at)`. SN GCC
    splits a global in the compiler above its `-G` threshold and leaves a smaller one to the assembler, which uses `$at`:
    the target's float pool words (small data) are the second kind."""
    for before, word in zip(words, words[1:]):
        if before >> 26 == 0x0F and (before >> 16) & 31 == 1 and word >> 26 == 0x31 and (word >> 21) & 31 == 1:
            return True
    return False


def uses_ldc1(words) -> bool:
    return any(word >> 26 == 0x35 for word in words)


def ends_jr_ra_nop(words) -> bool:
    """The code ends `jr $ra; nop` with something before it a scheduler could have put in the slot: SN GCC 2.8.1 fills
    that slot whenever it can, SN 2.7.2's builds do not."""
    if len(words) < 4 or list(words[-2:]) != [JR_RA, NOP]:
        return False
    before = words[-3]
    op, funct = before >> 26, before & 63
    transfer = op in (1, 2, 3, 4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17) or (op == 0 and funct in (8, 9, 0xC, 0xD))
    return before != NOP and not transfer
