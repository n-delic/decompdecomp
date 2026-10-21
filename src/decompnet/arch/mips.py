"""MIPS (N64, PlayStation 2): what decompdecomp knows about the processor itself, whatever the console.

- the relocations a match ignores the bits of, and how capstone reads its code;
- code no compiler makes (asm_reason): coprocessor 0 (the system: mfc0, mtc0, rfe, eret) or a console's coprocessor 2
  (the PS2's VU0: ctc2, mtc2, lwc2, cop2 commands), which C compilers never emit (C that drives VU0 does it with
  inline asm, which decompdecomp doesn't take); a system call (a kernel entry stub); code that ends without a return
  or a jump and so runs on into the next function (an extra entry point into hand-written code: the splitter cut it
  off); or a first block that reads a temporary register (at, v0, v1, t0-t9) or k0/k1 before setting it (compiled
  code gets its arguments in a0-a3);
- where a stretch of code is loaded (place): its calls are absolute, so they say it. The N64's code segments that
  load later (consoles/n64/code.py) and the PS2's overlays (consoles/ps2/overlays.py) are both found this way.
"""

from __future__ import annotations

import collections
import struct

import capstone

# an instruction word's fields: opcode (6 bits), rs, rt, rd (5 bits each), then SPECIAL's function (6 bits)
OPCODE_SHIFT, RS_SHIFT, RT_SHIFT, RD_SHIFT = 26, 21, 16, 11
REGISTER_BITS, FUNCT_BITS = 0x1F, 0x3F
IMMEDIATE_BITS, IMMEDIATE_SIGN = 0xFFFF, 0x8000  # the 16-bit immediate of loads, stores, addiu, branches
JUMP_TARGET_BITS = 0x03FFFFFF  # j/jal: the target's word address, inside the 256 MB region the jump is in
REGION_BITS = 0xF0000000  # what a j/jal keeps of the address it's at
UPPER_SHIFT, ADDRESS_BITS = 16, 0xFFFFFFFF  # lui sets an address's upper half; addresses are 32-bit

R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16, R_MIPS_GOT16 = 4, 5, 6, 7, 21
RELOC_MASKS = {  # relocation type -> (byte offset from r_offset, length, big-endian bitmask to clear)
    R_MIPS_26: (0, 4, JUMP_TARGET_BITS),
    R_MIPS_HI16: (0, 4, IMMEDIATE_BITS),
    R_MIPS_LO16: (0, 4, IMMEDIATE_BITS),
    R_MIPS_GPREL16: (0, 4, IMMEDIATE_BITS),
    R_MIPS_GOT16: (0, 4, IMMEDIATE_BITS),  # IDO -KPIC
}

# opcodes (the word's top six bits)
SPECIAL, REGIMM, J, JAL, BEQ, COP1 = 0x00, 0x01, 0x02, 0x03, 0x04, 0x11
ADDIU, ORI, LUI, DADDIU, SW, SD, SQ = 0x09, 0x0D, 0x0F, 0x19, 0x2B, 0x3F, 0x1F
GPR_STORES = (SW, SD, SQ)  # a register saved whole (to the stack: sq on the EE)
# SPECIAL's functions
JR, JALR, SYSCALL, ADDU, DADDU = 0x08, 0x09, 0x0C, 0x21, 0x2D
MOVES = (ADDU, 0x25, DADDU)  # addu, or, daddu: with $zero, a move
BRANCHES = {BEQ, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17}  # beq, bne, blez, bgtz and their "likely" forms
REGIMM_BRANCHES = {0x00, 0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13}  # bltz, bgez, bltzl, bgezl, and the -al forms
BGEZ, BGEZAL = 0x01, 0x11  # REGIMM's rt: with $zero, an unconditional branch (b, bal)
BC1 = 0x08  # COP1's rs for bc1f/bc1t
# registers
ZERO, V0, SP, RA = 0, 2, 29, 31


def fields(word: int) -> tuple[int, int, int, int, int]:
    """An instruction word's (opcode, rs, rt, rd, function)."""
    return (word >> OPCODE_SHIFT, (word >> RS_SHIFT) & REGISTER_BITS, (word >> RT_SHIFT) & REGISTER_BITS,
            (word >> RD_SHIFT) & REGISTER_BITS, word & FUNCT_BITS)


def signed_immediate(word: int) -> int:
    """The 16-bit immediate, sign-extended (a load's offset, addiu's addend, a branch's distance in words)."""
    return ((word & IMMEDIATE_BITS) ^ IMMEDIATE_SIGN) - IMMEDIATE_SIGN


