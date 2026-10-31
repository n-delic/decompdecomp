"""PowerPC64, big-endian (the PlayStation 3's PPU): what decompdecomp knows about the processor and its ELFv1 ABI.

- the relocations a match ignores the bits of, how capstone reads the code, and the code no compiler makes (a
  system call, a linker's call stub);
- the link-and-compare judge for ROM mode. A PowerPC64 program reaches functions through descriptors (`.opd`) and
  globals through the TOC (r2), and the linker chooses the encodings, so the judge doesn't patch fields blindly: for
  every relocation of the candidate it works out what the candidate refers to (a function's entry, a global's address,
  what a TOC slot holds) and checks that it is what the game's own instruction refers to (`scan_refs` reads that
  from the game's code once, at import). Only then are the game's field bits taken over, and the rest of the
  function must be equal byte for byte. A wrong callee, a wrong global or a different constant fails.

Two ABIs share it, told apart by the pointer width a project records (`ptr`): Sony's PS3 compilers use 32-bit pointers
in 64-bit registers (4-byte TOC slots and function descriptors of two 32-bit words, "ilp32"); the free PPU compilers
make the usual ELFv1 program (8-byte slots, 24-byte descriptors, "lp64"). Nothing here depends on which.

Not validated without a retail program (ROADMAP.md): whether Sony's linker narrows a TOC-slot load into an `addi`
(LLD does), and when it writes `ld r2,40(r1)` after a call: the second is accepted here (a `nop` the compiler emitted
after a call, which the linker made the TOC restore), the first isn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ..rom.link import LinkResult, significant

R_PPC64_ADDR32, R_PPC64_ADDR16, R_PPC64_ADDR16_LO, R_PPC64_ADDR16_HI, R_PPC64_ADDR16_HA = 1, 3, 4, 5, 6
R_PPC64_REL24, R_PPC64_REL32, R_PPC64_ADDR64 = 10, 26, 38
R_PPC64_TOC16, R_PPC64_TOC16_LO, R_PPC64_TOC16_HI, R_PPC64_TOC16_HA, R_PPC64_TOC = 47, 48, 49, 50, 51
R_PPC64_ADDR16_DS, R_PPC64_ADDR16_LO_DS, R_PPC64_TOC16_DS, R_PPC64_TOC16_LO_DS = 56, 57, 63, 64
R_SLOT_CONSTANT = 1000  # not a relocation: a load of a constant the TOC itself holds (a float, a big integer)

HALF = 0xFFFF
RELOC_MASKS = {  # relocation type -> (byte offset from r_offset, length, big-endian bitmask to clear)
    R_PPC64_ADDR32: (0, 4, 0xFFFFFFFF),
    R_PPC64_ADDR16: (0, 2, HALF), R_PPC64_ADDR16_LO: (0, 2, HALF), R_PPC64_ADDR16_HI: (0, 2, HALF),
    R_PPC64_ADDR16_HA: (0, 2, HALF),
    R_PPC64_REL24: (0, 4, 0x03FFFFFC),
    R_PPC64_REL32: (0, 4, 0xFFFFFFFF),
    R_PPC64_ADDR64: (0, 8, 0xFFFFFFFFFFFFFFFF),
    R_PPC64_TOC16: (0, 2, HALF), R_PPC64_TOC16_LO: (0, 2, HALF), R_PPC64_TOC16_HI: (0, 2, HALF),
    R_PPC64_TOC16_HA: (0, 2, HALF), R_PPC64_TOC: (0, 8, 0xFFFFFFFFFFFFFFFF), R_SLOT_CONSTANT: (0, 2, HALF),
    R_PPC64_ADDR16_DS: (0, 2, 0xFFFC), R_PPC64_ADDR16_LO_DS: (0, 2, 0xFFFC),
    R_PPC64_TOC16_DS: (0, 2, 0xFFFC), R_PPC64_TOC16_LO_DS: (0, 2, 0xFFFC),
}
CAPSTONE = {True: (capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)}

TOC_RELOCS = {R_PPC64_TOC16, R_PPC64_TOC16_LO, R_PPC64_TOC16_HI, R_PPC64_TOC16_HA, R_PPC64_TOC16_DS,
              R_PPC64_TOC16_LO_DS}
ABS_RELOCS = {R_PPC64_ADDR16, R_PPC64_ADDR16_LO, R_PPC64_ADDR16_HI, R_PPC64_ADDR16_HA, R_PPC64_ADDR16_DS,
              R_PPC64_ADDR16_LO_DS}
NOP = 0x60000000
LD_R2_TOC_SAVE = 0xE8410028  # ld r2,40(r1): what the linker writes after a call that goes through a TOC stub
BLR = 0x4E800020
BCTR = 0x4E800420

_LOAD_WIDTH = {32: 4, 34: 1, 40: 2, 42: 2, 48: 4, 50: 8, 58: 8}  # loads by primary opcode: the bytes they read (58: ld)
_D_USES = set(range(32, 56)) | {14, 24}  # D-form loads/stores (integer and float), addi, ori: a register + 16 bits
_DS_USES = {58, 62}  # ld/lwa, std: a register + 14 bits (the low two bits are the opcode's)


def sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _words(code: bytes):
    for at in range(0, len(code) - 3, 4):
        yield int.from_bytes(code[at:at + 4], "big")


# ------------------------------------------------------------------------------------------ hand-written assembly

_LINK_STUB_MAX = 8  # insns


def asm_reason(code: bytes, big_endian: bool = True, **_facts) -> str | None:
    """Why this PowerPC64 function can't come from a compiler, or None: it makes a system call (`sc`: no C compiler
    emits one; the kernel stubs the SDK's libraries link are assembly), returns from an interrupt, or is a short jump
    through the count register with no frame (a call stub the linker made, or an import stub)."""
    words = list(_words(code))
    for w in words:
        if w >> 26 == 17 and w & 2:
            return "makes a system call with sc: no compiler emits it, so it's hand-written assembly"
        if w >> 26 == 19 and (w >> 1) & 0x3FF in (18, 274, 50):
            return "returns from an interrupt: hand-written assembly"
    if 2 <= len(words) <= _LINK_STUB_MAX and words[-1] == BCTR and not any(w >> 26 in (18, 16) for w in words):
        return "loads an address and jumps through the count register without a frame of its own: a stub the linker " \
               "or the SDK's import table made, no C makes it"
    return None


# ------------------------------------------------------------------------------------------------- the game's code

@dataclass
class PpcTarget:
    """A function to judge: its bytes, where they are, and what its instructions refer to."""
    name: str
    vram: int
    code: bytes
    # (field offset, relocation type that would produce it, address referred to, TOC slot address or 0): see scan_refs
    refs: list[tuple[int, int, int, int]] = field(default_factory=list)
    ptr: int = 4  # bytes in a pointer / TOC slot / descriptor word (4: Sony's ABI, 8: ELFv1)
    toc: int = 0  # the program's TOC base (r2)

    def to_json(self) -> list:
        """What the project's table keeps as the function's relocations: (offset, type, address); the slots go in meta."""
        return [list(r[:3]) for r in self.refs]

    @staticmethod
    def refs_from_json(value: list, slots: dict | None = None) -> list[tuple[int, int, int, int]]:
        return [(int(r[0]), int(r[1]), int(r[2]), int((slots or {}).get(str(r[0]), 0))) for r in value]


def slots_of(refs: list[tuple[int, int, int, int]]) -> dict[str, int]:
    """The TOC slot addresses of a function's references, as its row's meta keeps them (offset -> slot)."""
    return {str(r[0]): r[3] for r in refs if r[3]}


def canonical_target(target: PpcTarget):
    """The target in canonical form, for the diff."""
    from ..elfobj import FunctionObj

    return FunctionObj(target.name, "ppc64", True, target.code, [])


_READER = capstone.Cs(*CAPSTONE[True])
_READER.detail = True


def _descriptor_entry(read, addr: int, ptr: int) -> int | None:
    """The code address a function descriptor at `addr` holds."""
    word = read(addr, ptr)
    return None if word is None or len(word) < ptr else int.from_bytes(word, "big")


def scan_refs(code: bytes, vram: int, toc: int, ptr: int, read, inside) -> list[tuple[int, int, int, int]]:
    """What the instructions of a game's function refer to, as PpcTarget.refs: calls (`bl`, and `b` out of the
    function), TOC-relative addresses and slot loads, and absolute address pairs (`lis` + `addi`/`ori`/a load).
    `read(addr, n)` gives the program's bytes; `inside(addr)` says whether an address is in the program's memory.

    A pointer-sized load through the TOC is a slot load: the slot holds the address of the global (or of a function's
    descriptor), which is the reference. When it doesn't look like an address, or the load is a float's, the slot
    holds the constant itself and the reference is the slot (kind R_SLOT_CONSTANT).

    Each reference's offset is the offset of the field a relocation would patch (a call's instruction, the low half
    of the others), so twins mask the same bytes the judge copies."""
    refs: list[tuple[int, int, int, int]] = []
    seen_hi: set[int] = set()  # a `lis`/`addis` shared by several uses is one reference, the first use's
    regs: dict[int, tuple[int, bool, int]] = {}  # register -> (value, TOC-relative?, offset of the instruction that set it)

    def hi_ref(hi_at: int, kind: int, address: int, slot: int) -> None:
        if hi_at >= 0 and hi_at not in seen_hi:
            seen_hi.add(hi_at)
            refs.append((hi_at + 2, kind, address, slot))

    for at in range(0, len(code) - 3, 4):
        w = int.from_bytes(code[at:at + 4], "big")
        op, d, a = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 18:  # b / bl / ba / bla
            li = w & 0x03FFFFFC
            li -= 0x04000000 if li & 0x02000000 else 0
            dest = li if w & 2 else vram + at + li
            if w & 1 or not vram <= dest < vram + len(code):
                refs.append((at, R_PPC64_REL24, dest & 0xFFFFFFFFFFFFFFFF, 0))
            if w & 1:
                regs.clear()
            continue
        if op in (16, 19):  # a conditional branch or blr/bctr: what's held is still held where we scan next
            continue
        if op == 15:  # addis rD, rA, imm (lis when rA is 0)
            imm = sext16(w & HALF) << 16
            if a == 2:
                regs[d] = (toc + imm, True, at)
            elif a == 0:
                regs[d] = (imm & 0xFFFFFFFF, False, at)
            else:
                regs.pop(d, None)
            continue
        if op not in _D_USES and op not in _DS_USES:
            _forget_written(regs, w)
            continue
        source = d if op == 24 else a  # ori rA, rS, imm reads rS
        if source == 2 and op != 24:
            base, is_toc, hi_at = toc, True, -1
        elif source in regs:
            base, is_toc, hi_at = regs[source]
        else:
            _forget_written(regs, w)
            continue
        low = w & HALF if op == 24 else sext16(w & (HALF if op in _D_USES else 0xFFFC))
        address = (base | low) if op == 24 else base + low
        ds = op in _DS_USES
        if is_toc:
            width = _LOAD_WIDTH.get(op, 0)
            slot = address
            value = int.from_bytes(read(slot, width) or b"", "big") if width else None
            if width == ptr and op not in (48, 50) and value and inside(value):  # a slot holding an address
                address = value
            elif width:  # a constant stored in the TOC
                refs.append((at + 2, R_SLOT_CONSTANT, slot, slot))
                hi_ref(hi_at, R_PPC64_TOC16_HA, slot, slot)
                if op not in (48, 50):  # (a float load's register isn't a general one)
                    regs.pop(d, None)
                continue
            else:
                slot = 0  # addi, or a store: the address itself is the reference
            kind = (R_PPC64_TOC16_LO_DS if ds else R_PPC64_TOC16_LO) if hi_at >= 0 else \
                (R_PPC64_TOC16_DS if ds else R_PPC64_TOC16)
            refs.append((at + 2, kind, address, slot))
            hi_ref(hi_at, R_PPC64_TOC16_HA, address, slot)
        else:
            if not inside(address & 0xFFFFFFFF):
                _forget_written(regs, w)
                continue
            if op == 24:
                refs.append((at + 2, R_PPC64_ADDR16_LO, address, 0))
                hi_ref(hi_at, R_PPC64_ADDR16_HI, address, 0)
            else:
                refs.append((at + 2, R_PPC64_ADDR16_LO_DS if ds else R_PPC64_ADDR16_LO, address, 0))
                hi_ref(hi_at, R_PPC64_ADDR16_HA, address, 0)
        if op in (14, 24, 32, 34, 40, 42, 58):  # the destination register now holds something else
            regs.pop(a if op == 24 else d, None)
    return refs


# Opcode 31's extended opcodes by which register they write (the low nine bits: the OE bit is ignored)
_X_WRITES_D = {8, 10, 40, 104, 136, 138, 200, 202, 232, 234, 235, 266, 9, 73, 75, 11, 457, 489, 459, 491,  # arithmetic
               20, 21, 23, 53, 55, 84, 87, 119, 279, 341, 343, 373, 375, 534, 790, 311, 19, 83, 339, 371}  # loads, mf*
_X_WRITES_A = {28, 60, 124, 284, 316, 412, 444, 476, 26, 58, 922, 954, 986, 24, 27, 536, 792, 824, 539, 794, 413, 124}
_UPDATE_FORMS = {33, 35, 37, 39, 41, 43, 45, 49, 51, 53, 55}


def _forget_written(regs: dict, w: int) -> None:
    """Drop the general registers an instruction writes from the ones scan_refs tracks (what it doesn't know it
    writes, it leaves: a stale value costs a missing or wrong reference, which the judge then reports)."""
    op, d, a = w >> 26, (w >> 21) & 31, (w >> 16) & 31
    if op in (7, 8, 12, 13, 14, 32, 34, 40, 42, 58):
        gone = {d} | ({a} if op == 58 and w & 3 == 1 else set())
    elif op in _UPDATE_FORMS:
        gone = {a, d} if op in (33, 35, 41, 43) else {a}
    elif op in (24, 25, 26, 27, 28, 29, 20, 21, 23, 30):
        gone = {a}
    elif op == 46:  # lmw
        gone = set(range(d, 32))
    elif op == 31:
        xo = (w >> 1) & 0x1FF
        gone = {d} if xo in _X_WRITES_D else {a} if xo in _X_WRITES_A else set()
    elif op == 17:
        gone = set(range(32))
    else:
        gone = set()
    for r in gone:
        regs.pop(r, None)


# ----------------------------------------------------------------------------------------------------- the judge

def _locate(elf: ELFFile, name: str) -> tuple[int, int, int] | None:
    """(section index, offset, size) of the code of function `name` in an object: the dotted symbol (.name) of the
    AIX-style ABIs, or the descriptor symbol `name` in `.opd`, whose first word relocates to the code."""
    symtab = elf.get_section_by_name(".symtab")
    if symtab is None:
        return None
    syms = {s.name: s for s in symtab.iter_symbols() if s.name}
    code = syms.get("." + name)
    if code is not None and isinstance(code["st_shndx"], int) and code["st_shndx"] and \
            elf.get_section(code["st_shndx"])["sh_flags"] & 4:
        shndx, start = code["st_shndx"], code["st_value"]
        size = code["st_size"]
    else:
        desc = syms.get(name)
        if desc is None or not isinstance(desc["st_shndx"], int) or not desc["st_shndx"]:
            return None
        section = elf.get_section(desc["st_shndx"])
        if not section.name.startswith(".opd"):
            exe = bool(section["sh_flags"] & 4)
            return (desc["st_shndx"], desc["st_value"], desc["st_size"]) if exe and desc["st_size"] else None
        found = None
        for rsec in elf.iter_sections():
            if isinstance(rsec, RelocationSection) and rsec["sh_info"] == desc["st_shndx"]:
                rsym = elf.get_section(rsec["sh_link"])
                for rel in rsec.iter_relocations():
                    if rel["r_offset"] == desc["st_value"]:
                        target = rsym.get_symbol(rel["r_info_sym"])
                        base = target["st_value"] if target["st_info"]["type"] != "STT_SECTION" else 0
                        found = (target["st_shndx"], base + rel["r_addend"])
        if found is None:
            return None
        shndx, start = found
        size = desc["st_size"] if desc["st_size"] > 24 else 0
        # (a descriptor symbol's size is the descriptor's own: the code runs to the next function symbol)
    if not isinstance(shndx, int) or not shndx:
        return None
    if size <= 0 or size == 24:
        starts = sorted(s["st_value"] for s in symtab.iter_symbols()
                        if s["st_shndx"] == shndx and s["st_value"] > start and s["st_info"]["type"] not in
                        ("STT_SECTION", "STT_FILE"))
        size = (starts[0] if starts else elf.get_section(shndx)["sh_size"]) - start
    return shndx, start, size


def _defined(sym) -> bool:
    return isinstance(sym["st_shndx"], int) and sym["st_shndx"] not in (0, 0xFFF1)


def _clean(name: str) -> str:
    return name[1:] if name.startswith(".") and len(name) > 1 else name


def _width_of(word: int, ptr: int) -> int:
    return _LOAD_WIDTH.get(word >> 26, ptr)


def link(obj: bytes, name: str, target: PpcTarget, symbols, read) -> LinkResult:
    """Link a compiled PowerPC64 function at the target's address and compare it with the game, byte for byte."""
    from ..elfobj import FunctionObj, Reloc

    elf = ELFFile(BytesIO(obj))
    found = _locate(elf, name)
    if found is None:
        return LinkResult(None, False, [f"the object has no function named `{name}`"])
    shndx, start, size = found
    code = bytearray(elf.get_section(shndx).data()[start:start + size])
    tcode = target.code
    ptr = target.ptr
    problems: list[str] = []
    relocs: list[Reloc] = []
    own: dict[tuple[int, int], int] = {}  # data the candidate defines itself: (section, offset) -> the game's address
    refs = {r[0]: r for r in target.refs}  # by field offset

    def problem(text: str) -> None:
        if text not in problems:
            problems.append(text)

    def place(key: tuple[int, int], original: int) -> None:
        if own.setdefault(key, original) != original:
            problem(f"{elf.get_section(key[0]).name}+0x{key[1]:X} is used for two different addresses in the original")

    def same(original: int, expected: int) -> bool:
        """The game's reference is `expected`, or a descriptor whose entry is."""
        mask = 0xFFFFFFFFFFFFFFFF
        if original & mask == expected & mask:
            return True
        return _descriptor_entry(read, original, ptr) == expected & mask

    # the candidate's TOC: slot offset -> what it holds (a symbol and addend), from its relocations
    toc_idx = {s.name: i for i, s in enumerate(elf.iter_sections()) if s.name.startswith(".toc")}
    slots: dict[tuple[int, int], tuple] = {}
    for rsec in elf.iter_sections():
        if isinstance(rsec, RelocationSection) and rsec["sh_info"] in toc_idx.values():
            rsym = elf.get_section(rsec["sh_link"])
            for rel in rsec.iter_relocations():
                slots[(rsec["sh_info"], rel["r_offset"])] = (rsym.get_symbol(rel["r_info_sym"]), rel["r_addend"],
                                                             rel["r_info_type"], rsym)

    def symbol_of(sym) -> tuple[str, bool]:
        if sym["st_info"]["type"] == "STT_SECTION":
            return elf.get_section(sym["st_shndx"]).name, True
        return _clean(sym.name), False

    def expected_for(sym, addend: int, original: int | None, what: str):
        """(the address the candidate's symbol+addend has in the game, whether it is a datum the candidate
        defines itself): for a name of the game, its address; for the function itself, the target's; for the
        candidate's own data, the address the game's code says it is at (it is placed there and its bytes checked)."""
        sname, is_section = symbol_of(sym)
        if sname == ".TOC.":
            return target.toc + addend, False
        mine = _defined(sym) and not (not is_section and symbols.resolve(sname) is not None)
        if not mine:
            base = symbols.resolve(sname)
            if base is None:
                problem(f"`{sname}` is not a known symbol. Use the name the assembly shows (like func_80001234 or "
                        f"D_00A12340), or give that address this name first with rename_symbol.")
                return None, False
            return (base + addend) & 0xFFFFFFFFFFFFFFFF, False
        if sym["st_shndx"] == shndx:
            return (target.vram + (0 if is_section else sym["st_value"]) + addend - start) & 0xFFFFFFFFFFFFFFFF, False
        if original is None:
            problem(f"your {what} refers to data of your own ({sname}) where the original's code has no address to "
                    "compare")
            return None, False
        key = (sym["st_shndx"], addend + (0 if is_section else sym["st_value"]))
        place(key, original)
        return original, True

    for rsec in elf.iter_sections():
        if not isinstance(rsec, RelocationSection) or rsec["sh_info"] != shndx:
            continue
        symtab = elf.get_section(rsec["sh_link"])
        for rel in rsec.iter_relocations():
            at = rel["r_offset"] - start
            if not 0 <= at < len(code):
                continue
            rtype, addend = rel["r_info_type"], rel["r_addend"]
            sym = symtab.get_symbol(rel["r_info_sym"])
            sname, _ = symbol_of(sym)
            relocs.append(Reloc(at, rtype, sname, addend))
            if rtype not in RELOC_MASKS:
                problem(f"relocation type {rtype} isn't supported (this isn't a statically linked PlayStation 3 "
                        "program's kind of object)")
                continue
            delta, length, mask = RELOC_MASKS[rtype]
            insn = (at + delta) & ~3 if length != 8 else at
            field_at = at + delta
            if field_at + length > len(tcode) or field_at + length > len(code):
                problem("your function is longer than the original")
                continue

            ok = False
            if rtype == R_PPC64_REL24:
                word = int.from_bytes(tcode[at:at + 4], "big")
                li = word & 0x03FFFFFC
                if li & 0x02000000:
                    li -= 0x04000000
                dest = li if word & 2 else target.vram + at + li
                expect, _ = expected_for(sym, addend, None, "call")
                if expect is None:
                    continue
                if dest != expect:
                    problem(f"you call {sname} where the original calls {symbols.label(dest)}")
                    continue
                ok = True
                if int.from_bytes(code[at + 4:at + 8], "big") == NOP and \
                        int.from_bytes(tcode[at + 4:at + 8], "big") == LD_R2_TOC_SAVE:
                    code[at + 4:at + 8] = tcode[at + 4:at + 8]  # the linker's TOC restore after a call through a stub
            elif rtype in TOC_RELOCS | ABS_RELOCS:
                ref = refs.get(at)
                if ref is None:
                    problem(f"the original has no address at 0x{target.vram + insn:08X}, where your code refers to "
                            f"{sname}")
                    continue
                if rtype in TOC_RELOCS and sym["st_info"]["type"] == "STT_SECTION" and \
                        elf.get_section(sym["st_shndx"]).name.startswith(".toc"):
                    ok = _toc_slot(elf, slots, sym, addend, ref, target, read, same, expected_for, problem, tcode, insn)
                else:
                    expect, mine = expected_for(sym, addend, ref[2], "reference")
                    if expect is None:
                        continue
                    ok = same(ref[2], expect) or (ref[3] and ref[3] == expect)
                    if not ok:
                        problem(f"you refer to {sname}{f'+0x{addend:X}' if addend else ''} where the original has "
                                f"{symbols.label(ref[2])} at 0x{target.vram + insn:08X}")
            else:
                problem(f"relocation type {rtype} isn't supported yet")
                continue
            if ok:
                cand = int.from_bytes(code[field_at:field_at + length], "big")
                tgt = int.from_bytes(tcode[field_at:field_at + length], "big")
                code[field_at:field_at + length] = ((cand & ~mask) | (tgt & mask)).to_bytes(length, "big")

    _verify_own(elf, name, shndx, start, target, symbols, read, own, problem)
    placements: dict[str, dict[int, int]] = {}
    for (sec_idx, offset), original in own.items():
        placements.setdefault(elf.get_section(sec_idx).name, {})[offset] = original
    linked = bytes(code)
    exact = not problems and linked == tcode
    return LinkResult(FunctionObj(name, "ppc64", True, linked, []), exact, problems, placements)


