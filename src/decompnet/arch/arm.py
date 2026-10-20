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
    if len(code) > STUB_BYTES:
        return None
    halves = [int.from_bytes(code[i:i + 2], "little") for i in range(0, len(code) - 1, 2)]
    if THUMB_BX_LR in halves and any(h & 0xFF00 == THUMB_SVC for h in halves[:halves.index(THUMB_BX_LR)]):
        return "calls the BIOS with svc (a Thumb stub): no compiler emits svc, so it's hand-written assembly"
    words = [int.from_bytes(code[i:i + 4], "little") for i in range(0, len(code) - 3, 4)]
    if ARM_BX_LR in words and any(w & ARM_SWI == ARM_SWI and w >> 28 != 0xF for w in words[:words.index(ARM_BX_LR)]):
        return "calls the BIOS with swi (an ARM stub): no compiler emits swi, so it's hand-written assembly"
    return None
