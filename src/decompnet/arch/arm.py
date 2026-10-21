"""ARM and Thumb (DS, and forks of public GBA and 3DS decomps): what decompdecomp knows about the processor.

The relocations a match ignores, how capstone reads the code, and the code no compiler makes: BIOS call stubs.
"""

from __future__ import annotations

import capstone

RELOC_MASKS = {  # relocation type -> (byte offset from r_offset, length, little-endian bitmask to clear)
    28: (0, 4, 0x00FFFFFF),  # R_ARM_CALL
    29: (0, 4, 0x00FFFFFF),  # R_ARM_JUMP24
    43: (0, 4, 0x000F0FFF),  # R_ARM_MOVW_ABS_NC
    44: (0, 4, 0x000F0FFF),  # R_ARM_MOVT_ABS
}
CAPSTONE = {False: (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)}

# Thumb code: an ELF object says "ARM" for both; references are bl pairs and literal-pool words
THUMB_RELOC_MASKS = {
    10: (0, 4, 0x07FF07FF),  # R_ARM_THM_CALL (both halves of a Thumb bl)
    2: (0, 4, 0xFFFFFFFF),  # R_ARM_ABS32 (a literal-pool word)
}
THUMB_CAPSTONE = {False: (capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)}

# A BIOS call stub: `svc #n; bx lr` and maybe a move around it (Nintendo's SDKs: CpuSet, Div, WaitByLoop...). No C
# compiler emits svc (CodeWarrior for DS has no intrinsic for it), so it's hand-written: on a DS game agents spent
# 30 tries on 19 of them. An object doesn't say whether a function is ARM or
# Thumb (dsd's don't set bit 0), so both readings are checked, on stubs only: a longer function's halfwords could
# read as an svc by chance.
STUB_BYTES = 16
THUMB_SVC, THUMB_BX_LR = 0xDF00, 0x4770  # svc #n (0xDFnn); bx lr
ARM_SWI, ARM_BX_LR = 0x0F000000, 0xE12FFF1E  # swi #n (cond 1111 nnnn...: bits 24-27 set); bx lr
# Instructions no compiler emits, in code known to be ARM (a Thumb function read as ARM words could look like one):
# the CPU's mode (msr/mrs: a DS program's start-up code switches it) and coprocessor 15, the cache and memory setup
SYSTEM = ((0x0FB0F000, 0x0320F000, "msr"), (0x0FB0FFF0, 0x0120F000, "msr"), (0x0FBF0FFF, 0x010F0000, "mrs"),
          (0x0F000F10, 0x0E000F10, "mcr/mrc p15"))  # (mask, value, what)
# A piece of a function, not one: ds-decomp's analysis put a symbol in the middle of a function, and every C function
# has a start and a return. On a DS game 159 of 557 open functions were such pieces (a lone `pop {r4, pc}`, a lone
# `push {r0-r3}` running on into the next symbol, one `str` from the middle of a function, a lone `bx ip`), each
# taken and given up on by five to seven agents in turn.
_PIECE = "a piece cut out of another function (its symbol is in the wrong place), which no C makes on its own"
# r4-r11 belong to the caller (the ARM calling convention): compiled code saves one before it sets it, and never
# reads one it hasn't set. Code that does is the rest of a function whose start set them (on a DS game: pieces
# "with r4/r5 live in", a jump table's cases cut from their switch, each given up on by four to eight agents)
CALLEE_SAVED = {"r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "sb", "sl", "fp"}
_READERS = {thumb: capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM)
            for thumb in (False, True)}
for _md in _READERS.values():
    _md.detail = True


def asm_reason(code: bytes, big_endian: bool = False, thumb: bool | None = None, **_facts) -> str | None:
    """Why this ARM or Thumb function can't come from a compiler (a BIOS call stub; in ARM code, the CPU's mode or
    coprocessor 15), or None. `thumb`: whether it's Thumb code, None when the object doesn't say."""
    if big_endian:
        return None
    if thumb is False:
        for at in range(0, len(code) - 3, 4):
            w = int.from_bytes(code[at:at + 4], "little")
            if w >> 28 != 0xF and (what := next((x for m, v, x in SYSTEM if w & m == v), None)):
                return f"uses {what}, the CPU's mode or its cache: no compiler emits it, so it's hand-written assembly"
    if thumb is not None and (why := piece(code, thumb)):
        return why
    if len(code) > STUB_BYTES:
        return None
    halves = [int.from_bytes(code[i:i + 2], "little") for i in range(0, len(code) - 1, 2)]
    if THUMB_BX_LR in halves and any(h & 0xFF00 == THUMB_SVC for h in halves[:halves.index(THUMB_BX_LR)]):
        return "calls the BIOS with svc (a Thumb stub): no compiler emits svc, so it's hand-written assembly"
    words = [int.from_bytes(code[i:i + 4], "little") for i in range(0, len(code) - 3, 4)]
    if ARM_BX_LR in words and any(w & ARM_SWI == ARM_SWI and w >> 28 != 0xF for w in words[:words.index(ARM_BX_LR)]):
        return "calls the BIOS with swi (an ARM stub): no compiler emits swi, so it's hand-written assembly"
    return None


