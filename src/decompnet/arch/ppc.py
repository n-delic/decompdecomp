"""PowerPC (GameCube, Wii): what decompdecomp knows about the processor itself, whatever the console.

- the relocations a match ignores the bits of, and how capstone reads its code;
- code no compiler makes (asm_reason): its first instructions, up to the first branch, read r0, r11 or r12 before
  setting them. Compiled code receives its arguments in r3-r10 and f1-f8 and never reads a scratch register it
  hasn't set; CodeWarrior's runtime helpers take theirs there: __ptmf_scall and its kin the pointer-to-member in
  r12, __save_gpr the save area in r11 (Mario Party 6's minigame modules each link their own copies).

Not a sign: instructions only assembly has (mtmsr, mtspr of a GQR...). C functions get them too, inlined from the
SDK's headers (OSInitFastCast's asm block, in HuSysInit), and an agent matches those by calling the same helpers.
"""

from __future__ import annotations

import capstone

RELOC_MASKS = {  # relocation type -> (byte offset from r_offset, length, big-endian bitmask to clear)
    4: (0, 2, 0xFFFF),  # R_PPC_ADDR16_LO
    5: (0, 2, 0xFFFF),  # R_PPC_ADDR16_HI
    6: (0, 2, 0xFFFF),  # R_PPC_ADDR16_HA
    10: (0, 4, 0x03FFFFFC),  # R_PPC_REL24
    109: (0, 4, 0x001FFFFF),  # R_PPC_EMB_SDA21
}
CAPSTONE = {True: (capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)}

_SCRATCH = (0, 11, 12)  # never an argument, never read by compiled code before it's set

# What instructions do with general registers, for the first block: opcode 31's extended opcodes by form
_X_LOAD = {23, 55, 87, 119, 279, 311, 343, 375, 534, 790, 20}  # lwzx..lhaux, lwbrx, lhbrx, lwarx: rD <- (rA|0 + rB)
_X_FLOAD = {535, 567, 599, 631}  # lfsx..lfdux: an FPR <- (rA|0 + rB)
_X_STORE = {151, 183, 215, 247, 407, 439, 662, 918, 150}  # stwx..sthux, stwbrx, sthbrx, stwcx.: rS -> (rA|0 + rB)
_X_FSTORE = {663, 695, 727, 759, 983}  # stfsx..stfdux, stfiwx: an FPR -> (rA|0 + rB)
_X_UPDATE = {55, 119, 311, 375, 567, 631, 183, 247, 439, 695, 759}  # the ...ux forms: rA is read and written
_XO_ARITH = {266, 10, 138, 234, 202, 40, 8, 136, 232, 200, 104, 235, 75, 11, 491, 459}  # rD <- rA (op rB)
_XO_ONE = {234, 202, 232, 200, 104}  # addme, addze, subfme, subfze, neg: rA only
_X_LOGIC = {28, 60, 444, 124, 316, 284, 412, 476, 24, 536, 792}  # and..sraw: rA <- rS op rB
_X_LOGIC1 = {824, 26, 922, 954}  # srawi, cntlzw, extsh, extsb: rA <- rS
_X_CACHE = {86, 54, 470, 278, 246, 1014, 982}  # dcbf, dcbst, dcbi, dcbt, dcbtst, dcbz, icbi: (rA|0 + rB)
_X_NEUTRAL = {598, 854, 512, 19}  # sync, eieio, mcrxr, mfcr (writes a GPR: see below)


def _words(code: bytes):
    for at in range(0, len(code) - 3, 4):
        yield int.from_bytes(code[at:at + 4], "big")


