"""PSP programs: an ELF for the Allegrex, most often a relocatable module (a PRX: ELF type 0xFFA0, every section based
at 0), which the PSP's module loader places at a free address and relocates. decompdecomp's ROM mode wants code at
fixed addresses, so a PRX is laid out here the way the loader lays out a game's main module, at USER_BASE: every
relocation applied (PSP's own kind, SHT_PSP_REL), the sections and symbols at their addresses there, and the debug
sections' addresses with them, so the program reads like an ordinary executable (ET_EXEC) from then on. A fixed
program (some games' EBOOT.BIN) is already that.

What the module itself says is read here too: its $gp (the module info's gp_value), whether it keeps any data
$gp-relative at all, and the names of the system functions it imports (each library's stub table: a NID per stub in
.sceStub.text, which the loader links).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from ...arch import mips
from ...rom import RomError

ELF_MAGIC = b"\x7fELF"
ELF_HEADER_BYTES, SECTION_HEADER_BYTES, PROGRAM_HEADER_BYTES = 0x34, 40, 32
EM_MIPS = 8
ET_EXEC, ET_PSP_PRX = 2, 0xFFA0
EF_MIPS_MACH, EF_MIPS_MACH_ALLEGREX = 0x00FF0000, 0x00A20000  # e_flags' machine variant: the PSP's Allegrex
SHT_NULL, SHT_SYMTAB, SHT_NOBITS, SHT_REL = 0, 2, 8, 9
SHT_MIPS_DEBUG = 0x70000005  # Metrowerks' linker gives its debug sections' relocations this type
SHT_PSP_REL = 0x700000A0  # a PRX's relocations of a loaded section
PT_NULL, PT_LOAD = 0, 1
SHF_ALLOC, SHF_EXECINSTR = 2, 4
SHN_LORESERVE = 0xFF00
USER_BASE = 0x08804000  # where the loader puts a game's main module: the start of user memory, past the kernel's part
R_MIPS_NONE, R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16 = 0, 2, 4, 5, 6, 7
# the loads and stores, whose rs is their base register: lb to swr, cache, lwc1 and swc1, and the VFPU's lv.s, lvl.q,
# lv.q, sv.s, svl.q, sv.q (its other opcodes in that range are arithmetic, with a vector register where rs is)
LOADS_STORES = frozenset(range(0x20, 0x30)) | {0x31, 0x39, 0x32, 0x35, 0x36, 0x3A, 0x3D, 0x3E}
GP_RELATIVE = {R_MIPS_GPREL16, 8, 12}  # GPREL16, LITERAL (a float constant), GPREL32: data counted from $gp
REL_BYTES = 8  # an Elf32_Rel
WORD = 0xFFFFFFFF
JUMP_BITS, REGION_BITS = 0x03FFFFFF, 0x0FFFFFFF  # j/jal's target, and the 256 MB it reaches
LOW_HALF = 0xFFFF
MODULE_INFO = ".rodata.sceModuleInfo"
MODULE_INFO_NAME, MODULE_INFO_GP = 4, 32  # SceModuleInfo: attributes (2), version (2), name (28), then gp_value
STUB_SECTION, STUB_TABLES = ".sceStub.text", ".lib.stub"
STUB_BYTES = 8  # each imported function's stub: jr ra; nop, until the loader links it (j or syscall)
LIBRARY_NAME, STUB_LENGTH, STUB_COUNT, NID_TABLE, STUB_TABLE = 0, 8, 10, 12, 16  # a SceLibraryStubTable's fields
LIBRARY_NAME_BYTES = 64
SYMBOL_BYTES, SYMBOL_TYPE, STT_SECTION = 16, 0xF, 3  # an Elf32_Sym; its st_info's type, a section's symbol


def is_elf(data: bytes) -> bool:
    """A 32-bit little-endian MIPS ELF of any type."""
    return (len(data) > ELF_HEADER_BYTES and data[:4] == ELF_MAGIC and data[4:6] == b"\x01\x01"
            and struct.unpack_from("<H", data, 18)[0] == EM_MIPS)


def is_psp_elf(data: bytes) -> bool:
    """A PSP program: a MIPS ELF that is a PRX, or whose flags say the Allegrex (a PS2 program says the R5900)."""
    if not is_elf(data):
        return False
    e_type, = struct.unpack_from("<H", data, 16)
    flags, = struct.unpack_from("<I", data, 36)
    return e_type == ET_PSP_PRX or flags & EF_MIPS_MACH == EF_MIPS_MACH_ALLEGREX


@dataclass
class _Section:
    index: int
    name: str
    kind: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int
    info: int


def _sections(data: bytes) -> list[_Section]:
    shoff, = struct.unpack_from("<I", data, 32)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 46)
    if not shoff or shentsize != SECTION_HEADER_BYTES or shoff + shnum * SECTION_HEADER_BYTES > len(data) \
            or shstrndx >= shnum:
        raise RomError("this PSP program's section headers are damaged: import the program as it came on the disc")
    heads = [struct.unpack_from("<10I", data, shoff + SECTION_HEADER_BYTES * i) for i in range(shnum)]
    strings = data[heads[shstrndx][4]:heads[shstrndx][4] + heads[shstrndx][5]]
    return [_Section(i, strings[h[0]:strings.find(b"\0", h[0])].decode("latin-1"), h[1], h[2], h[3], h[4], h[5], h[6],
                     h[7]) for i, h in enumerate(heads)]


def _segments(data: bytes) -> list[list[int]]:
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    return [list(struct.unpack_from("<8I", data, phoff + phentsize * i)) for i in range(phnum)]


def relocate(data: bytes, base: int = USER_BASE) -> bytes:
    """A PRX laid out at `base` as a fixed program (ET_EXEC); a program that isn't a PRX comes back as it is.

    The loader's way (PPSSPP's ElfReader does the same): a relocation's info keeps, besides its type, which program
    segment its offset is in and which segment's address it adds, so with the usual single segment every relocated
    word gets `base` added to the address it holds. A HI16's half is worked out with the LO16 after it, as the pair's
    address can carry into the upper half. The debug sections' relocations are ordinary ones against symbols: a word
    there that points into a loaded section moves with it, one that points into the debug data itself (DWARF 1's
    sibling links) doesn't."""
    if not is_elf(data) or struct.unpack_from("<H", data, 16)[0] != ET_PSP_PRX:
        return data
    sections = _sections(data)
    segments = _segments(data)
    out = bytearray(data)
    for rel in sections:
        target = sections[rel.info] if rel.info < len(sections) else None
        if target is None or not rel.name.startswith(".rel") and rel.kind != SHT_PSP_REL:
            continue
        if rel.kind == SHT_PSP_REL and target.flags & SHF_ALLOC:
            _apply_loader_relocations(data, out, rel, segments, base)
        elif rel.kind in (SHT_REL, SHT_MIPS_DEBUG) and not target.flags & SHF_ALLOC:
            _apply_debug_relocations(data, out, rel, target, sections, base)
    _move_symbols(data, out, sections, base)
    _fix_headers(out, sections, segments, base)
    return bytes(out)