def piece(code: bytes, thumb: bool) -> str | None:
    """Why this code is a piece of a function rather than one, or None: it pops pc without having pushed anything,
    starts with a jump through a register its caller set (an interworking veneer), uses one of the caller's registers
    (CALLEE_SAVED) before saving or setting it, or runs to its end without returning. Code that ends in a call isn't
    judged (the call may never return), nor code capstone can't read.

    The registers are read over the whole function, past its first branch (a piece cut from a switch starts with one):
    compiled code saves them at its start. Not the literal pool's words (what it loads pc-relative), which read as
    nonsense instructions, nor past a table of addresses a jump reads (ldr pc, [pc, rN])."""
    ins = list(_READERS[thumb].disasm(code, 0))
    if not ins:
        return None
    if ins[0].mnemonic == "bx" and ins[0].op_str != "lr":
        return f"jumps through {ins[0].op_str}, which its caller set (an interworking veneer): no C makes that"
    pool = set()
    for i in ins:
        for op in i.operands:
            if op.type == capstone.arm.ARM_OP_MEM and op.mem.base == capstone.arm.ARM_REG_PC and not op.mem.index:
                at = ((i.address + 4) & ~3 if thumb else i.address + 8) + op.mem.disp
                pool |= {at, at + 2}
    saved, ready, ended = False, set(), False  # pushed anything; the caller's registers saved or set; returned
    for i in ins:
        if i.address in pool:
            continue
        name, ops, always = i.mnemonic, i.op_str, i.cc in (capstone.arm.ARM_CC_AL, capstone.arm.ARM_CC_INVALID)
        if name.startswith("ldr") and ops.startswith("pc, [pc, r"):
            break  # a table of addresses follows: not instructions
        if name in ("push", "stmdb", "stmfd") and (name == "push" or ops.startswith("sp!")) or \
                (name.startswith("str") and "[sp, #-" in ops and ops.endswith("!")):
            saved = True
            ready |= {i.reg_name(r) for r in i.regs_access()[0]} & CALLEE_SAVED
            continue
        if name.startswith(("pop", "ldm")) and "pc" in ops:
            if not saved:
                return f"returns without having saved what it restores: the end of {_PIECE}"
            ended = True
            continue
        read, written = ({i.reg_name(r) for r in regs} & CALLEE_SAVED for regs in i.regs_access())
        if (used := sorted(read - ready) or sorted(written - ready)):
            said = "reads" if read - ready else "sets"
            return f"{said} {used[0]} before saving it, so the code before it set it up: the rest of {_PIECE}"
        ready |= written
        if always and (name == "b" or name == "bx" or ops.startswith("pc,")):
            ended = True  # a return, a tail call or a loop: a whole function's end, or a branch inside it
    if ended or sum(i.size for i in ins) < len(code) or ins[-1].mnemonic in ("bl", "blx"):
        return None
    if ins[0].mnemonic in ("push", "stmdb", "stmfd") and "lr" not in ins[0].op_str:
        return f"saves registers and runs on into the next function: the start of {_PIECE}"
    return f"never returns: {_PIECE}"


def jumps(code: bytes, thumb: bool, at: int) -> list[int]:
    """Where the code at `at` branches (b and its conditional forms, not calls), its literal pool's words left out. A
    compiled function branches only within itself or to another's start (a tail call): a branch into the middle of
    another symbol says that symbol is part of it (on a DS game, a switch whose cases ds-decomp cut into the symbols
    after it)."""
    ins = list(_READERS[thumb].disasm(code, at))
    pool = set()
    for i in ins:
        for op in i.operands:
            if op.type == capstone.arm.ARM_OP_MEM and op.mem.base == capstone.arm.ARM_REG_PC and not op.mem.index:
                where = ((i.address + 4) & ~3 if thumb else i.address + 8) + op.mem.disp
                pool |= {where, where + 2}
    out = []
    for i in ins:
        if i.address in pool:
            continue
        if i.mnemonic.startswith("ldr") and i.op_str.startswith("pc, [pc, r"):
            break  # a table of addresses follows
        if i.mnemonic.startswith(("b", "cb")) and not i.mnemonic.startswith(("bl", "bx", "bic", "bkpt")) and \
                i.operands and i.operands[-1].type == capstone.arm.ARM_OP_IMM:
            out.append(i.operands[-1].imm)
    return out
