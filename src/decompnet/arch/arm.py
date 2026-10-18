"""ARM and Thumb (only in forks of public decomps: GBA, DS, 3DS): what decompdecomp knows about the processor.

Only the relocations a match ignores and how capstone reads the code: no console started from scratch runs on ARM
in this version.
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
