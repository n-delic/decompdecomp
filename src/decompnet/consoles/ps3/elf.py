"""PlayStation 3 programs: the game's main executable is a big-endian ELF64 for the PowerPC (PPU), `EBOOT.ELF` once the
user has decrypted their own `EBOOT.BIN` (a SELF, which this app never decrypts).

What the reader knows, from the file alone (a retail program is stripped; its section names are fragments):
- the program headers: the code is the lowest PT_LOAD with a payload, not the largest executable one (a game's BSS
  can carry the executable bit);
- the module descriptor at `e_entry` (retail): a table of 8-byte (function, TOC base) pairs listing the functions in
  link order, the best source of function starts. A homebrew program's `e_entry` is one function descriptor instead;
- the ABI: Sony's compact descriptors (32-bit entry and TOC, 32-bit pointers in 64-bit registers: "ilp32") or the
  usual 24-byte ELFv1 ones ("lp64": the free PPU compilers' output);
- `.comment`'s compiler stamps, when the section headers survive.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, field

from ...kits import ps3sdk
from ...rom import RomError

EM_PPC64 = 21
ET_EXEC = 2
EI_OSABI_LV2 = 0x66  # CellOS Lv-2 (a homebrew linker may leave 0)
PT_LOAD = 1
PF_X = 1
SELF_MAGIC = b"SCE\0"
ELF_HEADER_BYTES = 0x40
SHT_NOBITS = 8

# What a compiler leaves in .comment: (pattern, compiler guess). Sony's GCC 4.1.1 says "CELL" and its SDK revision.
_COMPILERS = [(rb"GCC: \(GNU\) 4\.1\.1 \(CELL", "PS3-GCC-4.1.1"), (rb"GCC: \(GNU\) 4\.", "PS3-GCC-4"),
              (rb"GCC: \((?:GNU|ps3dev)[^)]*\) \d", "PS3DEV")]


@dataclass
class Segment:
    kind: int
    flags: int
    offset: int
    vaddr: int
    filesz: int
    memsz: int


@dataclass
class Section:
    name: str
    kind: int
    addr: int
    offset: int
    size: int
    flags: int = 0  # SHF_*: EXECUTABLE is code


@dataclass
class Ps3Elf:
    entry: int
    segments: list[Segment]
    sections: dict[str, Section] = field(default_factory=dict)
    osabi: int = 0

    @property
    def loads(self) -> list[Segment]:
        return [s for s in self.segments if s.kind == PT_LOAD]

    @property
    def code(self) -> Segment | None:
        """The lowest segment with a payload: the game's code (and the read-only data after it)."""
        return min((s for s in self.loads if s.filesz), key=lambda s: s.vaddr, default=None)


def looks_like_self(data: bytes) -> bool:
    return data[:4] == SELF_MAGIC


def is_ps3_elf(data: bytes) -> bool:
    """A big-endian ELF64 executable for the 64-bit PowerPC. (A Linux PowerPC64 program would pass: nobody starts a
    decompilation from one, and the PS3's own OS/ABI byte is 0x66 where its linker sets it.)"""
    return (len(data) >= ELF_HEADER_BYTES and data[:4] == b"\x7fELF" and data[4] == 2 and data[5] == 2
            and struct.unpack_from(">H", data, 16)[0] == ET_EXEC and struct.unpack_from(">H", data, 18)[0] == EM_PPC64)


def parse(data: bytes) -> Ps3Elf:
    """The program headers and (best effort) section headers of a PS3 ELF."""
    if not is_ps3_elf(data):
        raise RomError("this isn't a PlayStation 3 program (expected a big-endian 64-bit PowerPC ELF executable)")
    entry, phoff, shoff = struct.unpack_from(">QQQ", data, 24)
    phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(">HHHHH", data, 54)
    segments = []
    if phentsize >= 56 and phoff + phentsize * phnum <= len(data):
        for i in range(phnum):
            kind, flags, off, vaddr, _, filesz, memsz, _ = struct.unpack_from(">IIQQQQQQ", data, phoff + phentsize * i)
            segments.append(Segment(kind, flags, off, vaddr, filesz, memsz))
    return Ps3Elf(entry, segments, _sections(data, shoff, shentsize, shnum, shstrndx), data[7])