def jump_target(word: int, at: int) -> int:
    """Where a j or jal at address `at` goes: its 26 bits, in the region of the address it's at."""
    return ((word & JUMP_TARGET_BITS) << 2) | (at & REGION_BITS)
# MIPS64 decoding also covers MIPS I/II; N64 (MIPS III) and PS2 (R5900) code uses 64-bit instructions.
CAPSTONE = {True: (capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_BIG_ENDIAN),
            False: (capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_LITTLE_ENDIAN)}

JR_RA = 0x03E00008
COP0, COP2 = 0x10, 0x12  # the opcodes of coprocessor 0's and coprocessor 2's instructions
COP2_MEMORY = (0x32, 0x3A, 0x36, 0x3E)  # lwc2, swc2, ldc2 (lqc2 on the EE), sdc2 (sqc2)
EABI_ARGUMENTS = {8, 9, 10, 11}  # t0-t3: arguments too under the EABI
SCAN_BLOCK = 0x400  # code is looked for in blocks of this many bytes; a function's start is at most two before
GP = 28  # the register $gp
GP_SEARCH_BYTES = 0x400  # how far after the entry point start-up code sets $gp


def is_frame(word: int) -> bool:
    """A stack frame made: the first instruction of most compiled functions."""
    return (word & 0xFFFF8000) == 0x27BD8000  # addiu $sp, $sp, -N


def jal_targets(words: tuple[int, ...], region: int) -> list[int]:
    """Where the calls (jal) among `words` go, for code that runs in `region` (a jal keeps its top four bits)."""
    return [jump_target(w, region) for w in words if w >> OPCODE_SHIFT == JAL]


BACK_FUNCTIONS = 64  # how many functions code_before walks back over at most


def code_before(rom: bytes, start: int, floor: int) -> int:
    """Where code that begins with a stack frame at `start` (big-endian) really begins: a function's first
    instructions can come before its frame (loads the compiler moved up), and functions that make no frame (leaves)
    can come before it. Walks back to the previous return (jr ra and its delay slot), padding after it skipped, while
    every word on the way is an instruction, and on over the functions before, down to `floor` at most (where the
    code before it ends). On an N64 game a segment found at its first frame had lost two leaf functions and the
    first two instructions of a third, which could never match."""
    import rabbitizer

    def valid(at: int) -> bool:
        return rabbitizer.Instruction(int.from_bytes(rom[at:at + 4], "big")).isValid()

    def word(at: int) -> int:
        return int.from_bytes(rom[at:at + 4], "big")

    pos = start
    for _ in range(BACK_FUNCTIONS):
        at = pos - 8 if pos - 8 >= floor and word(pos - 8) == JR_RA else pos  # (after a return: the function before)
        boundary = None
        while at - 4 >= floor and at - 4 >= pos - 2 * SCAN_BLOCK:
            at -= 4
            if at - 4 >= floor and word(at - 4) == JR_RA:  # `at` is a delay slot: the function before ends there
                boundary = at + 4
                break
            if not valid(at):
                break
        else:
            boundary = at if at == floor else None  # every word an instruction, down to the code before it
        if boundary is None:
            return pos
        while boundary < pos and word(boundary) == 0:  # padding between functions
            boundary += 4
        if boundary >= pos:
            return pos
        pos = boundary
    return pos


