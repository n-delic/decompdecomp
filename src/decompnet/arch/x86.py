"""x86 (Windows PC games; Xbox in public decomps): what decompdecomp knows about the processor itself.

The relocations a match ignores, how capstone reads the code, the float constants an instruction loads, the
constants a function stores into objects (a constructor's vtable: consoles/pc/rtti.py), and Ghidra's drafts of
methods shown as `Class::method`. Hand-written assembly, the
compiler map and the fields of classes and structs have no x86 reader yet (ROADMAP.md says where to start).
"""

from __future__ import annotations

import re

import capstone
from capstone import x86 as cs_x86

RELOC_MASKS: dict[int, tuple[int, int, int]] = {}  # every x86 relocation fills the 4 bytes at r_offset (the default)
CAPSTONE = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_32)}

RELOC_MASKS_64 = {1: (0, 8, 0xFFFFFFFFFFFFFFFF)}  # R_X86_64_64; the rest fill 4 bytes
CAPSTONE_64 = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_64)}

# A memory operand naming data by its address (D_00412340, _D_00412340, D_00412340+0x4), as delinked targets name it
_DATA_OPERAND = re.compile(r"\[\s*_?D_([0-9A-Fa-f]{8})(?:\s*\+\s*(0x[0-9A-Fa-f]+|\d+))?\s*\]")


FRAME_REGISTERS = {cs_x86.X86_REG_ESP, cs_x86.X86_REG_EBP}  # a store through these is to a local, not an object


def immediate_stores(code: bytes, addr: int) -> dict[int, int]:
    """Where a function stores a 32-bit constant into an object through a register (`mov dword ptr [esi + 8],
    offset X`): the constant's offset in the code -> the displacement. A constructor stores its class's vtable so,
    at +0 (a second base's at its place). Stores to locals (esp, ebp) and to absolute addresses are left out."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    out = {}
    for ins in md.disasm(code, addr):
        ops = ins.operands
        if ins.id == cs_x86.X86_INS_MOV and len(ops) == 2 and ops[0].type == cs_x86.X86_OP_MEM and ops[0].size == 4 \
                and ops[1].type == cs_x86.X86_OP_IMM and ins.imm_size == 4 and ops[0].mem.index == 0 \
                and ops[0].mem.base not in (0, *FRAME_REGISTERS):
            out[ins.address - addr + ins.imm_offset] = ops[0].mem.disp
    return out


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


# Ghidra's line that starts a function: `undefined4 __fastcall _func_005E2510_4(int param_1, int param_2)` ({key}:
# func_<address>, which every name Ghidra gives the function holds)
_GHIDRA_SIGNATURE = r"^(?P<ret>[^\n(]*?)(?P<conv>__thiscall |__fastcall )?(?P<name>\w*{key}\w*)\((?P<params>[^()\n]*)\)\s*$"
_CPP_NAME = re.compile(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*")
_THIS_IN_ECX = "in_ECX"  # what Ghidra calls a register it sees read before it's written: `this`, in a method


def method_draft(draft: str, key: str, cls: str, name: str, role: str, why: str) -> str | None:
    """Ghidra's draft of a function known as a method of `cls` (`role`: "virtual function 3", found from `why`),
    shown as C++: `cls::name(...)` with `this`. A method gets `this` in ecx (__thiscall), which Ghidra shows as
    in_ECX, or as the first parameter where it took the function for __thiscall or __fastcall (a method built as
    __fastcall, as the project names it, also gets an unused second register argument, edx: dropped when the body
    doesn't use it). None when that can't be done: no signature found, or a class name that isn't a plain C++ name
    (a template's)."""
    found = re.search(_GHIDRA_SIGNATURE.format(key=re.escape(key)), draft, re.M)
    if not found or not _CPP_NAME.fullmatch(cls):
        return None
    params = [p.strip() for p in found.group("params").split(",") if p.strip() not in ("", "void")]
    body = draft[found.end():]
    this = None
    if found.group("conv") and params:
        this = params.pop(0).split()[-1].lstrip("*")
        if found.group("conv").startswith("__fastcall") and params and \
                not re.search(rf"\b{re.escape(params[0].split()[-1].lstrip('*'))}\b", body):
            params.pop(0)
    elif re.search(rf"\b{_THIS_IN_ECX}\b", body):
        this = _THIS_IN_ECX
        body = re.sub(rf"^[ \t]*[\w *]+\b{_THIS_IN_ECX};[ \t]*\n", "", body, count=1, flags=re.M)  # its declaration
    if this:
        body = re.sub(rf"\b{re.escape(this)}\b", "this", body)
    note = (f"/* C++: {name} is {role} of class {cls}, found from {why}.\n"
            f"   A method gets `this` in ecx (__thiscall){f': Ghidra called it {this}' if this else ''}; get_class has "
            "the class. */")
    return f"{draft[:found.start()]}{note}\n{found.group('ret')}{cls}::{name}({', '.join(params)}){body}"