def _toc_slot(elf, slots, sym, addend, ref, target, read, same, expected_for, problem, tcode, insn) -> bool:
    """A TOC-relative reference to a slot of the candidate's own `.toc`: the slot holds a symbol's address (checked
    against what the game's slot holds), or a constant (checked against the game's bytes at the slot)."""
    key = (sym["st_shndx"], addend)
    slot = slots.get(key)
    if slot is None:  # a constant stored in the TOC: a float, a big integer
        word = int.from_bytes(tcode[insn:insn + 4], "big")
        width = _width_of(word, target.ptr)
        mine = elf.get_section(sym["st_shndx"]).data()[addend:addend + width]
        theirs = read(ref[3] or ref[2], width) if ref[1] == R_SLOT_CONSTANT or ref[3] else None
        if theirs is None or ref[1] != R_SLOT_CONSTANT and not ref[3]:
            problem(f"your TOC holds a constant at 0x{addend:X} where the original's code loads an address")
            return False
        if mine != theirs:
            problem(f"your constant in the TOC ({mine.hex(' ')}) differs from the original's ({theirs.hex(' ')})")
            return False
        return True
    held, held_addend, _, held_symtab = slot
    if ref[1] == R_SLOT_CONSTANT:
        problem("your TOC slot holds an address where the original's holds a constant")
        return False
    expect, _ = expected_for(held, held_addend, ref[2], "TOC slot")
    if expect is None:
        return False
    if not same(ref[2], expect):
        name = held.name if held["st_info"]["type"] != "STT_SECTION" else elf.get_section(held["st_shndx"]).name
        problem(f"you refer to {_clean(name)}{f'+0x{held_addend:X}' if held_addend else ''} where the original has "
                f"0x{ref[2]:08X} at 0x{target.vram + insn:08X}")
        return False
    return True


