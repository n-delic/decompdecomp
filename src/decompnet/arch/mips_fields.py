"""MIPS (N64, PlayStation 2): what a function does with pointers, for the struct layouts of structs.py.

One pass in address order, like classes.py: branches aren't followed, so what a register holds is known until an
instruction overwrites it or a call clobbers it. A register holds a pointer (a structs term) plus a constant
(`addiu a0, s0, 0x40` points 0x40 into s0's struct). What it records:
- every load and store through such a register, at the term's offset, with its width and kind (lhu: an unsigned
  16-bit field, lwc1: a float), and through a global's address (%lo, %gp_rel relocations);
- a word loaded from a pointer is itself a pointer's term (the field), whatever it turns out to be: only a load or
  store through it says it is one;
- the pointers in a0-a3 at each call, read after the call's delay slot (where compilers often set a0), what v0
  holds at each return, and pointers stored into fields;
- the stack: a pointer spilled to the frame and loaded back is still the same pointer;
- walks: an argument moved along by a constant inside a loop (memset, strcpy, an array's loop) is a walk.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from ..structs import ARGUMENT, CALL, FIELD, GLOBAL, RETURNED, Facts

R_MIPS_26, R_MIPS_LO16, R_MIPS_GPREL16 = 4, 6, 7
ZERO, V0, SP, RA = 0, 2, 29, 31
ARGUMENT_REGISTERS = (4, 5, 6, 7)  # a0-a3 (the PS2's EABI has t0-t3 too: rarely a struct's pointer)
CALLER_SAVED = set(range(1, 16)) | {24, 25, 31}  # at, v0-v1, a0-a3, t0-t7, t8-t9, ra
SPECIAL, REGIMM, J, JAL, COP1 = 0, 1, 2, 3, 0x11
ADDIU, ORI, LUI, DADDIU = 0x09, 0x0D, 0x0F, 0x19
JR, JALR = 0x08, 0x09  # SPECIAL functions
MOVES = (0x21, 0x25, 0x2D)  # addu, or, daddu: with $zero, a move
BRANCHES = {4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17}  # beq, bne, blez, bgtz and their "likely" forms
REGIMM_BRANCHES = {0, 1, 2, 3, 0x10, 0x11, 0x12, 0x13}  # bltz, bgez, bltzl, bgezl, and the -al forms
BC1 = 8  # COP1's rs for bc1f/bc1t
# loads: opcode -> (bytes, kind); the stores' kind "" doesn't say signed or unsigned
LOADS = {0x20: (1, "s8"), 0x24: (1, "u8"), 0x21: (2, "s16"), 0x25: (2, "u16"), 0x23: (4, "s32"), 0x27: (4, "u32"),
         0x37: (8, "u64"), 0x1E: (16, "u128"), 0x31: (4, "f32"), 0x35: (8, "f64"), 0x36: (16, "vu128")}
STORES = {0x28: (1, ""), 0x29: (2, ""), 0x2B: (4, ""), 0x3F: (8, ""), 0x1F: (16, ""), 0x39: (4, "f32"),
          0x3D: (8, "f64"), 0x3E: (16, "vu128")}
WORD_LOADS = (0x23, 0x27)  # lw, lwu: what a pointer is loaded with (pointers are 32-bit on both consoles)
GPR_STORES = (0x2B, 0x3F, 0x1F)  # sw, sd, sq: a register saved whole to the stack
GPR_LOADS = {0x20, 0x21, 0x23, 0x24, 0x25, 0x27, 0x37, 0x1E, 0x1A, 0x1B, 0x22, 0x26}
FPU_TO_GPR = (0, 1, 2)  # COP1's rs for mfc1, dmfc1, cfc1: they write a general register
NO_DESTINATION = {JR, 0x0C, 0x0D, 0x0F, 0x11, 0x13, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F}  # jr, syscall,
# break, sync, mthi, mtlo, mult(u), div(u) and their doubleword forms
MMI = 0x1C  # the EE's multimedia instructions: they write rd


def _destination(word: int) -> int | None:
    """The general register an instruction writes, if any."""
    op, rs, rt, rd, funct = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, word & 63
    if op == SPECIAL:
        return None if funct in NO_DESTINATION or funct >= 0x30 else rd
    if op == MMI:
        return rd
    if 8 <= op <= LUI or op in (0x18, DADDIU) or op in GPR_LOADS:
        return rt
    if op == COP1 and rs in FPU_TO_GPR:
        return rt
    if op == JAL:
        return RA
    return None


def _signed(word: int) -> int:
    imm = word & 0xFFFF
    return imm - 0x10000 if imm & 0x8000 else imm


def _branch(word: int) -> bool:
    """A conditional branch (with a 16-bit offset): REGIMM's bltz, bgez and their kin, not its traps."""
    op = word >> 26
    if op == REGIMM:
        return (word >> 16) & 31 in REGIMM_BRANCHES
    return op in BRANCHES or (op == COP1 and (word >> 21) & 31 == BC1)


