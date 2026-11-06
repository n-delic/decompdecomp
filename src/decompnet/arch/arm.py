"""ARM and Thumb (DS, and forks of public GBA and 3DS decomps): what decompdecomp knows about the processor.

The relocations a match ignores, how capstone reads the code, the code no compiler makes (BIOS call stubs), and the
link-and-compare judge for ROM mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ..rom.link import LinkResult, significant

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


# The relocation types a GBA candidate's object has. `match.py`'s MIPS judge reads a disassembler's relocations;
# here the original bytes at each relocation say what the candidate must refer to (a Thumb `bl`'s destination, a
# literal-pool word), so the target needs no relocation list of its own: only the function's bytes and its mode.
R_ARM_PC24, R_ARM_ABS32, R_ARM_THM_CALL, R_ARM_CALL, R_ARM_JUMP24, R_ARM_V4BX = 1, 2, 10, 28, 29, 40


@dataclass
class ArmTarget:
    """A function to judge: its bytes, where they are, and whether they are Thumb (the ROM mode's ARM target)."""
    name: str
    vram: int
    code: bytes
    thumb: bool = True
    # (offset, kind, address) of what its code refers to, for xrefs and twins; the judge reads the original bytes
    relocs: list[tuple[int, int, int]] = field(default_factory=list)

    def to_json(self) -> list:
        return [list(r) for r in self.relocs]

    @staticmethod
    def relocs_from_json(value: list) -> list[tuple[int, int, int]]:
        return [tuple(r) for r in value]


def canonical_target(target: ArmTarget):
    """The target in canonical form, for the diff (its processor mode is per function, not per console)."""
    from ..elfobj import FunctionObj

    return FunctionObj(target.name, "thumb" if target.thumb else "arm", False, target.code, [])


def thumb_bl_dest(code: bytes, at: int, pc: int) -> int | None:
    """Destination of a Thumb `bl` pair at code[at:at+4] located at address pc."""
    h1 = int.from_bytes(code[at:at + 2], "little")
    h2 = int.from_bytes(code[at + 2:at + 4], "little")
    if (h1 & 0xF800) != 0xF000 or (h2 & 0xE800) != 0xE800:
        return None
    hi = h1 & 0x7FF
    if hi & 0x400:
        hi -= 0x800
    return (pc + 4 + (hi << 12) + ((h2 & 0x7FF) << 1)) & 0xFFFFFFFF


def arm_b_dest(code: bytes, at: int, pc: int) -> int | None:
    """Destination of an ARM `b`/`bl` word at code[at:at+4] located at address pc."""
    word = int.from_bytes(code[at:at + 4], "little")
    if (word >> 25) & 7 != 5:
        return None
    imm = word & 0xFFFFFF
    if imm & 0x800000:
        imm -= 0x1000000
    return (pc + 8 + (imm << 2)) & 0xFFFFFFFF


def _sym_name(elf: ELFFile, symtab, idx: int):
    sym = symtab.get_symbol(idx)
    if sym["st_info"]["type"] == "STT_SECTION":
        return elf.get_section(sym["st_shndx"]).name, sym, True
    return sym.name, sym, False


def _defined(sym) -> bool:
    return isinstance(sym["st_shndx"], int) and sym["st_shndx"] != 0


def link(obj: bytes, name: str, target: ArmTarget, symbols, read) -> LinkResult:
    """Link a compiled ARM/Thumb function at the target's address and compare it with the ROM, byte for byte."""
    from ..elfobj import FunctionObj, Reloc, _function_symbols

    elf = ELFFile(BytesIO(obj))
    found = _function_symbols(elf).get(name)
    if found is None:
        return LinkResult(None, False, [f"the object has no function named `{name}`"])
    shndx, start, size = found
    start &= ~1  # Thumb functions have the low bit set
    section = elf.get_section(shndx)
    code = bytearray(section.data()[start:start + size])
    problems: list[str] = []
    relocs: list[Reloc] = []
    tcode = target.code
    own: dict[tuple[int, int], int] = {}  # data the candidate defines itself: (section, offset) -> the original's address
    symtab_all = elf.get_section_by_name(".symtab")

    def problem(text: str) -> None:
        if text not in problems:
            problems.append(text)

    def place(key: tuple[int, int], original: int) -> None:
        if own.setdefault(key, original) != original:
            problem(f"{elf.get_section(key[0]).name}+0x{key[1]:X} is used for two different addresses in the original")

    for rsec in elf.iter_sections():
        if not isinstance(rsec, RelocationSection) or rsec["sh_info"] != shndx:
            continue
        symtab = elf.get_section(rsec["sh_link"])
        for rel in rsec.iter_relocations():
            at = rel["r_offset"] - start
            if not 0 <= at < len(code):
                continue
            rtype = rel["r_info_type"]
            if rtype == R_ARM_V4BX:
                continue
            sname, sym, is_section = _sym_name(elf, symtab, rel["r_info_sym"])
            relocs.append(Reloc(at, rtype, sname, 0))
            pc = target.vram + at
            if at + 4 > len(tcode):
                problem("your function is longer than the original")
                continue
            if rtype == R_ARM_ABS32:
                addend = int.from_bytes(code[at:at + 4], "little")
                original = int.from_bytes(tcode[at:at + 4], "little")
                if _defined(sym) and sym["st_shndx"] == shndx:  # a pointer into this function (a switch's jump table)
                    inner = (0 if is_section else (sym["st_value"] & ~1)) + addend - start
                    if (target.vram + inner) & 0xFFFFFFFF != original & ~1 and (target.vram + inner) != original:
                        problem(f"a pointer to your function +0x{inner:X} where the original points at 0x{original:08X}")
                elif not _defined(sym) or (not is_section and symbols.resolve(sname) is not None):
                    base = symbols.resolve(sname)
                    if base is None:
                        problem(f"`{sname}` is not a known symbol. Use the name the assembly shows (like sub_08001234 "
                                f"or gUnknown_03001234), or give that address this name first with rename_symbol.")
                        continue
                    if (base + addend) & 0xFFFFFFFF != original and ((base + addend) | 1) & 0xFFFFFFFF != original:
                        problem(f"a pointer to {sname}{f'+0x{addend:X}' if addend else ''} where the original has "
                                f"0x{original:08X} ({symbols.label(original)})")
                else:  # data this function defines itself (a string, a table): the original's copy is at `original`
                    place((sym["st_shndx"], addend + (0 if is_section else sym["st_value"])), original)
                code[at:at + 4] = tcode[at:at + 4]
            elif rtype in (R_ARM_THM_CALL,):
                dest = thumb_bl_dest(tcode, at, pc)
                base = symbols.resolve(sname)
                if base is None:
                    problem(f"`{sname}` is not a known function. Call it by the name the assembly shows (like "
                            f"sub_08001234), or give that address this name first with rename_symbol.")
                    continue
                if dest is None or (dest & ~1) != (base & ~1):
                    problem(f"you call {sname} where the original calls "
                            f"{symbols.label(dest) if dest is not None else 'something else'}")
                    continue
                code[at:at + 4] = tcode[at:at + 4]
            elif rtype in (R_ARM_PC24, R_ARM_CALL, R_ARM_JUMP24):
                dest = arm_b_dest(tcode, at, pc)
                base = symbols.resolve(sname)
                if base is None or dest is None or (dest & ~1) != (base & ~1):
                    problem(f"you branch to {sname} where the original goes to "
                            f"{symbols.label(dest) if dest is not None else 'something else'}")
                    continue
                code[at:at + 4] = tcode[at:at + 4]
            else:
                problem(f"relocation type {rtype} isn't supported yet")

    _verify_own(elf, symtab_all, name, shndx, start, target, symbols, read, own, problem)
    placements: dict[str, dict[int, int]] = {}
    for (sec_idx, offset), original in own.items():
        placements.setdefault(elf.get_section(sec_idx).name, {})[offset] = original
    linked = bytes(code)
    exact = not problems and linked == tcode[:len(linked)] and len(linked) == len(tcode)
    return LinkResult(FunctionObj(name, "thumb" if target.thumb else "arm", False, linked, []), exact, problems,
                      placements)


def _verify_own(elf: ELFFile, symtab, name: str, fn_shndx: int, fn_start: int, target: ArmTarget, symbols,
                read, own: dict, problem) -> None:
    """The data the candidate defines itself must be the original's: placed where the original's code points, each
    datum compared up to the next one (after every place is known), pointers inside it (a table of strings)
    followed to where the original's pointers point."""
    data_relocs: dict[int, list] = {}

    def relocs_in(sec_idx: int) -> list:
        if sec_idx not in data_relocs:
            out = []
            for rsec in elf.iter_sections():
                if isinstance(rsec, RelocationSection) and rsec["sh_info"] == sec_idx:
                    rsym = elf.get_section(rsec["sh_link"])
                    out += [(rel["r_offset"], rel["r_info_type"], rsym.get_symbol(rel["r_info_sym"]))
                            for rel in rsec.iter_relocations()]
            data_relocs[sec_idx] = out
        return data_relocs[sec_idx]

    def extent(sec_idx: int, off: int) -> int:
        size = elf.get_section(sec_idx)["sh_size"]
        bounds = {s["st_value"] for s in symtab.iter_symbols() if s["st_shndx"] == sec_idx
                  and s["st_info"]["type"] not in ("STT_SECTION", "STT_FILE") and not s.name.startswith("$")}
        bounds |= {o for (i, o) in own if i == sec_idx}
        return next((b for b in sorted(bounds) if b > off), size) - off

    def pointee(s, value: int) -> tuple[int | None, tuple[int, int] | None]:
        sname = elf.get_section(s["st_shndx"]).name if s["st_info"]["type"] == "STT_SECTION" else s.name
        if not _defined(s) or (s["st_info"]["type"] != "STT_SECTION" and symbols.resolve(sname) is not None):
            base = symbols.resolve(sname)
            return (None if base is None else (base + value) & 0xFFFFFFFF), None
        in_sec = value + (0 if s["st_info"]["type"] == "STT_SECTION" else s["st_value"])
        if s["st_shndx"] == fn_shndx:
            return (target.vram + (in_sec & ~1) - fn_start) & 0xFFFFFFFF, None
        return None, (s["st_shndx"], in_sec)

    def in_rom(sec_idx: int) -> bool:
        return elf.get_section(sec_idx)["sh_type"] != "SHT_NOBITS"

    todo = list(own)
    while todo:  # pointers inside the data lead to more of it
        sec_idx, off = todo.pop()
        if not in_rom(sec_idx):
            continue
        addr, length, data = own[(sec_idx, off)], extent(sec_idx, off), elf.get_section(sec_idx).data()
        for at, rtype, s in relocs_in(sec_idx):
            if rtype != R_ARM_ABS32 or not off <= at <= off + length - 4:
                continue
            _, key = pointee(s, int.from_bytes(data[at:at + 4], "little"))
            if key is None or key in own or (word := read(addr + at - off, 4)) is None:
                continue
            own[key] = int.from_bytes(word, "little")
            todo.append(key)

    def next_start(lo: int) -> int | None:
        found = [a for a in (symbols.next_after(lo - 1), *own.values()) if a is not None and a >= lo]
        return min(found, default=None)

    for (sec_idx, off), addr in sorted(own.items()):
        if not in_rom(sec_idx):
            continue
        length = extent(sec_idx, off)
        sec = elf.get_section(sec_idx)
        mine = bytearray(sec.data()[off:off + length])
        if not mine:
            continue
        unplaced = False
        for at, rtype, s in relocs_in(sec_idx):
            at -= off
            if rtype != R_ARM_ABS32 or not 0 <= at <= length - 4:
                continue
            dest, key = pointee(s, int.from_bytes(mine[at:at + 4], "little"))
            if key is not None:
                dest = own.get(key)
            if dest is None:
                problem(f"can't place a pointer inside your data at {sec.name}+0x{off + at:X}")
                unplaced = True
                break
            word = read(addr + at, 4)
            if word is not None and (int.from_bytes(word, "little") & ~1) == (dest & ~1):
                dest = int.from_bytes(word, "little")  # a Thumb function's address has its low bit set
            mine[at:at + 4] = (dest & 0xFFFFFFFF).to_bytes(4, "little")
        if unplaced:
            continue
        n = significant(bytes(mine), addr, None, next_start)
        rom = read(addr, n)
        if rom is not None and rom != bytes(mine[:n]):
            problem(f"your {sec.name} data at +0x{off:X} differs from the original's at 0x{addr:08X} "
                    f"({symbols.label(addr)}): yours {bytes(mine[:min(n, 24)]).hex(' ')}"
                    f"{'…' if n > 24 else ''}, original {rom[:24].hex(' ')}{'…' if n > 24 else ''}")