def _uses(w: int) -> tuple[list[int], list[int]] | None:
    """(general registers read, written) by one instruction of a function's first block; None at a branch (the
    block ends) or an instruction not known here (no verdict)."""
    op, d, a, b = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    base = [a] if a else []  # (rA|0): r0 there means the number 0
    if op in (14, 15):  # addi, addis (li, lis)
        return base, [d]
    if op in (7, 8, 12, 13):  # mulli, subfic, addic, addic.
        return [a], [d]
    if op in (10, 11, 3):  # cmpli, cmpi, twi
        return [a], []
    if op in (24, 25, 26, 27, 28, 29):  # ori..andis.: rA <- rS op imm
        if op == 24 and w & 0xFFFF == 0 and a == d:  # nop (ori r0,r0,0), and ori rX,rX,0
            return [], []
        return [d], [a]
    if op in (20, 21, 23):  # rlwimi (inserts into rA), rlwinm, rlwnm (shifts by rB)
        return [d] + ([a] if op == 20 else []) + ([b] if op == 23 else []), [a]
    if 32 <= op <= 47:  # integer loads and stores
        if op == 46:  # lmw rD..r31
            return base, list(range(d, 32))
        if op == 47:  # stmw rS..r31
            return list(range(d, 32)) + base, []
        update = bool(op & 1)
        addr = [a] if update else base
        if op in (36, 37, 38, 39, 44, 45):  # stores
            return [d] + addr, [a] if update else []
        return addr, [d] + ([a] if update else [])
    if 48 <= op <= 55 or op in (56, 57, 60, 61):  # float loads and stores, psq_l(u)/psq_st(u): rA only
        update = bool(op & 1)
        return ([a] if update else base), ([a] if update else [])
    if op == 4:  # paired singles; psq_lx/psq_stx(u) take rA and rB
        xo = (w >> 1) & 0x3F
        if xo in (6, 7, 38, 39):
            return ([a] if xo >= 32 else base) + [b], [a] if xo >= 32 else []
        return [], []
    if op in (59, 63):  # floating point arithmetic
        return [], []
    if op == 31:
        xo = (w >> 1) & 0x3FF
        if xo in _X_LOAD or xo in _X_FLOAD:
            upd = xo in _X_UPDATE
            return ([a] if upd else base) + [b], ([d] if xo in _X_LOAD else []) + ([a] if upd else [])
        if xo in _X_STORE or xo in _X_FSTORE:
            upd = xo in _X_UPDATE
            return ([d] if xo in _X_STORE else []) + ([a] if upd else base) + [b], [a] if upd else []
        if (w >> 1) & 0x1FF in _XO_ARITH:  # (also with OE set)
            return [a] + ([] if (w >> 1) & 0x1FF in _XO_ONE else [b]), [d]
        if xo in _X_LOGIC:
            if xo == 444 and d == b == a:  # mr rX,rX
                return [], []
            return [d, b], [a]
        if xo in _X_LOGIC1:
            return [d], [a]
        if xo in (0, 32, 4):  # cmp, cmpl, tw
            return [a, b], []
        if xo == 339:  # mfspr
            return [], [d]
        if xo in (467, 144):  # mtspr, mtcrf
            return [d], []
        if xo in _X_CACHE:
            return base + [b], []
        if xo in _X_NEUTRAL:
            return [], [d] if xo == 19 else []
        return None
    if op == 19:  # cr logic, mcrf and isync go on; bclr/bcctr and the rest end the block
        return ([], []) if (w >> 1) & 0x3FF in (0, 33, 129, 150, 193, 225, 257, 289, 417, 449) else None
    return None  # branches (b, bc), sc, a zero word, data...: the end of the block


def asm_reason(code: bytes, big_endian: bool = True, **_facts) -> str | None:
    """Why this PowerPC function (its big-endian code, from the entry) can't come from a compiler, or None. (No
    console fact changes it.)"""
    written: set[int] = set()
    for w in _words(code):
        uses = _uses(w)
        if uses is None:
            return None
        reads, writes = uses
        for r in reads:
            if r in _SCRATCH and r not in written:
                return f"reads r{r} before setting it: compiled code receives nothing there"
        written.update(writes)
    return None