def _sections(data: bytes, shoff: int, shentsize: int, shnum: int, shstrndx: int) -> dict[str, Section]:
    if not shoff or shentsize < 64 or not shnum or shoff + shentsize * shnum > len(data) or shstrndx >= shnum:
        return {}
    heads = [struct.unpack_from(">IIQQQQ", data, shoff + shentsize * i) for i in range(shnum)]
    _, _, _, _, str_off, str_size = heads[shstrndx]
    names = data[str_off:str_off + str_size]
    out = {}
    for i, (name, kind, flags, addr, off, size) in enumerate(heads):
        end = names.find(b"\0", name)
        label = names[name:end].decode("latin-1") if name < len(names) and end != -1 else ""
        # a decrypted retail program's section names are zeros (decryption restores only what its segments hold):
        # its sections are still there, by number, with their addresses, sizes and flags
        label = label if label.startswith(".") else f"#{i}"
        out.setdefault(label, Section(label, kind, addr, off, size, flags))
    return out


EXECUTABLE = 0x4  # SHF_EXECINSTR


def section_bytes(data: bytes, section: Section | None) -> bytes:
    return b"" if section is None or section.kind == SHT_NOBITS else data[section.offset:section.offset + section.size]


def read_at(data: bytes, elf: Ps3Elf, addr: int, length: int) -> bytes | None:
    """The file's bytes at a virtual address, or None outside the loaded file (BSS has none)."""
    for s in elf.loads:
        if s.vaddr <= addr and addr + length <= s.vaddr + s.filesz:
            at = s.offset + addr - s.vaddr
            return data[at:at + length]
    return None


@dataclass
class Descriptor:
    """What `e_entry` points at: Sony's table of (function, TOC) pairs, or one ELFv1 function descriptor."""
    abi: str  # "ilp32" (compact 8-byte pairs) or "lp64" (24-byte descriptors)
    toc: int
    functions: list[int]
    tocs: list[int]  # each function's TOC base, in the same order


def module_descriptor(data: bytes, elf: Ps3Elf) -> Descriptor | None:
    """The table at `e_entry`, or None when the entry point holds no descriptor this reader knows. A compact pair is a
    code address then a TOC base, both 32 bits (the first is never zero); an ELFv1 descriptor's 64-bit entry has a zero
    high half. shortcut: the table ends at the first pair outside the code segment, which is how a retail one's
    zero terminator reads too; a pair of the same TOC base in a different segment would end it early."""
    code = elf.code
    head = read_at(data, elf, elf.entry, 16)
    if code is None or head is None:
        return None
    first, second = struct.unpack(">II", head[:8])
    if first == 0:  # ELFv1: 8-byte entry, 8-byte TOC, 8-byte environment
        entry, toc = struct.unpack(">QQ", head)
        if code.vaddr <= entry < code.vaddr + code.filesz:
            return Descriptor("lp64", toc, [entry], [toc])
        return None
    lo, hi = code.vaddr, code.vaddr + code.filesz
    functions, tocs = [], []
    at = elf.entry
    while (pair := read_at(data, elf, at, 8)) is not None:
        func, toc = struct.unpack(">II", pair)
        if not lo <= func < hi or func % 4 or not toc:
            break
        functions.append(func)
        tocs.append(toc)
        at += 8
    if not functions:
        return None
    return Descriptor("ilp32", tocs[0], functions, tocs)


PROCESS_PARAM, PROCESS_PARAM_MAGIC = 0x60000001, 0x13BCC5F6  # sys_process_param: its program header and magic