def place(rom: bytes, lo: int, hi: int, floor: int, calls: list[int], known: list[tuple[int, int]],
          regions) -> tuple[int, int, int, int] | None:
    """Where one stretch of big-endian code (`rom[lo:hi]`) is loaded: (start, end, vram, calls that landed on a
    function start), or None. Its own calls (jal: absolute addresses) say it: the load address that makes the most
    of them land on its stack frames' starts, within `regions` (where code of its console runs)."""
    words = struct.unpack(f">{(hi - lo) // 4}I", rom[lo:hi])
    frames = [i * 4 for i, w in enumerate(words) if is_frame(w)]
    returns = [i * 4 for i, w in enumerate(words) if w == JR_RA]
    if len(frames) < 8 or len(returns) < 8:
        return None
    best: tuple[int, int, set[int]] | None = None  # (hits, base, the calls' targets)
    for lo_addr, hi_addr in regions:
        own = set(jal_targets(words, lo_addr))
        votes: collections.Counter = collections.Counter()
        for t in own:
            for f in frames:
                base = t - f
                if lo_addr <= base < hi_addr and base % 4 == 0:
                    votes[base] += 1
        if votes:
            base, hits = votes.most_common(1)[0]
            if best is None or hits > best[0]:
                best = (hits, base, own)
    if best is None:
        return None
    hits, base, own = best  # the address ROM `lo` is loaded at
    if hits < max(8, len(frames) // 10):
        return None
    # the first function: the lowest address called in it (from itself or the known code). Every function returns,
    # so code before `lo` is at most a little: a stray "call" from data words can't drag the start further back.
    back = max(floor, lo - 2 * SCAN_BLOCK) - lo
    called = [t - base for t in own | set(calls) if back <= t - base < hi - lo
              and not any(a <= t < b for a, b in known)]  # a call into known code says nothing about this one
    first = min([frames[0], *called])
    start = code_before(rom, lo + first, max(floor, 0))
    first = start - lo
    end = lo + returns[-1] + 8  # the last return and its delay slot
    end = min((end + 0xF) & ~0xF, len(rom))
    return (start, end, base + first, hits)


_MIPS_TEMPS = {1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 26, 27}  # at, v0-v1, t0-t7, t8-t9, k0-k1
_MIPS_LOAD = set(range(0x20, 0x28)) | {0x37, 0x1A, 0x1B, 0x1E}  # lb..lwr, ld, ldl/ldr, lq (EE)
_MIPS_STORE = set(range(0x28, 0x2F)) | {0x3F, 0x2C, 0x2D, 0x1F}  # sb..swr, sd, sdl/sdr, sq (EE)


def _mips_transfer(w: int) -> bool:
    """An unconditional transfer a function can end with: jr/jalr, j/jal, b (beq zero, zero), bal."""
    op, rs, rt, _, funct = fields(w)
    if op == SPECIAL:
        return funct in (JR, JALR)
    if op in (J, JAL):
        return True
    if op == BEQ and rs == ZERO and rt == ZERO:
        return True
    return op == REGIMM and rs == ZERO and rt in (BGEZ, BGEZAL)


def _mips_branch(w: int) -> bool:
    op, rs = w >> OPCODE_SHIFT, (w >> RS_SHIFT) & REGISTER_BITS
    return _mips_transfer(w) or op == REGIMM or op in BRANCHES or (op in (COP1, COP2) and rs == BC1)


def _mips_uses(w: int) -> tuple[set[int], set[int]] | None:
    """(general registers read, written) by one MIPS instruction, None when not known (no verdict)."""
    op, rs, rt, rd, f = fields(w)
    if op == SPECIAL:
        if f in (0, 2, 3):  # sll, srl, sra (sll zero = nop)
            return ({rt}, {rd})
        if f in (4, 6, 7, 0x14, 0x16, 0x17):  # sllv, srlv, srav, dsllv...
            return ({rs, rt}, {rd})
        if f in (0x38, 0x3A, 0x3B, 0x3C, 0x3E, 0x3F):  # dsll, dsrl, dsra, dsll32...
            return ({rt}, {rd})
        if f == 8:
            return ({rs}, set())
        if f == 9:
            return ({rs}, {rd})
        if f in (0x10, 0x12):  # mfhi, mflo
            return (set(), {rd})
        if f in (0x11, 0x13):  # mthi, mtlo
            return ({rs}, set())
        if f in (0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F):  # mult, multu, div, divu (and doubleword)
            return ({rs, rt}, set())
        if 0x20 <= f <= 0x2F:  # add..sltu, dadd..
            return ({rs, rt}, {rd})
        if f in (0x0C, 0x0D, 0x0F):  # syscall, break, sync
            return (set(), set())
        if 0x30 <= f <= 0x36:  # traps
            return ({rs, rt}, set())
        return None
    if op in (8, 9, 10, 11, 12, 13, 14, 0x18, 0x19):  # addi(u), slti(u), andi, ori, xori, daddi(u)
        return ({rs}, {rt})
    if op == 15:  # lui
        return (set(), {rt})
    if op in _MIPS_LOAD:
        return ({rs}, {rt})
    if op in _MIPS_STORE:
        return ({rs, rt}, set())
    if op in (0x31, 0x35, 0x39, 0x3D):  # lwc1, ldc1, swc1, sdc1: the base register
        return ({rs}, set())
    if op == 0x11:  # FPU: mfc1/dmfc1/cfc1 write a GPR, mtc1/dmtc1/ctc1 read one, the rest none
        return ((set(), {rt}) if rs in (0, 1, 2) else ({rt}, set()) if rs in (4, 5, 6) else (set(), set()))
    if op in (4, 5, 0x14, 0x15):  # beq, bne (likely)
        return ({rs, rt}, set())
    if op in (1, 6, 7, 0x16, 0x17):  # bgez/bltz..., blez, bgtz (likely)
        return ({rs}, set())
    if op in (2, 3):
        return (set(), {31} if op == 3 else set())
    return None


def asm_reason(code: bytes, big_endian: bool, *, eabi: bool = False, cop2: str = "") -> str | None:
    """Why this MIPS function (its code, from the entry) can't come from a compiler, or None. The console says what
    its compilers do: `eabi`, eight integer arguments (a0-a3 and t0-t3, the PS2's EABI) instead of four; `cop2`, what
    coprocessor 2 is when C reaches it only through inline asm ("VU0" on the PS2), "" when it isn't there."""
    order = "big" if big_endian else "little"
    words = [int.from_bytes(code[at:at + 4], order) for at in range(0, len(code) - 3, 4)]
    if not words:
        return None
    for w in words:
        op = w >> OPCODE_SHIFT
        if op == SPECIAL and w & FUNCT_BITS == SYSCALL:  # not break (0x0D): GCC emits that for its own divide-by-zero checks
            return ("makes a system call (syscall): a kernel entry stub, written in assembly; C has no way to emit "
                    "it (the PS2's libkernl stubs: addiu v1, n; syscall; jr ra)")
        if op == COP0:
            return "uses coprocessor 0 (the system's: mfc0, mtc0, rfe, eret): no C compiler emits it"
        if cop2 and (op == COP2 or op in COP2_MEMORY):
            return f"uses {cop2} (coprocessor 2): C reaches it only through inline asm"
    body = list(words)
    while len(body) > 1 and body[-1] == 0:  # alignment padding
        body.pop()
    if not any(_mips_transfer(w) for w in body[-2:]):
        return ("ends without a return or a jump, so it runs on into the next function: an entry point inside "
                "hand-written code (compiled functions end with jr ra)")
    names = {1: "at", 2: "v0", 3: "v1", 24: "t8", 25: "t9", 26: "k0", 27: "k1"}
    temps = _MIPS_TEMPS - EABI_ARGUMENTS if eabi else _MIPS_TEMPS
    args = "a0-a3 and t0-t3" if eabi else "a0-a3"
    written: set[int] = {0}
    for i, w in enumerate(words[:64]):
        uses = _mips_uses(w)
        if uses is None:
            return None
        if (early := sorted((uses[0] - written) & temps)):
            name = names.get(early[0], f"t{early[0] - 8}")
            return f"reads {name} before setting it: compiled code gets its arguments in {args}"
        written |= uses[1]
        if _mips_branch(w) and i + 1 < len(words):  # the first block ends with the branch's delay slot
            slot = _mips_uses(words[i + 1])
            if slot is not None and (early := sorted((slot[0] - written) & temps)):
                name = names.get(early[0], f"t{early[0] - 8}")
                return f"reads {name} before setting it: compiled code gets its arguments in {args}"
            return None
    return None


def gp_from_entry(image: bytes, entry: int, segments: list) -> int:
    """$gp as a little-endian program's start-up code sets it (`lui $gp, hi` + `addiu $gp, $gp, lo`), from its entry
    point; `segments` say where each address is in `image` (rom/split.py's Segment). 0: not found."""
    import rabbitizer

    seg = next((s for s in segments if s.contains(entry, 4)), None)
    if seg is None:
        return 0
    at = seg.rom_start + entry - seg.vram
    hi = None
    for i in range(0, GP_SEARCH_BYTES, 4):
        word = int.from_bytes(image[at + i:at + i + 4], "little")
        ins = rabbitizer.Instruction(word, entry + i)
        name = ins.getOpcodeName()
        if name == "lui" and ins.rt.value == GP:
            hi = (word & IMMEDIATE_BITS) << UPPER_SHIFT
        elif hi is not None and name in ("addiu", "daddiu") and ins.rt.value == GP and ins.rs.value == GP:
            return (hi + signed_immediate(word)) & ADDRESS_BITS
        elif hi is not None and name == "ori" and ins.rt.value == GP and ins.rs.value == GP:
            return hi | (word & IMMEDIATE_BITS)
    return 0
