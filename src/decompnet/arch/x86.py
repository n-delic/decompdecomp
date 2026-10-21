"""x86 (Windows PC games; Xbox in public decomps): what decompdecomp knows about the processor itself.

Only the relocations a match ignores and how capstone reads the code, for now. Hand-written assembly, the compiler
map and class recovery have no x86 reader yet (ROADMAP.md says where to start).
"""

from __future__ import annotations

import re

import capstone

RELOC_MASKS: dict[int, tuple[int, int, int]] = {}  # every x86 relocation fills the 4 bytes at r_offset (the default)
CAPSTONE = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_32)}

RELOC_MASKS_64 = {1: (0, 8, 0xFFFFFFFFFFFFFFFF)}  # R_X86_64_64; the rest fill 4 bytes
CAPSTONE_64 = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_64)}

# A memory operand naming data by its address (D_00412340, _D_00412340, D_00412340+0x4), as delinked targets name it
_DATA_OPERAND = re.compile(r"\[\s*_?D_([0-9A-Fa-f]{8})(?:\s*\+\s*(0x[0-9A-Fa-f]+|\d+))?\s*\]")


def float_operands(text: str) -> list[tuple[int, str]]:
    """The float constants an instruction loads from data by address, as (address, "f" or "d"): SSE scalar loads and
    arithmetic (movss, mulss, comisd...) and x87 ones (fld, fmul, fcomp...; a qword operand is a double). A diff
    showed `mulss xmm0, [_D_00412340]` against its own `[_kDegToRad]`, and an agent had to read_data to learn
    the target meant 0.0174533 (pi/180)."""
    mnemonic, _, operands = text.strip().partition(" ")
    m = _DATA_OPERAND.search(operands)
    if m is None:
        return []
    op = mnemonic.lower()
    if op.endswith("ss") or op in ("cvtss2sd", "cvtss2si", "cvttss2si"):
        kind = "f"
    elif op.endswith("sd") and op not in ("movsd",) or op in ("cvtsd2ss", "cvtsd2si", "cvttsd2si"):
        kind = "d"
    elif op == "movsd" and "xmm" in operands:  # (movsd without xmm is the string instruction)
        kind = "d"
    elif op.startswith("f") and not op.startswith(("fi", "fst", "fnst", "fldcw", "fnstcw", "fxch")):
        kind = "d" if "qword" in operands.lower() else "f" if "tbyte" not in operands.lower() else ""
    else:
        kind = ""
    if not kind:
        return []
    return [(int(m.group(1), 16) + (int(m.group(2), 0) if m.group(2) else 0), kind)]