def sdk_version(data: bytes, elf: Ps3Elf) -> str:
    """The SDK the program was built with, as its process parameters record it ("2.5.0" from 0x00250001), or "".
    A decrypted retail program's .comment (and its section names) are zeros: only its segments survive decryption,
    and the process parameters are one."""
    seg = next((s for s in elf.segments if s.kind == PROCESS_PARAM and s.filesz >= 16), None)
    if seg is None or struct.unpack_from(">I", data, seg.offset + 4)[0] != PROCESS_PARAM_MAGIC:
        return ""
    v = struct.unpack_from(">I", data, seg.offset + 12)[0]
    return f"{v >> 20 & 0xF}.{v >> 16 & 0xF}.{v >> 12 & 0xF}" if v else ""


MINIMAL_TOC_SAVE, MINIMAL_TOC_LOAD = 0xFBC10000, 0x83C20000  # std r30,N(r1) / lwz r30,N(r2): -mminimal-toc's
MINIMAL_TOC_FUNCTIONS = 8  # this many functions that set it up: the game was built with -mminimal-toc


def minimal_toc(data: bytes, elf: Ps3Elf) -> bool:
    """Whether the program was built with GCC's -mminimal-toc: its functions keep each file's own table of addresses in
    r30, saving r30 (`std r30,N(r1)`) and loading it from the TOC (`lwz r30,N(r2)`) before reaching a global."""
    code = elf.code
    words = struct.unpack_from(f">{code.filesz // 4}I", data, code.offset)
    seen = 0
    for k in range(len(words) - 1):
        if words[k] & 0xFFFF0000 == MINIMAL_TOC_SAVE and words[k + 1] & 0xFFFF0000 == MINIMAL_TOC_LOAD:
            seen += 1
            if seen >= MINIMAL_TOC_FUNCTIONS:
                return True
    return False


def guess_compiler(comment: bytes) -> str:
    return next((guess for pattern, guess in _COMPILERS if re.search(pattern, comment)), "")


def read_elf(data: bytes, name: str = "") -> tuple[bytes, dict]:
    """(the program, the facts its header and notes give), or a RomError saying what to do with a file that isn't
    one: a retail SELF is the user's own copy to decrypt first, with their own tools."""
    if looks_like_self(data):
        raise RomError("this is a PlayStation 3 SELF (the EBOOT.BIN on the disc is encrypted). decompdecomp never "
                       "decrypts games: decrypt your own copy first with your own tools and keys (RPCS3 can do it "
                       "for a game you installed), then pick the resulting EBOOT.ELF")
    elf = parse(data)
    if elf.code is None:
        raise RomError("this PlayStation 3 ELF has no loadable code")
    descriptor = module_descriptor(data, elf)
    comment = section_bytes(data, elf.sections.get(".comment"))
    sdk = sdk_version(data, elf)
    stem = re.sub(r"\.(elf|self|bin)$", "", name.split("/")[-1].split("\\")[-1], flags=re.I)
    title = stem if stem and stem.lower() not in ("eboot", "game", "main") else "PlayStation 3 game"
    return data, {
        "title": title, "game_code": "", "region": "Unknown region", "version": 0, "entrypoint": elf.entry, "gp": 0,
        "crc1": 0, "crc2": 0, "size": len(data), "sha1": hashlib.sha1(data).hexdigest(), "byte_order": "elf",
        # (what the import says it looks built with: the compiler's stamp, else the SDK its parameters record)
        "compiler_guess": guess_compiler(comment) or (f"SDK {sdk}" if sdk else ""),
        "platform": "ps3", "small_data": False,
        # the compiler's stamp ("SDK270"), else the SDK the process parameters record, written as the stamp is
        "sdk": ps3sdk.stamp(comment) or (f"SDK{sdk.replace('.', '')}" if sdk else ""),
        "sdk_version": sdk, "minimal_toc": minimal_toc(data, elf),
        "osabi": elf.osabi, "abi": descriptor.abi if descriptor else "ilp32",
        "toc": descriptor.toc if descriptor else 0, "descriptors": len(descriptor.functions) if descriptor else 0,
        "symbols": ".symtab" in elf.sections,
    }
