"""x86 (Windows PC games; Xbox in public decomps): what decompdecomp knows about the processor itself.

Only the relocations a match ignores and how capstone reads the code, for now. Hand-written assembly, the compiler
map and class recovery have no x86 reader yet (ROADMAP.md says where to start).
"""

from __future__ import annotations

import capstone

RELOC_MASKS: dict[int, tuple[int, int, int]] = {}  # every x86 relocation fills the 4 bytes at r_offset (the default)
CAPSTONE = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_32)}

RELOC_MASKS_64 = {1: (0, 8, 0xFFFFFFFFFFFFFFFF)}  # R_X86_64_64; the rest fill 4 bytes
CAPSTONE_64 = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_64)}
