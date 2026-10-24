"""x86 (Windows PC games; Xbox in public decomps): what decompdecomp knows about the processor itself.

The relocations a match ignores, how capstone reads the code, and the pieces of functions Ghidra's analysis cut off
(`piece`). Hand-written assembly, the compiler map and class recovery have no x86 reader yet (ROADMAP.md says where
to start).
"""

from __future__ import annotations

import re

import capstone
from capstone import x86

RELOC_MASKS: dict[int, tuple[int, int, int]] = {}  # every x86 relocation fills the 4 bytes at r_offset (the default)
CAPSTONE = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_32)}

RELOC_MASKS_64 = {1: (0, 8, 0xFFFFFFFFFFFFFFFF)}  # R_X86_64_64; the rest fill 4 bytes
CAPSTONE_64 = {False: (capstone.CS_ARCH_X86, capstone.CS_MODE_64)}

_READER = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_READER.detail = True
# ebx, esi, edi and ebp belong to the caller (every x86 calling convention MSVC uses): compiled code saves one (push)
# before it sets it, and never reads one it hasn't set. Code that does continues a function whose start set them
CALLEE_SAVED = ("ebx", "esi", "edi", "ebp")
_PIECE = "a piece cut off the function before it (Ghidra put a function's start in its middle), which no C makes alone"
_PADDING = ("nop", "int3")  # what Visual C++ and the linker fill the bytes between functions with
_ENDS = ("ret", "retn", "jmp", "hlt", *_PADDING)  # what doesn't run on into the next instruction
SHORT_JUMP_BYTES = 2  # jmp rel8, jcc rel8
IMM32_INSTRUCTION_BYTES = 5  # mov eax, imm32; push imm32: an address


def _frame_helper(ins: list) -> bool:
    """Whether the code starts by calling a helper that sets up its frame: `mov eax, <handler>; call __EH_prolog`
    (MSVC's C++ functions, built small; `push <n>` first for __EH_prolog3), or `push <n>; push <scope table>; call
    __SEH_prolog` (the C runtime's). The helper sets ebp and saves the caller's registers, so what the code does
    with them after is its own. Only these shapes: `push 0xc; call _lock` is an ordinary call, and a piece after
    such a function isn't its."""
    head = next((ins[:k] for k in (1, 2) if len(ins) > k and ins[k].mnemonic == "call"), [])
    kinds = ["" if not i.operands or i.operands[-1].type != x86.X86_OP_IMM else "push" if i.mnemonic == "push"
             else "mov eax" if i.mnemonic == "mov" and i.op_str.startswith("eax,") else "" for i in head]
    return kinds in (["mov eax"], ["push", "mov eax"], ["push", "push"]) and head[-1].size == IMM32_INSTRUCTION_BYTES


def _sets_frame(ins: list) -> bool:
    """Whether the code sets up an ebp frame: `mov ebp, esp`, `enter`, or a helper that does it (_frame_helper)."""
    return _frame_helper(ins) or any(i.mnemonic == "enter" or i.mnemonic == "mov" and i.op_str == "ebp, esp" for i in ins)


def _filler(ins) -> bool:
    """Whether the instruction does nothing: `mov edi, edi`, `lea esp, [esp]`, the multi-byte nops MSVC aligns code
    with (between a C runtime's hand-written functions, before a jump table)."""
    ops = ins.operands
    if len(ops) != 2 or ops[0].type != x86.X86_OP_REG:
        return False
    if ins.mnemonic == "mov":
        return ops[1].type == x86.X86_OP_REG and ops[1].reg == ops[0].reg
    return ins.mnemonic == "lea" and ops[1].type == x86.X86_OP_MEM and ops[1].mem.base == ops[0].reg and \
        not ops[1].mem.index and not ops[1].mem.disp


def _uses_unset(code: bytes) -> tuple[str, str] | None:
    """(register, what the code does with it) for the first of the caller's registers (CALLEE_SAVED) the code reads
    or restores before saving or setting it, up to its first ret or jmp (past it, code reached by a branch from
    earlier may rely on what that set); None when it never does. xor and sub of a register with itself only set it,
    `enter` saves and sets ebp, and `pushal` saves them all (hand-written helpers in a PC game start with both)."""
    ins = list(_READER.disasm(code, 0))
    if _frame_helper(ins):
        return None
    ready: set[str] = set()
    for i in ins:
        name, ops = i.mnemonic, i.op_str
        if name == "push" and ops in CALLEE_SAVED or name in ("enter", "pushal", "pusha"):
            ready |= {"ebp"} if name == "enter" else set(CALLEE_SAVED) if name != "push" else {ops}
            continue
        if name == "pop" and ops in CALLEE_SAVED and ops not in ready:
            return ops, "restores"
        if name == "leave" and "ebp" not in ready:
            return "ebp", "restores"
        read, written = ({i.reg_name(r) for r in regs} & set(CALLEE_SAVED) for regs in i.regs_access())
        regs = [op.reg for op in i.operands if op.type == x86.X86_OP_REG]
        if name in ("xor", "sub") and len(regs) == 2 == len(i.operands) and regs[0] == regs[1]:
            read = set()
        if _filler(i):  # (MSVC's padding between hand-written functions)
            continue
        if unset := sorted(read - ready):
            return unset[0], "reads"
        ready |= written
        if name in ("ret", "retn", "jmp"):
            return None
    return None


def piece(before: bytes, code: bytes) -> str | None:
    """Why `code` is the rest of the function whose code `before` is, right before it, or None. On a PC game Ghidra
    started 181 of 11,877 functions in the middle of others: a lone epilogue after a call it took for one that never
    returns (`throw`), a catch block or an `__except` filter placed after the function's return. Neither part can
    match: one has no epilogue, the other no prologue.

    `code` reads or restores one of the caller's registers before saving or setting it (an epilogue's `pop esi`, the
    frame's `[ebp-0xc]`), and either `before` runs on into it (its last instruction isn't a ret, a jmp or padding),
    or `before` set up an ebp frame and `code` reads ebp (the frame), or `before` jumps into it or past it with a
    short jump (compiled code reaches another function only by a relocated rel32 jump or call). `before` must end
    with an instruction, not with padding or data: padding ends a function.

    MSVC's C++ unwind funclets (`.text$x`, after the C runtime) read their parent's ebp without a frame too, by
    design, but they're not pieces: each follows another funclet, which ends with a jmp or a ret and never sets up a
    frame, so none of the three holds. They're set aside by a rule of their own; keep the two apart."""
    ins = list(_READER.disasm(before, 0))
    if not ins or sum(i.size for i in ins) != len(before) or ins[-1].mnemonic in _PADDING:
        return None  # data at its end (a jump table), or padding: a function's end
    unset = _uses_unset(code)
    if unset is None:
        return None
    reg, did = unset
    why = f"{did} {reg} before saving or setting it"
    if ins[-1].mnemonic not in _ENDS:
        return f"{why}, and the code before runs on into it: the rest of {_PIECE}"
    if reg == "ebp" and _sets_frame(ins):
        return f"{why}, the frame the code before set up: the rest of {_PIECE}"
    end = len(before)
    for i in ins:
        if i.size == SHORT_JUMP_BYTES and x86.X86_GRP_JUMP in i.groups and i.operands and \
                i.operands[0].type == x86.X86_OP_IMM and i.operands[0].imm >= end:
            return f"{why}, and the code before jumps into it or past it with a short jump: the rest of {_PIECE}"
    return None


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