def _loops(words: tuple[int, ...]) -> list[tuple[int, int]]:
    """(first, last) instruction of every backward branch's loop."""
    out = []
    for k, w in enumerate(words):
        if _branch(w) and (target := k + 1 + _signed(w)) <= k:
            out.append((target, k))
    return out


def read_facts(vram: int, code: bytes, relocs: list, big_endian: bool, datum: Callable[[int], int | None]) -> Facts:
    """What the function at `vram` does with pointers. `relocs`: (offset, type, address) of its references;
    `datum(address)`: where the global containing an address starts, None when the address is code (a callback's
    address isn't a struct: on GTA San Andreas, treating them as one made one type of 3,737 functions' pointers)."""
    words = struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4])
    at_word = {off // 4: (rtype, addr) for off, rtype, addr in relocs}

    def global_at(addr: int) -> tuple[tuple, int] | None:
        start = datum(addr)
        return None if start is None else ((GLOBAL, start), addr - start)

    loops = _loops(words)
    regs: dict[int, tuple[tuple, int]] = {r: ((ARGUMENT, n), 0) for n, r in enumerate(ARGUMENT_REGISTERS)}
    stack: dict[int, tuple[tuple, int]] = {}
    out = Facts()
    due: tuple[int, int, str] | None = None  # (delay slot, call's instruction, "call" / "tail" / "return")
    callee = None
    end = vram + len(code)
    for k, w in enumerate(words):
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, _signed(w)
        rtype, addr = at_word.get(k, (None, 0))
        global_ref = rtype in (R_MIPS_LO16, R_MIPS_GPREL16)
        value = None
        if op in LOADS or op in STORES:
            size, kind = LOADS.get(op) or STORES[op]
            place = None  # (term, offset) the instruction reads or writes
            if global_ref:
                place = global_at(addr)
            elif rs in regs:
                term, delta = regs[rs]
                place = (term, delta + imm)
            if place is not None:
                out.accesses.append((place[0], place[1], size, kind))
                if op == 0x2B and rt in regs and regs[rt][1] == 0:  # sw of a pointer: the field's type is its own
                    out.stores.append((place[0], place[1], regs[rt][0]))
                if op in WORD_LOADS:
                    value = ((FIELD, place[0], place[1]), 0)
            elif rs == SP:
                if op in GPR_STORES:
                    if rt in regs:
                        stack[imm] = regs[rt]
                    else:
                        stack.pop(imm, None)
                elif op in GPR_LOADS:
                    value = stack.get(imm)
        elif op in (ADDIU, DADDIU, ORI) and global_ref:  # a global's address
            value = global_at(addr)
        elif op in (ADDIU, DADDIU) and rs in regs:
            term, delta = regs[rs]
            value = (term, delta + imm)
            if rt == rs and any(first <= k <= last for first, last in loops):
                out.walked.add(term)
        elif op == SPECIAL and (w & 63) in MOVES and ZERO in (rs, rt):
            value = regs.get(rt if rs == ZERO else rs)
        target = _destination(w)
        if target and target != ZERO:
            regs.pop(target, None)
            if value is not None:
                regs[target] = value
        if due is not None and due[0] == k:
            _, call_at, what = due
            if what in ("call", "tail"):
                for n, r in enumerate(ARGUMENT_REGISTERS):
                    if r in regs and regs[r][1] == 0:
                        out.calls.append((callee, n, regs[r][0]))
                result = (CALL, call_at, callee)
                out.calls.append((callee, RETURNED, result))
                if what == "tail":
                    out.returns.append(result)
            elif what == "return" and V0 in regs and regs[V0][1] == 0:
                out.returns.append(regs[V0][0])
            if what in ("call", "tail", "indirect"):
                for r in CALLER_SAVED:
                    regs.pop(r, None)
                if what == "call":
                    regs[V0] = ((CALL, call_at, callee), 0)
            due = None
        # a transfer's effects come after its delay slot
        if op == JAL:
            callee = addr if rtype == R_MIPS_26 else ((w & 0x3FFFFFF) << 2) | (vram & 0xF0000000)
            due = (k + 1, k, "call")
        elif op == J:
            there = addr if rtype == R_MIPS_26 else ((w & 0x3FFFFFF) << 2) | (vram & 0xF0000000)
            if not vram <= there < end:  # a jump out of the function: a tail call
                callee = there
                due = (k + 1, k, "tail")
        elif op == SPECIAL and (w & 63) == JR and rs == RA:
            due = (k + 1, k, "return")
        elif op == SPECIAL and (w & 63) == JALR:
            due = (k + 1, k, "indirect")
    return out