def _apply_loader_relocations(data: bytes, out: bytearray, rel: _Section, segments: list[list[int]], base: int) -> None:
    entries = [struct.unpack_from("<II", data, rel.offset + REL_BYTES * k) for k in range(rel.size // REL_BYTES)]

    def where(offset: int, segment: int) -> int:
        """The file offset of a word the relocation's offset names in a segment."""
        if segment >= len(segments) or offset + 4 > segments[segment][4]:
            raise RomError("this PSP program has a relocation outside its code and data: it may be damaged, or "
                           "packed in a way this app doesn't read yet")
        return segments[segment][1] + offset

    for k, (offset, info) in enumerate(entries):
        kind, segment, relative_to = info & 0xFF, (info >> 8) & 0xFF, (info >> 16) & 0xFF
        if kind in (R_MIPS_NONE, R_MIPS_GPREL16):  # $gp-relative: the distance doesn't move
            continue
        at = where(offset, segment)
        shift = segments[relative_to][2] + base if relative_to < len(segments) else base
        word, = struct.unpack_from("<I", data, at)
        if kind == R_MIPS_32:
            word = (word + shift) & WORD
        elif kind == R_MIPS_26:
            word = (word & ~JUMP_BITS & WORD) | ((((word & JUMP_BITS) << 2) + shift) & REGION_BITS) >> 2
        elif kind == R_MIPS_HI16:
            low = next((o for o, i in entries[k + 1:] if i & 0xFF == R_MIPS_LO16), None)
            if low is None:
                raise RomError("this PSP program has an address's upper half without its lower half: it may be "
                               "damaged")
            low_half, = struct.unpack_from("<h", data, where(low, segment))
            address = ((word & LOW_HALF) << 16) + low_half + shift
            word = (word & ~LOW_HALF & WORD) | (((address + 0x8000) >> 16) & LOW_HALF)
        elif kind == R_MIPS_LO16:
            word = (word & ~LOW_HALF & WORD) | ((word + shift) & LOW_HALF)
        else:
            raise RomError(f"this PSP program has a relocation of a kind this app doesn't read yet (type {kind}): "
                           "please report it, with the game's kind (not the game)")
        struct.pack_into("<I", out, at, word)


def _apply_debug_relocations(data: bytes, out: bytearray, rel: _Section, target: _Section, sections: list[_Section],
                             base: int) -> None:
    table = sections[rel.link] if rel.link < len(sections) else None
    if table is None or table.kind != SHT_SYMTAB:
        return
    for k in range(rel.size // REL_BYTES):
        offset, info = struct.unpack_from("<II", data, rel.offset + REL_BYTES * k)
        if info & 0xFF != R_MIPS_32 or offset + 4 > target.size:  # (DWARF 1 addresses are whole words)
            continue
        symbol = info >> 8
        shndx, = struct.unpack_from("<H", data, table.offset + 16 * symbol + 14)
        if 0 < shndx < min(len(sections), SHN_LORESERVE) and sections[shndx].flags & SHF_ALLOC:
            at = target.offset + offset
            struct.pack_into("<I", out, at, (struct.unpack_from("<I", data, at)[0] + base) & WORD)


def _move_symbols(data: bytes, out: bytearray, sections: list[_Section], base: int) -> None:
    """Symbols of loaded sections at their new addresses. A PRX's symbols may count from their section's start
    (Metrowerks' linker: a .data symbol at 0x6A474 in a .data at 0x6DE48), or hold module addresses: counted from
    their section when any of them lies before its section's address, which a module address can't."""
    for table in (s for s in sections if s.kind == SHT_SYMTAB):
        symbols = []
        for k in range(table.size // 16):
            at = table.offset + 16 * k
            value, = struct.unpack_from("<I", data, at + 4)
            shndx, = struct.unpack_from("<H", data, at + 14)
            if 0 < shndx < min(len(sections), SHN_LORESERVE) and sections[shndx].flags & SHF_ALLOC:
                symbols.append((at, value, sections[shndx]))
        from_section = any(value < section.addr for _, value, section in symbols)
        for at, value, section in symbols:
            struct.pack_into("<I", out, at + 4, (value + (section.addr if from_section else 0) + base) & WORD)


def _fix_headers(out: bytearray, sections: list[_Section], segments: list[list[int]], base: int) -> None:
    """The headers of a fixed program: ET_EXEC, the entry point, segments and sections at their addresses, the
    relocation segment and sections no longer loaded or applied. A .bss whose header says nothing of its size (0, as
    Metrowerks' linker leaves it) gets the rest of its segment, which the loader clears."""
    struct.pack_into("<H", out, 16, ET_EXEC)
    struct.pack_into("<I", out, 24, (struct.unpack_from("<I", out, 24)[0] + base) & WORD)
    phoff, = struct.unpack_from("<I", out, 28)
    phentsize, = struct.unpack_from("<H", out, 42)
    for i, (kind, _, vaddr, _, _, memsz, _, _) in enumerate(segments):
        at = phoff + phentsize * i
        if kind != PT_LOAD:
            struct.pack_into("<I", out, at, PT_NULL)
            continue
        struct.pack_into("<II", out, at + 8, vaddr + base, vaddr + base)  # p_vaddr; p_paddr (a PRX's module info)
        loaded = [s for s in sections if s.flags & SHF_ALLOC and vaddr <= s.addr <= vaddr + memsz]
        last = max(loaded, key=lambda s: (s.addr, s.size), default=None)
        if last is not None and last.kind == SHT_NOBITS and last.addr + last.size < vaddr + memsz:
            last.size = vaddr + memsz - last.addr
    shoff, = struct.unpack_from("<I", out, 32)
    for s in sections:
        at = shoff + SECTION_HEADER_BYTES * s.index
        if s.flags & SHF_ALLOC:
            struct.pack_into("<I", out, at + 12, s.addr + base)
            struct.pack_into("<I", out, at + 20, s.size)
        elif s.kind == SHT_PSP_REL or (s.kind == SHT_MIPS_DEBUG and s.name.startswith(".rel")):
            struct.pack_into("<I", out, at + 4, SHT_NULL)  # applied: nothing in them is left to do


def gp_value(program: bytes) -> int:
    """$gp as the module info says it (the loader sets it before the module starts), 0 when it says none."""
    info = next((s for s in _sections(program) if s.name == MODULE_INFO), None) if is_elf(program) else None
    if info is None or info.size < MODULE_INFO_GP + 4:
        return 0
    return struct.unpack_from("<I", program, info.offset + MODULE_INFO_GP)[0]


def uses_small_data(data: bytes) -> bool:
    """Whether the program keeps data $gp-relative (small data: GCC's and SNC's -G8, CodeWarrior's -sdatathreshold 8),
    by its own evidence: a $gp-relative relocation of its code or data, or (a linker that leaves them out, as the
    demo's does: the loader has nothing to do for them) code that loads, stores or takes an address through $gp. Not
    by its gp_value: every module's info has one, used or not (a PSP game with debug symbols has it in each of its 29
    modules, and not one of them reads through $gp: its code matches only with small data off). `data`: the program
    as it came, before relocate() applies its relocations and drops their tables."""
    if not is_elf(data):
        return False
    sections = _sections(data)
    for rel in sections:
        target = sections[rel.info] if rel.info < len(sections) else None
        if rel.kind in (SHT_PSP_REL, SHT_REL) and target is not None and target.flags & SHF_ALLOC and any(
                data[rel.offset + REL_BYTES * k + 4] in GP_RELATIVE for k in range(rel.size // REL_BYTES)):
            return True
    code = _code(data, sections)
    for word, in struct.iter_unpack("<I", code[:len(code) & ~3]):
        op, base, rt = word >> mips.OPCODE_SHIFT, word >> mips.RS_SHIFT & mips.REGISTER_BITS, \
            word >> mips.RT_SHIFT & mips.REGISTER_BITS
        # (start-up code that sets $gp itself, `addiu $gp, $gp, %lo(_gp)`, isn't small data)
        if base == mips.GP and rt != mips.GP and (op == mips.ADDIU or op in LOADS_STORES):
            return True
    return False


def _code(program: bytes, sections: list[_Section]) -> bytes:
    """The program's own code, its import stubs left out (the loader writes them)."""
    return b"".join(program[s.offset:s.offset + s.size] for s in sections
                    if s.flags & SHF_EXECINSTR and s.kind != SHT_NOBITS and s.name != STUB_SECTION)


def module_name(program: bytes) -> bytes:
    """The module's own name, as its module info says it (b"" when it has none): what the loader knows it by."""
    info = next((s for s in _sections(program) if s.name == MODULE_INFO), None) if is_elf(program) else None
    if info is None or info.size < MODULE_INFO_GP:
        return b""
    return program[info.offset + MODULE_INFO_NAME:info.offset + MODULE_INFO_GP].split(b"\0", 1)[0]


def extent(program: bytes) -> int:
    """Where the program's memory ends: its last loaded segment's end, its .bss included (a PRX's counts from 0)."""
    return max((vaddr + memsz for kind, _, vaddr, _, _, memsz, _, _ in _segments(program) if kind == PT_LOAD),
               default=0)


def stub_names(program: bytes) -> dict[int, str]:
    """The address of each imported function's stub in a fixed program -> its name: the game's own symbol there if
    its symbol table has one (Metrowerks' linker names each stub, beside a `.sceStub.text.<library>` section symbol),
    else the name the PSP SDK gives the library's NID (nids.py), else the library and the NID the loader links it by
    (`IoFileMgrForUser_109F50BC`)."""
    from . import nids

    if not is_elf(program):
        return {}
    by_name = {s.name: s for s in _sections(program)}
    tables = by_name.get(STUB_TABLES)
    if tables is None:
        return {}

    def at(address: int, length: int) -> bytes:
        """Up to `length` bytes of the program at a loaded address, as far as its section goes."""
        section = next((s for s in by_name.values() if s.flags & SHF_ALLOC and s.kind != SHT_NOBITS
                        and s.addr <= address < s.addr + s.size), None)
        if section is None:
            return b""
        start = section.offset + address - section.addr
        return program[start:start + min(length, section.addr + section.size - address)]

    own = _stub_symbols(program, by_name)
    names: dict[int, str] = {}
    offset = 0
    while offset + NID_TABLE + 8 <= tables.size:
        entry = program[tables.offset + offset:tables.offset + tables.size]
        words = entry[STUB_LENGTH]
        library = at(struct.unpack_from("<I", entry, LIBRARY_NAME)[0], LIBRARY_NAME_BYTES).split(b"\0", 1)[0]
        count, = struct.unpack_from("<H", entry, STUB_COUNT)
        nids_at, stubs_at = struct.unpack_from("<II", entry, NID_TABLE)
        nid_words = at(nids_at, 4 * count)
        if not words or not library or not library.isascii() or len(nid_words) != 4 * count:
            break  # not a stub table as the loader reads them: names from here on would be wrong
        for k in range(count):
            nid, = struct.unpack_from("<I", nid_words, 4 * k)
            stub = stubs_at + STUB_BYTES * k
            names[stub] = own.get(stub) or nids.name(library.decode(), nid) or f"{library.decode()}_{nid:08X}"
        offset += 4 * words
    return names


def _stub_symbols(program: bytes, by_name: dict[str, _Section]) -> dict[int, str]:
    """The game's own names for its stubs: the symbols in .sceStub.text that aren't a section's (`.sceStub.text.
    <library>`)."""
    stubs, table = by_name.get(STUB_SECTION), by_name.get(".symtab")
    if stubs is None or table is None or table.kind != SHT_SYMTAB:
        return {}
    strings = next((s for s in by_name.values() if s.index == table.link), None)
    if strings is None:
        return {}
    names = program[strings.offset:strings.offset + strings.size]
    own = {}
    for at in range(table.offset, table.offset + table.size - SYMBOL_BYTES + 1, SYMBOL_BYTES):
        name_at, value, _, info, _, shndx = struct.unpack_from("<IIIBBH", program, at)
        name = names[name_at:names.find(b"\0", name_at)].decode("latin-1")
        if shndx == stubs.index and info & SYMBOL_TYPE != STT_SECTION and name and not name.startswith("."):
            own.setdefault(value, name)
    return own


def stub_range(program: bytes) -> tuple[int, int] | None:
    """Where the imported functions' stubs are (.sceStub.text), in a fixed program."""
    if not is_elf(program):
        return None
    stubs = next((s for s in _sections(program) if s.name == STUB_SECTION and s.size), None)
    return (stubs.addr, stubs.addr + stubs.size) if stubs is not None else None


def comment(program: bytes) -> bytes:
    """The program's .comment note (which compiler built it), b"" when it has none."""
    found = next((s for s in _sections(program) if s.name == ".comment"), None) if is_elf(program) else None
    return program[found.offset:found.offset + found.size] if found is not None else b""


_GCC_NOTE = re.compile(rb"GCC: \(GNU\) [\d.]+\+allegrex-[\d.]+-psp-(\d+)\.(\d+)")
SDK_1_7 = (1, 5)  # SDK GCCs from this version on build like 1.7.1's; earlier ones like 1.3.1's


def guess_compiler(program: bytes) -> str:
    """Which compiler built the game, from its .comment note: Metrowerks' ("MW MIPS C Compiler (2.4.1.01)", a
    version decomp.me doesn't keep: every CodeWarrior build it has writes 3.0.0, so CodeWarrior 3.0.1's latest build
    is the guess and the import's compiler test tries the others), or the SDK's GCC with its version. SN Systems' SNC
    leaves no note, but its code copies registers with `or rd, rs, $zero` where GCC and CodeWarrior use `addu` (the
    demo built with SNC: 682 to 0; with GCC: 0 to 613; a CodeWarrior game: 0 to 11,153): more of those than addu
    moves is SNC's. Otherwise the SDK's later GCC; the import's test of the installed compilers decides."""
    note = comment(program)
    if b"MW MIPS C Compiler" in note:
        return "MWCCPSP"
    if (found := _GCC_NOTE.search(note)) is not None:
        return "PSPGCC-1.7" if (int(found[1]), int(found[2])) >= SDK_1_7 else "PSPGCC-1.3"
    if is_elf(program):
        from ...compilermap import quirks

        habits = quirks(_code(program, _sections(program)), False)
        if habits["or move"] > habits["addu move"]:
            return "SNC"
    return "PSPGCC-1.7"