def _verify_own(elf: ELFFile, name: str, fn_shndx: int, fn_start: int, target: PpcTarget, symbols, read,
                own: dict, problem) -> None:
    """The data the candidate defines itself (strings, tables) must be the original's: placed where the original's
    code points, each datum compared up to the next one, pointers inside it followed to where the original's point."""
    symtab = elf.get_section_by_name(".symtab")
    data_relocs: dict[int, list] = {}

    def relocs_in(sec_idx: int) -> list:
        if sec_idx not in data_relocs:
            out = []
            for rsec in elf.iter_sections():
                if isinstance(rsec, RelocationSection) and rsec["sh_info"] == sec_idx:
                    rsym = elf.get_section(rsec["sh_link"])
                    out += [(rel["r_offset"], rel["r_info_type"], rsym.get_symbol(rel["r_info_sym"]), rel["r_addend"])
                            for rel in rsec.iter_relocations()]
            data_relocs[sec_idx] = out
        return data_relocs[sec_idx]

    def extent(sec_idx: int, off: int) -> int:
        size = elf.get_section(sec_idx)["sh_size"]
        bounds = {s["st_value"] for s in symtab.iter_symbols() if s["st_shndx"] == sec_idx
                  and s["st_info"]["type"] not in ("STT_SECTION", "STT_FILE")}
        bounds |= {o for (i, o) in own if i == sec_idx}
        return next((b for b in sorted(bounds) if b > off), size) - off

    def pointee(s, value: int):
        sname = elf.get_section(s["st_shndx"]).name if s["st_info"]["type"] == "STT_SECTION" else _clean(s.name)
        if not _defined(s) or (s["st_info"]["type"] != "STT_SECTION" and symbols.resolve(sname) is not None):
            base = symbols.resolve(sname)
            return (None if base is None else base + value), None
        in_sec = value + (0 if s["st_info"]["type"] == "STT_SECTION" else s["st_value"])
        if s["st_shndx"] == fn_shndx:
            return target.vram + in_sec - fn_start, None
        return None, (s["st_shndx"], in_sec)

    def in_file(sec_idx: int) -> bool:
        return elf.get_section(sec_idx)["sh_type"] != "SHT_NOBITS" and not elf.get_section(sec_idx).name.startswith(".toc")

    todo = list(own)
    while todo:  # pointers inside the data lead to more of it
        sec_idx, off = todo.pop()
        if not in_file(sec_idx):
            continue
        addr, length = own[(sec_idx, off)], extent(sec_idx, off)
        for at, rtype, s, addend in relocs_in(sec_idx):
            if rtype not in (R_PPC64_ADDR64, R_PPC64_ADDR32) or not off <= at < off + length:
                continue
            _, key = pointee(s, addend)
            width = 8 if rtype == R_PPC64_ADDR64 else 4
            if key is None or key in own or (word := read(addr + at - off, width)) is None:
                continue
            own[key] = int.from_bytes(word, "big")
            todo.append(key)

    def next_start(lo: int) -> int | None:
        found = [a for a in (symbols.next_after(lo - 1), *own.values()) if a is not None and a >= lo]
        return min(found, default=None)

    for (sec_idx, off), addr in sorted(own.items()):
        if not in_file(sec_idx):
            continue
        length = extent(sec_idx, off)
        sec = elf.get_section(sec_idx)
        mine = bytearray(sec.data()[off:off + length])
        if not mine:
            continue
        unplaced = False
        for at, rtype, s, addend in relocs_in(sec_idx):
            at -= off
            if rtype not in (R_PPC64_ADDR64, R_PPC64_ADDR32) or not 0 <= at < length:
                continue
            width = 8 if rtype == R_PPC64_ADDR64 else 4
            dest, key = pointee(s, addend)
            if key is not None:
                dest = own.get(key)
            if dest is None:
                problem(f"can't place a pointer inside your data at {sec.name}+0x{off + at:X}")
                unplaced = True
                break
            mine[at:at + width] = (dest & ((1 << 8 * width) - 1)).to_bytes(width, "big")
        if unplaced:
            continue
        n = significant(bytes(mine), addr, None, next_start)
        theirs = read(addr, n)
        if theirs is not None and theirs != bytes(mine[:n]):
            problem(f"your {sec.name} data at +0x{off:X} differs from the original's at 0x{addr:08X} "
                    f"({symbols.label(addr)}): yours {bytes(mine[:min(n, 24)]).hex(' ')}"
                    f"{'…' if n > 24 else ''}, original {theirs[:24].hex(' ')}{'…' if n > 24 else ''}")
