"""Start a PC (Windows) decompilation from scratch: a project made for the user's own copy of a 32-bit x86 program
(.exe), built with the game's Visual C++ and judged by objdiff, worked on like any community project.

There's no community toolkit for this as there is for GameCube (decomp-toolkit) or DS (ds-decomp), so decompdecomp
does the delinking itself:

- Ghidra's analysis finds the functions (and reads the program's imports and exports);
- each function's extent is its code plus what MSVC puts after it (jump tables, byte index tables) and the nop
  padding Visual C++ 6 ends functions with; code Ghidra didn't see in between becomes functions of its own;
- capstone reads every instruction for the addresses it holds: calls and jumps to other functions, and data
  addresses, become relocations (functions by their decorated names, `_func_00401000`, `_func_00401020@8` for
  __stdcall, `@func_00401040@8` for __fastcall; data as `_D_00402034`; imports as `__imp__Name@4`), so each function
  matches whatever names the C uses, under objdiff's usual rules;
- the exception-handling stubs Visual C++ makes (its `.text$x` code, at the end of the program's code) are set aside:
  functions.json says why (`generated`), and no C of their own makes them (exception_stubs);
- the pieces Ghidra cut off functions (a lone epilogue, a catch block) are joined back to them (merge_pieces), never
  a stub; check_code puts the program's code back together from the delinked functions, the rebuild check.

The project (tools/configure.py, tools/delink.py) cuts the functions out of the user's copy into COFF objects at
build time, one per source file (src/auto/text_<address>.cpp, about 4 KiB of functions each), and compiles src/ with
MSVC through wibo. A program built without relocations (most games before 2005) has every address fixed, which is
what this reads; there's no relink of the whole .exe yet, so files aren't linked into the game as on GameCube.
The game's own DLLs are analysed the same way, each a program of the project (find_modules).
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import capstone
from capstone import x86

from decompnet.hostenv import NO_WINDOW

from ... import toolchains
from ...arch import x86 as x86_arch
from ...forks.project import ForkError, ForkProject

UNIT_BYTES = 0x1000
# the linker aligns code to 16 bytes (a function's section, from Visual C++ 2005 on each function's), filling the gap
# with int3 (zeros in a program another linker rewrote); when such a gap follows a third of the functions or more, it's
# the linker's padding before every function, not an object's end: a 2005 game has one after 88% of its functions, a
# 2010 one after 60%, a Visual C++ 6.0 game after 10% (where its compiler's nops pad the rest: analyse's PAD)
FUNCTION_ALIGN, PADDING_BYTES, ALIGNED_SHARE = 16, (0xCC, 0x00, 0x90), 3
TEMPLATE = Path(__file__).with_name("project_template")
MSVC_DEFAULT = "6.0"
DIR32, REL32 = 0x06, 0x14
PAD = 0x90  # what Visual C++ 6 pads a function's section with (the linker pads between objects with 0xCC or zeros)
# Which Visual C++ built each part of the program, by the product ids its Rich header gives: every version numbers its
# tools anew, so a compiler's id says its version. From the public lists of Rich header tools: richprint's comp_id.txt
# (github.com/dishather/richprint) and kirschju/richheader's prodids.py. (version, the ids of its C and C++
# compilers, and of its whole-program objects: /GL's link-time code generation (LTCG) and profile-guided ones)
RICH_COMPILERS = (
    ("6.0", (0x0A, 0x0B, *range(0x15, 0x19), 0x23, 0x24, *range(0x30, 0x36)), ()),
    ("7.0", (0x1C, 0x1D, 0x39, 0x3A), (0x2B, 0x2C, *range(0x41, 0x45))),
    ("7.1", (*range(0x4C, 0x50), *range(0x5F, 0x63)), (*range(0x50, 0x56), *range(0x63, 0x69))),
    ("8.0", range(0x6D, 0x71), (*range(0x71, 0x77), 0x82)),
    ("9.0", range(0x83, 0x87), range(0x89, 0x90)),
    ("10.0", (0xAA, 0xAB, 0xBC, 0xBD), (*range(0xAE, 0xB5), *range(0xC0, 0xC7))),
    ("11.0", (0xCE, 0xCF), range(0xD2, 0xD9)),
    ("12.0", (0xE0, 0xE1, 0xF2, 0xF3), (*range(0xE4, 0xEB), *range(0xF6, 0xFD))),
    ("14", (0x104, 0x105), range(0x108, 0x10F)),  # 2015 and every version since: the build says which
)
# Visual C++ 2015 to 2026 share their tools' ids: the first build of each (comp_id.txt)
VC14_BUILDS = ((35503, "14.5"), (30401, "14.3"), (27508, "14.2"), (25017, "14.1"), (0, "14.0"))
VISUAL_STUDIO = {"6.0": "6.0", "7.0": ".NET 2002", "7.1": ".NET 2003", "8.0": "2005", "9.0": "2008", "10.0": "2010",
                 "11.0": "2012", "12.0": "2013", "14.0": "2015", "14.1": "2017", "14.2": "2019", "14.3": "2022",
                 "14.4": "2022", "14.5": "2026"}
# the release each common build is (comp_id.txt), so the import says "2008 SP1": each update generates other code
RELEASES = {("6.0", 8447): "SP3", ("6.0", 8799): "SP4", ("6.0", 8966): "SP5", ("6.0", 9044): "SP5 Processor Pack",
            ("6.0", 9782): "SP6", ("7.1", 6030): "SP1", ("9.0", 30729): "SP1", ("10.0", 40219): "SP1",
            ("11.0", 51106): "Update 1", ("11.0", 60315): "Update 2", ("11.0", 60610): "Update 3",
            ("11.0", 61030): "Update 4", ("12.0", 30501): "Update 2", ("12.0", 30723): "Update 3",
            ("12.0", 31101): "Update 4", ("12.0", 40629): "Update 5", ("14.0", 23506): "Update 1",
            ("14.0", 23918): "Update 2", ("14.0", 24210): "Update 3", ("14.0", 24215): "Update 3.1"}
# The compiler packs' Visual C++ (consoles/pc MSVC_VERSIONS) by the version and build the Rich header names: each update
# generates other code, so a game builds with its own build's. 2005's RTM and SP1 are both build 50727 (the Rich
# header keeps no more of the version), so an import takes SP1; RTM ("8.0") is chosen by file, with try_compiler. From each compiler's own banner (cl.exe)
RICH_BUILDS = {("6.0", 8168): "6.0", ("7.0", 9466): "7.0", ("7.1", 3077): "7.1", ("8.0", 50727): "8.0SP1",
               ("9.0", 21022): "9.0", ("9.0", 30729): "9.0SP1", ("10.0", 30319): "10.0", ("10.0", 40219): "10.0SP1"}
MSVC_PACKS = ("msvc", "msvc2005")  # toolchains.toml: 6.0-7.1, 2005-2010; each version in a folder of its pack
LAST_IN_SCOPE = (14, 0)  # Visual C++ 2015: games built with 2017 and newer are out of decompdecomp's scope for now
LINKER_VERSION_AT = 2  # in the optional header: the linker's major and minor version, a byte each
# DRM and packers, by the sections they add to a program, as Detect It Easy's rules find them (github.com/horsicq/
# Detect-It-Easy, db/PE) and Steamless finds Steam's (github.com/atom0s/Steamless: a ".bind" section)
WRAPPER_SECTIONS = {".bind": "SteamStub (Steam's DRM)", ".securom": "SecuROM", ".dsstext": "SecuROM",
                    ".cms_t": "SecuROM", ".cms_d": "SecuROM", "stxt774": "SafeDisc", "stxt371": "SafeDisc",
                    ".sforce3": "StarForce", ".themida": "Themida", ".winlice": "WinLicense", ".vmp0": "VMProtect",
                    ".vmp1": "VMProtect", ".enigma1": "Enigma Protector", ".enigma2": "Enigma Protector"}
PACKER_SECTIONS = {"UPX0": "UPX", "UPX1": "UPX"}
# Denuvo's anti-tamper names no section of its own: Detect It Easy knows it by many sections with names like these
# (and more evidence). Its ".edata" is left out: that's where any program's exports are (a 2002 game has one), so
# two such names are asked for instead of one
DENUVO_SECTION = re.compile(r"\.[ex](text|code)|\.(u|my|s[xr]|x[pd])data|\.arch|\.xtls")
DENUVO_MIN_SECTIONS, DENUVO_MIN_NAMES = 11, 2
# the Rich header's linkers (comp_id.txt): the one that linked the program says which build compiled the game's own
# code; libraries come prebuilt, often by another build (a 2019 game's middleware, built with 2015)
RICH_LINKERS = {0x04, 0x13, 0x1E, 0x20, 0x25, 0x28, 0x3C, 0x3D, 0x47, 0x56, 0x5A, 0x78, 0x91, 0x9D, 0xBA, 0xCC, 0xDE,
                0xF0, 0x102}
MAIN_BUILD_SHARE = 10  # without the linker's build: the newest build that compiled a tenth of the files or more
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Section:
    name: str
    va: int  # absolute address
    vsize: int
    offset: int  # in the file
    size: int  # bytes in the file
    code: bool


@dataclass
class PE:
    data: bytes
    image_base: int
    image_size: int
    entry: int
    timestamp: int
    sections: list[Section]
    imports: dict[int, str] = field(default_factory=dict)  # import address table slot -> "Sleep"

    def section_at(self, addr: int) -> Section | None:
        return next((s for s in self.sections if s.va <= addr < s.va + max(s.vsize, s.size)), None)

    def offset(self, addr: int) -> int | None:
        s = self.section_at(addr)
        return None if s is None or addr - s.va >= s.size else s.offset + addr - s.va

    def read(self, addr: int, n: int) -> bytes:
        off = self.offset(addr)
        return b"" if off is None else self.data[off:off + n]

    def contains(self, addr: int) -> bool:
        return self.image_base <= addr < self.image_base + self.image_size


# a Windows program: the DOS header ("MZ") says where the PE header is; after it the COFF header, then the optional
# header (PE32's: a 32-bit program), then the section table
DOS_HEADER_BYTES, PE_OFFSET_AT = 0x40, 0x3C
PE_HEADER_BYTES = 0x18  # "PE\0\0" and the COFF header
MACHINE_AT, SECTION_COUNT_AT, TIMESTAMP_AT, OPTIONAL_HEADER_SIZE_AT = 4, 6, 8, 20  # in the COFF header, from "PE\0\0"
OPTIONAL_HEADER_AT = PE_HEADER_BYTES
MACHINE_I386, PE32_MAGIC = 0x14C, 0x10B
X86 = "32-bit x86"
MACHINES = {MACHINE_I386: X86, 0x8664: "64-bit (x64)", 0xAA64: "64-bit ARM", 0x1C0: "ARM", 0x1C4: "ARM",
            0x200: "Itanium"}
ENTRY_AT, IMAGE_BASE_AT, IMAGE_SIZE_AT, IMPORTS_AT = 16, 28, 56, 104  # in the optional header
SECTION_HEADER_BYTES, SECTION_FLAGS_AT = 40, 36
CODE_AND_EXECUTABLE = 0x20000020  # IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE
PE_PROBE_BYTES, PE_HEADERS_BYTES = 0x1000, 0x200  # is_pe reads this much of a file, or the PE header and this much more
# the import directory: 20-byte descriptors, each with a table of 4-byte entries (a name's address, or an ordinal)
IMPORT_DESCRIPTOR_BYTES = 20
MAX_IMPORT_DLLS, MAX_IMPORTS_PER_DLL = 4096, 65536  # a broken table stops here
BY_ORDINAL, ORDINAL_BITS = 0x80000000, 0xFFFF
DLL_NAME_BYTES, IMPORT_NAME_BYTES = 64, 256
IMPORT_HINT_BYTES = 2  # an import's name follows its 2-byte hint
# the Rich header: the tools that built the program, XORed with a key, between the DOS stub and "Rich"
RICH_START = 0x80
RICH_ENTRIES_AT, RICH_ENTRY_BYTES = 16, 8  # after "DanS" and its padding: (product << 16 | build, count)
# read_data
READ_DEFAULT_BYTES, READ_MIN_BYTES, READ_MAX_BYTES = 64, 4, 512
HEX_ROW_BYTES = 16
POINTERS_SHOWN, FLOATS_SHOWN = 24, 8
PLAUSIBLE_FLOAT = (1e-6, 1e7)  # magnitudes a float in game data has; other words aren't floats
MIN_STRING_CHARS = 3
PRINTABLE = range(32, 127)
# exception_stubs: C++ built with /GX (as games of the time were) puts the code that destroys a function's local objects
# when an exception passes through it, and the function's handler, in a section of its own, .text$x, which the linker
# puts after all of the program's code. Ghidra takes each stub for a function, the smallest of the project, so agents
# were handed them first, though compiling the function they belong to is what makes them (a 2000s Visual C++ 6.0
# game: 4,677 of its 11,877 functions)
EH_STUB_WHY = ("exception-handling code Visual C++ makes (its .text$x): it comes out of compiling the function it "
               "belongs to, so no C of its own makes it")
EH_STUB_GAP = 4  # functions without the shape the run of stubs may hold in a row (Ghidra merged or cut a few)
# a run of stubs that code follows (Visual C++ 2005 on: the linker puts the dynamic initializers' .text$yc and
# .text$yd after .text$x) has at least this many, so a few pieces cut off near the end of the main body never pass
EH_STUB_FOLLOWED_RUN = 64
EH_STUB_BYTES = 32  # how much of a function's start its shape is read from (an array funclet's pushes and lea)
TOOL_SECONDS = 3600  # Ghidra on a big game, the project's build
ERROR_TAIL = 1500  # how much of a tool's output an error shows


def _pe_header(head: bytes) -> int | None:
    if len(head) < DOS_HEADER_BYTES or head[:2] != b"MZ":
        return None
    at = struct.unpack_from("<I", head, PE_OFFSET_AT)[0]
    if at + PE_HEADER_BYTES > len(head) or head[at:at + 4] != b"PE\0\0":
        return None
    return at


def is_pe(head: bytes) -> bool:
    """A 32-bit x86 Windows program (the first 4 KiB of the file are enough)."""
    at = _pe_header(head)
    return at is not None and struct.unpack_from("<H", head, at + MACHINE_AT)[0] == MACHINE_I386 and \
        struct.unpack_from("<H", head, at + OPTIONAL_HEADER_AT)[0] == PE32_MAGIC


def read_pe(data: bytes) -> PE:
    at = _pe_header(data)
    if at is None or not is_pe(data[:max(PE_PROBE_BYTES, at + PE_HEADERS_BYTES)]):
        raise ForkError("this isn't a 32-bit Windows program (.exe): PC games start from their x86 .exe")
    count, _, _, _, opt_size = struct.unpack_from("<HIIIH", data, at + SECTION_COUNT_AT)
    timestamp = struct.unpack_from("<I", data, at + TIMESTAMP_AT)[0]
    opt = at + OPTIONAL_HEADER_AT
    entry_rva, base, image_size = (struct.unpack_from("<I", data, opt + field)[0]
                                   for field in (ENTRY_AT, IMAGE_BASE_AT, IMAGE_SIZE_AT))
    sections = []
    for number in range(count):
        header = opt + opt_size + SECTION_HEADER_BYTES * number
        name, vsize, va, size, offset = struct.unpack_from("<8sIIII", data, header)
        flags = struct.unpack_from("<I", data, header + SECTION_FLAGS_AT)[0]
        sections.append(Section(name.rstrip(b"\0").decode("latin-1"), base + va, vsize, offset,
                                min(size, max(0, len(data) - offset)), bool(flags & CODE_AND_EXECUTABLE)))
    pe = PE(data, base, image_size, base + entry_rva, timestamp, sections)
    pe.imports = _imports(pe, opt)
    return pe


def _imports(pe: PE, opt: int) -> dict[int, str]:
    """Import address table slot -> the function's name (or DLL ordinal), from the import directory."""
    rva = struct.unpack_from("<I", pe.data, opt + IMPORTS_AT)[0]
    out: dict[int, str] = {}
    if not rva:
        return out
    at = pe.image_base + rva
    for _ in range(MAX_IMPORT_DLLS):
        desc = pe.read(at, IMPORT_DESCRIPTOR_BYTES)
        if len(desc) < IMPORT_DESCRIPTOR_BYTES or desc == bytes(IMPORT_DESCRIPTOR_BYTES):
            break
        lookup, _, _, name_rva, iat = struct.unpack("<IIIII", desc)
        dll = pe.read(pe.image_base + name_rva, DLL_NAME_BYTES).split(b"\0", 1)[0].decode("latin-1")
        lookup = lookup or iat
        for k in range(MAX_IMPORTS_PER_DLL):
            entry = pe.read(pe.image_base + lookup + 4 * k, 4)
            if len(entry) < 4 or not struct.unpack("<I", entry)[0]:
                break
            value = struct.unpack("<I", entry)[0]
            if value & BY_ORDINAL:
                name = f"{Path(dll).stem}_{value & ORDINAL_BITS}"
            else:
                address = pe.image_base + value + IMPORT_HINT_BYTES
                name = pe.read(address, IMPORT_NAME_BYTES).split(b"\0", 1)[0].decode("latin-1")
            out[pe.image_base + iat + 4 * k] = name if _IDENT.fullmatch(name) else f"{Path(dll).stem}_{k}"
        at += IMPORT_DESCRIPTOR_BYTES
    return out


def rich_builds(data: bytes) -> list[tuple[int, int, int]]:
    """(product id, build number, count) of every tool the Rich header says built a part of the program."""
    at = _pe_header(data)
    end = data.find(b"Rich", RICH_START, at)
    if end < 0:
        return []
    key = data[end + 4:end + 8]
    # "DanS", XORed with the key, starts it: usually at 0x80, after the DOS stub, but a longer stub moves it
    start = data.rfind(bytes(a ^ b for a, b in zip(b"DanS", key)), 0, end)
    if start < 0:
        return []
    plain = bytes(byte ^ key[i % 4] for i, byte in enumerate(data[start:end]))
    out = []
    for entry in range(RICH_ENTRIES_AT, len(plain) - RICH_ENTRY_BYTES + 1, RICH_ENTRY_BYTES):
        comp, count = struct.unpack_from("<II", plain, entry)
        out.append((comp >> 16, comp & 0xFFFF, count))
    return out


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@dataclass
class Build:
    """What a Windows program says about how it was built (describe)."""
    machine: str  # X86, "64-bit (x64)"...
    version: str | None  # the Visual C++ that compiled the game's own code, "9.0"; None: the program doesn't say
    build: int | None  # that compiler's build number (the Rich header's); None: only the linker's version said
    whole_program: int = 0  # compiled files built for link-time code generation (/GL)
    compiled: int = 0  # every compiled file the Rich header counts
    wrapper: str | None = None  # the DRM or packer it's recognised by

    @property
    def compiler(self) -> str:
        """Its name, release and build: "Visual C++ 2008 SP1 (9.0, build 30729)"."""
        if self.version is None:
            return "a compiler it doesn't name"
        year = VISUAL_STUDIO[self.version]
        release = RELEASES.get((self.version, self.build))
        detail = [self.version] if year != self.version else []
        detail.append(f"build {self.build}" if self.build else "by its linker's version")
        return f"Visual C++ {year}{' ' + release if release else ''} ({', '.join(detail)})"

    @property
    def packed(self) -> bool:
        return self.wrapper in PACKER_SECTIONS.values()

    @property
    def out_of_scope(self) -> bool:
        return self.version is not None and _version_key(self.version) > LAST_IN_SCOPE

    @property
    def newer_than_pack(self) -> bool:
        return self.version is not None and \
            _version_key(self.version) > max(_version_key(version) for version, _ in RICH_BUILDS)

    @property
    def pack_version(self) -> str:
        """The compiler packs' Visual C++ to build with: the program's own build, or the closest build of its version
        (the newest, when only its linker's version names it), or the closest the packs have."""
        same = [(build, name) for (version, build), name in RICH_BUILDS.items() if version == self.version]
        if same:
            return min(same, key=lambda b: abs(b[0] - self.build) if self.build else -b[0])[1]
        return _pack()[-1] if self.newer_than_pack else MSVC_DEFAULT

    @property
    def exact(self) -> bool:
        """The packs have the very build that compiled it."""
        return (self.version, self.build) in RICH_BUILDS

    def summary(self) -> str:
        """What the program is, in one line, for the import's log."""
        made = (f"built with {self.compiler}" if self.version else
                "its Rich header and linker don't say which Visual C++ built it")
        glued = (f"{self.whole_program} of its {self.compiled} compiled files with whole-program optimisation (/GL)"
                 if self.whole_program else "no whole-program optimisation")
        return f"{self.machine}, {made}, {glued}, {f'wrapped in {self.wrapper}' if self.wrapper else 'no DRM or packer found'}"

    def blockers(self) -> list[str]:
        """Why decompdecomp can't start a project for this program, each with what it means: none, it can."""
        out = []
        if self.machine != X86:
            out.append(f"it's a {self.machine} program, and decompdecomp starts PC games from 32-bit x86 ones for now "
                       "(64-bit games come after Visual C++ 2012 to 2015: ROADMAP.md, Newer Windows games)")
        if self.packed:
            out.append(f"it's compressed with {self.wrapper}, so its code only appears when it runs, and decompdecomp "
                       "reads a program as it is on disk")
        elif self.wrapper:
            out.append(f"it's wrapped in {self.wrapper}, DRM that encrypts or hides its code: decompdecomp never removes "
                       "or gets around protection, so it works only on programs without it, such as a DRM-free release")
        if self.out_of_scope:
            out.append(f"it was built with {self.compiler}, and decompdecomp's PC games stop at Visual C++ 2015: games "
                       "built with 2017 and newer are out of its scope for now")
        # a game that is mostly LTCG is refused rather than left to agents for weeks (most of it can't match); a few
        # such files (a library built with /GL) only cost their own functions, which cannot_match says
        if self.whole_program * 2 > self.compiled > 0:
            out.append(f"{self.whole_program} of its {self.compiled} compiled files were built with whole-program "
                       "optimisation (/GL and /LTCG): the linker made their code for the whole program at once, "
                       "inlining across files, so no file's code can be matched on its own")
        return out

    def cannot_match(self) -> list[str]:
        """Why a program decompdecomp does start can't match (all of it, or some): none, it can."""
        out = []
        if self.newer_than_pack:
            out.append(f"it was built with {self.compiler}, and decompdecomp has Visual C++ 6.0 to 2010 so far: "
                       "2012 to 2015 come next (ROADMAP.md, Newer Windows games), so nothing matches until then")
        if self.whole_program:
            out.append(f"{self.whole_program} of its {self.compiled} compiled files were built with whole-program "
                       "optimisation (/GL): their functions can't match")
        return out


def _pack() -> list[str]:
    from . import MSVC_VERSIONS

    return [v.removeprefix("MSVC/") for v in MSVC_VERSIONS]


def is_windows_program(head: bytes) -> bool:
    """Any Windows program: the console takes them all, so one it can't start is told exactly why (Build.blockers)."""
    return _pe_header(head) is not None


def describe(data: bytes) -> Build:
    """What the program's headers say about how it was built: its processor, the Visual C++ that compiled most of it
    (the Rich header's compilers, counted by file; without one, the linker's version), how much of it was built for
    link-time code generation, and the DRM or packer its sections show."""
    at = _pe_header(data)
    if at is None:
        raise ForkError("this isn't a Windows program (.exe): PC games start from their .exe")
    machine_id, count = struct.unpack_from("<HH", data, at + MACHINE_AT)
    opt_size = struct.unpack_from("<H", data, at + OPTIONAL_HEADER_SIZE_AT)[0]
    files: dict[tuple[str, int], int] = {}
    whole = 0
    rich = rich_builds(data)
    for product, number, n in rich:
        for version, compilers, glued in RICH_COMPILERS:
            if product in compilers or product in glued:
                if version == "14":
                    version = next(v for first, v in VC14_BUILDS if number >= first)
                files[version, number] = files.get((version, number), 0) + n
                whole += n if product in glued else 0
    linked = [k for k in files if k[1] in {number for product, number, _ in rich if product in RICH_LINKERS}]
    if linked:
        version, number = max(linked, key=files.get)
    elif files:
        by_version: dict[str, int] = {}
        for (version, _), n in files.items():
            by_version[version] = by_version.get(version, 0) + n
        version = max(by_version, key=by_version.get)
        number = max(k[1] for k in files if k[0] == version and files[k] * MAIN_BUILD_SHARE >= by_version[version])
    else:  # no Rich header (stripped, or a store's signature in its place): the linker's version names the toolset
        major, minor = data[at + OPTIONAL_HEADER_AT + LINKER_VERSION_AT:at + OPTIONAL_HEADER_AT + LINKER_VERSION_AT + 2]
        version = {6: "6.0", 7: "7.1" if minor >= 10 else "7.0"}.get(major, f"{major}.{minor // 10 if major == 14 else 0}")
        version, number = (version if version in VISUAL_STUDIO else None), None
    first = at + OPTIONAL_HEADER_AT + opt_size
    names = [data[first + SECTION_HEADER_BYTES * i:first + SECTION_HEADER_BYTES * i + 8].rstrip(b"\0").decode("latin-1")
             for i in range(count)]
    known = {**WRAPPER_SECTIONS, **PACKER_SECTIONS}
    wrapper = next((known[name] for name in names if name in known), None)
    if wrapper is None and len(names) >= DENUVO_MIN_SECTIONS and \
            len({n for n in names if DENUVO_SECTION.fullmatch(n)}) >= DENUVO_MIN_NAMES:
        wrapper = "Denuvo"
    return Build(MACHINES.get(machine_id, f"machine type {machine_id:#x}"), version, number, whole,
                 sum(files.values()), wrapper)


def game_info(path: Path) -> dict:
    """The game's facts, after its headers say decompdecomp can start it (a ForkError says why not)."""
    data = path.read_bytes()
    built = describe(data)
    if why := built.blockers():
        raise ForkError(f"decompdecomp can't start this program: {'; and '.join(why)}.")
    pe = read_pe(data)
    stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower()) or "game"
    version = built.pack_version
    if built.version is None:
        why = "the program doesn't say which Visual C++ built it; 6.0 built most games of its time"
    elif built.exact:
        why = f"it was built with {built.compiler}"
    else:  # another update of its version (the packs have the common ones), or a version they don't have yet
        why = f"it was built with {built.compiler}; the closest the compiler packs have is"
    return {"platform": "pc", "id": stem, "title": path.stem, "region": "PC", "sha1": hashlib.sha1(data).hexdigest(),
            "timestamp": pe.timestamp, "compiler": version, "compiler_why": why, "summary": built.summary(),
            "cannot_match": built.cannot_match()}


# ---------------------------------------------------------------------------------------------------- the game's DLLs
# Many games keep much of their code in DLLs of their own beside the .exe (#12: a 2010 racing game's .exe is small;
# an engine and its game logic are two DLLs). Each is a program of the project: its source files in a folder of its
# name, its functions delinked from it. DLLs are relocatable, and many prefer the same address (0x10000000, the
# linker's default): the first keeps its own, a later one that would overlap the program or a DLL before it is moved
# by its base relocations to the next free 64 KiB, as Windows' loader moves it. So no two functions or data share an
# address, and func_<address> and D_<address> stay names no two programs have.
DLL_GRANULARITY = 0x10000  # where Windows puts a DLL it moves: its allocation granularity
MODULE_DEPTH = 2  # folders below the game's a DLL is looked for in (a game's own: <game>/<mod>/dlls/)
EXPORTS_AT, BASE_RELOCATIONS_AT = 96, 136  # the export table's and the base relocations' directories (optional header)
CHARACTERISTICS_AT, RELOCS_STRIPPED = 18, 0x0001  # the COFF header's flags; linked /FIXED: it can't move
EXPORT_DIRECTORY_BYTES, MAX_EXPORTS = 40, 65536
MODULE_YEAR = 365 * 24 * 3600
PLAUSIBLE_TIMESTAMPS = (788918400, 2208988800)  # 1995 to 2040: a build's time (reproducible builds hash it instead)
RUNTIME, STAND_IN, MIDDLEWARE, PROTECTION, PLUGIN = "runtime", "stand-in", "middleware", "protection", "plugin"
LATER, NO_ROOM, NOT_PROGRAM = "later", "no room", "not a program"
# what's not the game's own, by the DLL's name: what Windows and Visual C++ install, what a wrapper or a fix replaces
# (in a game's folder a ddraw.dll is dgVoodoo's or a widescreen fix's: the real one is Windows'), licensed middleware
# (sound, video, physics, a store's SDK) and copy protection
OTHERS_DLLS = (
    (RUNTIME, re.compile(r"msvc[rp]\w*|msvcirt|mfc\d+\w*|vcruntime\w*|ucrtbased?|api_ms_win_\w*|concrt\w*|vcomp\w*"
                         r"|atl\d+|msvbvm\w*|unicows|vcamp\w*|vccorlib\w*")),
    (STAND_IN, re.compile(r"d3d\w*|ddraw|dinput8?|dsound|dxgi|xinput\w*|x3daudio\w*|xaudio\w*|winmm|version|dbghelp"
                          r"|opengl32|glu32|wsock32|ws2_32|msacm32|dplayx|dsetup\w*|xlive|gdiplus|comctl32|wininet"
                          r"|winhttp|kernel32|user32|gdi32|advapi32|ole32|oleaut32|shell32|msvfw32|avifil32")),
    (MIDDLEWARE, re.compile(r"binkw32|bink2w32|binkawin|smackw32|mss\w*|mp3dec|fmod\w*|bass\w*|eax\w*|ogg|vorbis\w*"
                            r"|libogg\w*|libvorbis\w*|theora\w*|openal32|wrap_oal|soft_oal|physx\w*|nvcuda|cudart\w*"
                            r"|cg|cggl|cgd3d\w*|steam_api\w*|steamclient\w*|galaxy\w*|discord\w*|sdl\d*(_\w+)?"
                            r"|libcef|chromehtml|icu\w*|avcodec\w*|avformat\w*|avutil\w*|swscale\w*|swresample\w*"
                            r"|zlib\w*|libpng\w*|jpeg\w*|ijl\d+|lua\d*\w*|python\d+\w*|granny2|iconv|xvid\w*|divx\w*"
                            r"|a3dapi|nvapi\w*|gfsdk\w*|amd_ags\w*|crashrpt\w*|bugsplat\w*|havok\w*|speedtree\w*"
                            r"|scaleform\w*|awesomium\w*|gameux\w*|wmvcore|quartz")),
    (PROTECTION, re.compile(r"drvmgt|dvm|secdrv|pbsv|pbcl|pbag|cdac\w*|starforce\w*|protect\w*")),
)
LEFT_OUT = {
    RUNTIME: "Microsoft's C and C++ runtime or Windows' own: they come with Visual C++ and Windows, not with the game",
    STAND_IN: "named as Windows' own (DirectX, input, sound): in a game's folder, a wrapper or a fix someone added, as "
              "the real one comes with Windows",
    MIDDLEWARE: "middleware the game licensed (sound, video, physics, a store's SDK), not the game's own code",
    PROTECTION: "copy protection's: decompdecomp never works on protection",
    PLUGIN: "ASI plugins: mods an ASI loader puts into the game (or a sound library's codecs), not the game's own code",
    LATER: "built a year or more after the program: a mod's, a fix's or a launcher's, not the game's own",
    NO_ROOM: "their addresses are the program's or another DLL's, and without base relocations they can't move",
    NOT_PROGRAM: "not 32-bit x86 Windows programs decompdecomp reads",
}


@dataclass
class Module:
    """One of the game's own DLLs, as the project has it."""
    path: Path  # the user's copy
    rel: str  # where it is in the game's folder: "valve/dlls/hl.dll"
    name: str  # its source files' folder, src/auto/<name>/
    pe: PE  # at the address the project gives it
    built: Build
    preferred: int  # the address its header prefers


@functools.cache
def _delink_tool():
    """The project's tools/delink.py (standard library only), whose rebase and programs the analysis shares."""
    spec = importlib.util.spec_from_file_location("decompdecomp_pc_delink", TEMPLATE / "tools" / "delink.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def rebase(data: bytes, base: int) -> bytes:
    return _delink_tool().rebase(data, base)


def unit_module(unit: str) -> str:
    """The program a unit is of: its folder, a DLL's name ("" for the .exe's)."""
    return _delink_tool().module(unit)


def export_name(raw: str) -> str | None:
    """The real name of what a DLL exports as `raw`: a C name as it is, a decorated one bare (_Init@4 is Init), a
    C++ function by its name and a method by its class's too (?start@App@@QAEXXZ is App::start, as tools/names.py
    reads it); None: none to write (an operator, a constructor, a template's)."""
    if raw.startswith("?"):
        word, _, rest = raw[1:].partition("@")
        scopes = [] if rest.startswith("@") else rest.split("@@", 1)[0].split("@")[::-1]
        return "::".join([*scopes, word]) if all(_IDENT.fullmatch(n) for n in (*scopes, word)) else None
    m = re.fullmatch(r"[_@]?([A-Za-z_][A-Za-z0-9_]*)(?:@\d+)?", raw)
    return m.group(1) if m else None


def exports(pe: PE) -> dict[int, str]:
    """Address -> the name a DLL exports it by, from its export table (export_name); forwarded exports (code of
    another DLL) left out."""
    at = _pe_header(pe.data) + OPTIONAL_HEADER_AT
    rva, size = struct.unpack_from("<II", pe.data, at + EXPORTS_AT)
    table = pe.read(pe.image_base + rva, EXPORT_DIRECTORY_BYTES) if rva else b""
    if len(table) < EXPORT_DIRECTORY_BYTES:
        return {}
    _, _, _, _, _, _, count, named, functions, names, ordinals = struct.unpack("<IIHHIIIIIII", table)
    out = {}
    for k in range(min(named, MAX_EXPORTS)):
        word, number = pe.read(pe.image_base + names + 4 * k, 4), pe.read(pe.image_base + ordinals + 2 * k, 2)
        if len(word) < 4 or len(number) < 2 or (index := struct.unpack("<H", number)[0]) >= count:
            continue
        target = struct.unpack("<I", pe.read(pe.image_base + functions + 4 * index, 4).ljust(4, b"\0"))[0]
        raw = pe.read(pe.image_base + struct.unpack("<I", word)[0], IMPORT_NAME_BYTES).split(b"\0", 1)[0]
        name = export_name(raw.decode("latin-1"))
        if name and target and not rva <= target < rva + size:
            out.setdefault(pe.image_base + target, name)
    return out


def program_in(folder: Path) -> Path | None:
    """The game's program in a folder the user pointed at: the biggest 32-bit x86 .exe in it decompdecomp can start
    (a launcher, a tool or an uninstaller is smaller than the game), else the biggest it can't (whose import then says
    why); None: no Windows program in it."""
    found = []
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() == ".exe" and path.is_file():
            with open(path, "rb") as f:
                head = f.read(PE_PROBE_BYTES)
            if is_windows_program(head):
                data = path.read_bytes()
                found.append((not is_pe(head) or bool(describe(data).blockers()), -len(data), path))
    return min(found)[2] if found else None


def _others(path: Path) -> str | None:
    """Why a DLL isn't the game's own, by its name; None: nothing says so."""
    if path.suffix.lower() == ".asi":
        return PLUGIN
    stem = path.stem.lower().replace("-", "_")  # (avcodec-53, api-ms-win-crt-heap-l1-1-0)
    return next((why for why, names in OTHERS_DLLS if names.fullmatch(stem)), None)


def _overlaps(start: int, size: int, taken: list[tuple[int, int]]) -> tuple[int, int] | None:
    return next(((a, b) for a, b in taken if start < b and a < start + size), None)


def find_modules(game: Path, main: PE) -> tuple[list[Module], list[tuple[str, str]]]:
    """The game's own DLLs beside its program `game` (in its folder and MODULE_DEPTH below, not in a folder with a
    program of its own: a launcher's, a tool's, an installer's), each at the address the project gives it, in the
    order Windows would load them (the game's folder first); and (where, why) of each DLL left out: someone else's by
    its name (OTHERS_DLLS), an ASI plugin, one the import can't start (Build.blockers), or built long after the
    program (a mod's or a fix's)."""
    top = game.parent
    taken = [(main.image_base, main.image_base + main.image_size)]
    found: list[Module] = []
    left_out: list[tuple[str, str]] = []
    names = {""}
    candidates = [p for depth in range(MODULE_DEPTH + 1) for p in sorted(top.glob("/".join(["*"] * (depth + 1))),
                                                                         key=lambda p: p.as_posix().lower())
                  if p.suffix.lower() in (".dll", ".asi") and p.is_file()]
    for path in candidates:
        rel = path.relative_to(top)
        folders = [top.joinpath(*rel.parts[:k]) for k in range(1, len(rel.parts))]
        if any(f.name.startswith(".") or any(p.suffix.lower() == ".exe" for p in f.iterdir()) for f in folders):
            continue
        if why := _others(path):
            left_out.append((rel.as_posix(), why))
            continue
        data = path.read_bytes()
        if not is_pe(data[:PE_PROBE_BYTES]):
            left_out.append((rel.as_posix(), NOT_PROGRAM))
            continue
        built = describe(data)
        if blockers := built.blockers():
            left_out.append((rel.as_posix(), "; ".join(blockers)))
            continue
        pe = read_pe(data)
        stamps = (main.timestamp, pe.timestamp)
        if all(PLAUSIBLE_TIMESTAMPS[0] < t < PLAUSIBLE_TIMESTAMPS[1] for t in stamps) and \
                pe.timestamp - main.timestamp > MODULE_YEAR:
            left_out.append((rel.as_posix(), LATER))
            continue
        base, preferred = pe.image_base, pe.image_base
        while (hit := _overlaps(base, pe.image_size, taken)) is not None:
            base = -(-hit[1] // DLL_GRANULARITY) * DLL_GRANULARITY
        if base != preferred:
            flags = struct.unpack_from("<H", data, _pe_header(data) + 4 + CHARACTERISTICS_AT)[0]
            relocations = struct.unpack_from("<II", data, _pe_header(data) + OPTIONAL_HEADER_AT + BASE_RELOCATIONS_AT)
            if flags & RELOCS_STRIPPED or not relocations[1]:
                left_out.append((rel.as_posix(), NO_ROOM))
                continue
            pe = read_pe(rebase(data, base))
        taken.append((base, base + pe.image_size))
        name = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "dll"
        while name in names:
            name += "_"
        names.add(name)
        found.append(Module(path, rel.as_posix(), name, pe, built, preferred))
    return found, left_out


def left_out_lines(left_out: list[tuple[str, str]]) -> list[str]:
    """What the import log says of the DLLs left out: a line for each reason, naming them."""
    reasons = dict.fromkeys(why for _, why in left_out)
    return [f"left out, {LEFT_OUT.get(why, why)}: {', '.join(rel for rel, w in left_out if w == why)}"
            for why in reasons]


def missing(root: Path, version: str) -> list[str]:
    """The user's copies a project's programs need that aren't in it (yet): its .exe, its DLLs."""
    try:
        info = json.loads((root / "config" / version / "functions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [exe for exe, _, _ in _delink_tool().programs(info).values() if not (root / exe).is_file()]


def programs(root: Path, info: dict) -> dict[str, PE]:
    """Each program of a project (functions.json's `info`) by its source files' folder ("" for the .exe), at the
    address the project gives it."""
    return {name: read_pe(data if base is None else rebase(data, base))
            for name, (path, _, base) in _delink_tool().programs(info).items()
            for data in [(root / path).read_bytes()]}


# ---------------------------------------------------------------------------------------------------- analysis


@dataclass
class Function:
    addr: int
    size: int
    name: str  # the symbol, decorated as MSVC decorates the C name
    relocs: list[tuple[int, int, str | None, int]] = field(default_factory=list)  # (offset, type, symbol, addend)
    unit: str = ""  # its source file (functions.json's unit), once the project has them


def decorate(name: str, convention: str, purge: int, params: int) -> str:
    """The symbol MSVC makes for a C function: _f (__cdecl), _f@8 (__stdcall), @f@8 (__fastcall). __thiscall
    functions (C++ methods, `this` in ecx) are written in C as __fastcall with an unused second argument (edx)."""
    if convention == "__thiscall":
        return f"@{name}@{purge + 8}"
    if convention == "__fastcall":
        return f"@{name}@{purge + 4 * min(max(params, 1), 2)}"
    if purge > 0:
        return f"_{name}@{purge}"
    return f"_{name}"


def _disasm(md, code: bytes, addr: int):
    """Instructions, stopping at bytes that don't decode."""
    return md.disasm(code, addr)


def _tables(md, pe: PE, start: int, code_end: int, limit: int) -> tuple[int, int, list[int]]:
    """(where the function's code ends, where its data ends, the jump table entries' offsets): MSVC puts a switch's
    jump table (and its byte index table) right after the function's code."""
    tables, bytes_at = [], []
    for ins in _disasm(md, pe.read(start, code_end - start), start):
        for op in ins.operands:
            if op.type != x86.X86_OP_MEM or ins.disp_size != 4:
                continue
            disp = op.mem.disp & 0xFFFFFFFF
            if not start < disp < limit or disp <= ins.address:
                continue
            if op.mem.scale == 4 and ins.id == x86.X86_INS_JMP:
                tables.append(disp)
            elif op.size == 1:
                bytes_at.append(disp)
    code_end = min([code_end, *tables, *bytes_at])
    data_end, entries = code_end, []
    for t in sorted(set(tables)):
        at = t
        while at + 4 <= limit and (at == t or at not in tables):  # (one table ends where the next one starts: its
            # entries were relocated twice, and the code rebuilt from the objects had them doubled)
            value = struct.unpack("<I", pe.read(at, 4).ljust(4, b"\0"))[0]
            if not start <= value < code_end:
                break
            entries.append(at - start)
            at += 4
        data_end = max(data_end, at)
    for b in sorted(set(bytes_at)):  # a byte table runs to the next function, less its padding
        tail = pe.read(b, limit - b).rstrip(b"\x90\xcc")
        data_end = max(data_end, b + len(tail))
    return code_end, data_end, entries


def analyse(pe: PE, found: list[dict]) -> list[Function]:
    """Every function's extent, name and relocations, from Ghidra's function list."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    rows: dict[int, dict] = {}
    for r in found:
        s = pe.section_at(r["entry"])
        if r.get("thunk") or s is None or not s.code or pe.offset(r["entry"]) is None:
            continue
        rows[r["entry"]] = r
    starts = sorted(rows)
    i = 0
    extents: dict[int, tuple[int, int, list[int]]] = {}  # start -> (code end, end, jump table entries)
    while i < len(starts):
        start = starts[i]
        sec = pe.section_at(start)
        limit = min(starts[i + 1] if i + 1 < len(starts) else sec.va + sec.size, sec.va + sec.size)
        code_end = max(start + 1, min(rows[start].get("end", limit), limit))
        code_end, end, entries = _tables(md, pe, start, code_end, limit)
        gap = pe.read(end, limit - end)
        rest = gap.lstrip(b"\x90").lstrip(b"\xcc\x00")
        if rest and not rest.strip(b"\x90\xcc\x00") == b"":
            # code nobody calls directly (callbacks, vtables) that Ghidra didn't make a function: one of its own
            missed = limit - len(rest)
            # less the padding after it, but not its last instruction's zeros (a `ret 8` ends with one)
            body = len(rest.rstrip(b"\x90\xcc\x00"))
            # or up to the int3s Visual C++ 2005 and later pad each function of a file with: what follows them is
            # another function (inlined everywhere, so nothing calls it: Ghidra missed several in a row there)
            body = next((i.address + i.size for i in _disasm(md, rest, 0) if i.address + i.size >= body or (
                i.mnemonic in ("ret", "jmp") and rest[i.address + i.size:i.address + i.size + 1] == b"\xcc")), body)
            rows[missed] = {"entry": missed, "end": missed + body, "name": "",
                            "source": "DEFAULT", "purge": 0, "convention": "__cdecl"}
            starts.insert(i + 1, missed)
            limit = missed
            gap = pe.read(end, limit - end)
        if gap and gap.strip(b"\x90") == b"":
            end = limit  # Visual C++ 6's own padding: part of the compiled function's section too
        extents[start] = (code_end, end, entries)
        i += 1

    names = {}
    for start, r in rows.items():
        base = r.get("name", "")
        named = r.get("source") in ("IMPORTED", "USER_DEFINED") and _IDENT.fullmatch(base) and base != "entry"
        names[start] = decorate(base if named else f"func_{start:08X}", r.get("convention", ""),
                                int(r.get("purge") or 0), int(r.get("params") or 0))

    return [Function(start, extents[start][1] - start, names[start], _relocations(md, pe, start, *extents[start], names))
            for start in starts]


def _relocations(md, pe: PE, start: int, code_end: int, end: int, entries: list[int],
                 names: dict[int, str]) -> list[tuple[int, int, str | None, int]]:
    """The relocations of the function from `start` to `end` (its code up to `code_end`, then its jump tables, whose
    entries are at `entries`): what its code says about addresses outside it, by the symbols `names` gives
    functions' addresses."""

    def symbol(addr: int) -> tuple[str | None, int]:
        if start <= addr < end:
            return None, addr - start
        if addr in names:
            return names[addr], 0
        if addr in pe.imports:
            return f"__imp__{pe.imports[addr]}", 0
        return f"_D_{addr:08X}", 0

    relocs = []
    for ins in _disasm(md, pe.read(start, code_end - start), start):
        at = ins.address - start
        if ins.size >= 5 and (x86.X86_GRP_JUMP in ins.groups or x86.X86_GRP_CALL in ins.groups) \
                and ins.operands and ins.operands[0].type == x86.X86_OP_IMM \
                and ins.id not in (x86.X86_INS_LCALL, x86.X86_INS_LJMP):  # rel32: not a short jump, nor a far one
            target = ins.operands[0].imm & 0xFFFFFFFF
            if not start <= target < end:  # calls, and jumps to other functions (tail calls)
                name, _ = symbol(target)
                relocs.append((at + ins.size - 4, REL32, name, 0))
            continue
        for op in ins.operands:
            if op.type == x86.X86_OP_MEM and ins.disp_size == 4 and pe.contains(op.mem.disp & 0xFFFFFFFF):
                name, addend = symbol(op.mem.disp & 0xFFFFFFFF)
                relocs.append((at + ins.disp_offset, DIR32, name, addend))
            elif op.type == x86.X86_OP_IMM and ins.imm_size == 4 and pe.contains(op.imm & 0xFFFFFFFF):
                name, addend = symbol(op.imm & 0xFFFFFFFF)
                relocs.append((at + ins.imm_offset, DIR32, name, addend))
    for off in entries:
        relocs.append((off, DIR32, None, struct.unpack("<I", pe.read(start + off, 4))[0] - start))
    return sorted(relocs)


CALL = 0xE8  # the opcode before a rel32 call's target
JMP_REL32, TWO_BYTE_OPCODE, JCC_REL32 = 0xE9, 0x0F, range(0x80, 0x90)  # the other near rel32 jumps' opcodes
_DATA_SYMBOL = re.compile(r"_D_([0-9A-F]{8})")  # data by its address, as analyse names it
MAX_INSTRUCTION_BYTES = 15


def join(pe: PE, parts: list[Function], names: dict[int, str]) -> Function:
    """One function of neighbouring functions (`parts`, in order; one, to give it a new extent), under the first one's
    name, with the bytes no function claims between them, analysed again as one: what pointed from one part to
    another is the function's own now (no relocation for a jump, a label for an address), and a jump table after a
    part is read as the whole's (a function's table comes after its last piece). `names`: every function's address
    -> its symbol."""
    first, end = parts[0], parts[-1].addr + parts[-1].size
    if any(b.addr < a.addr + a.size for a, b in zip(parts, parts[1:])):
        raise ForkError("they overlap in the project's config, so they can't be joined")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    code_end, _, entries = _tables(md, pe, first.addr, end, end)
    return Function(first.addr, end - first.addr, first.name,
                    _relocations(md, pe, first.addr, code_end, end, entries, names), first.unit)


def merge_pieces(pe: PE, functions: list[Function], keep: set[str] = frozenset(), movable=None,
                 log=print) -> tuple[list[Function], dict[str, list[str]]]:
    """Join the pieces Ghidra cut off functions (arch/x86.piece: a lone epilogue after a call it took for one that
    never returns, a catch block or an `__except` filter after the function's return) to the function before them,
    which they continue: on a PC game 181 such pieces could never match, and neither could the functions they
    belong to. Not a piece another function calls or holds the address of (a function's start: Ghidra's guess was
    right), nor the function's own jump table (Ghidra made a function of one), nor into or out of a function of
    `keep` (matched ones: their C would no longer be the whole function; the exception-handling stubs, whose first
    one reads the frame of the function before it). Across two source files
    (`Function.unit`), a piece moves to the earlier one only when its own keeps other functions and
    `movable(piece)` says its C isn't there. Returns the functions, and each that grew with the pieces it took."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    names = {f.addr: f.name for f in functions}
    reached: dict[str, set[str]] = {}  # name -> the functions whose code calls it or holds its address
    for f in functions:
        for off, kind, name, _ in f.relocs:
            if name is not None and (kind == DIR32 or pe.read(f.addr + off - 1, 1) == bytes([CALL])):
                reached.setdefault(name, set()).add(f.name)
    home: dict[str, str] = {}  # a piece -> the function it was joined to

    def now(name: str) -> str:
        while name in home:
            name = home[name]
        return name
    out, joined, k = list(functions), {}, 1
    while k < len(out):
        owner, part = out[k - 1], out[k]
        before = pe.read(owner.addr, part.addr - owner.addr)
        # (its own code may call it, a `__finally` block, or hold its address, where a catch block goes on)
        if owner.name in keep or part.name in keep or {now(n) for n in reached.get(part.name, ())} - {owner.name} \
                or not _ends_at(md, before, owner.size) or _reads_from(md, before, owner.addr, part.addr) or \
                not x86_arch.piece(before, pe.read(part.addr, part.size)) or \
                owner.unit != part.unit and (sum(f.unit == part.unit for f in out) < 2 or
                                             (movable is not None and not movable(part))):
            k += 1
            continue
        out[k - 1:k + 1] = [join(pe, [owner, part], names)]
        home[part.name] = owner.name
        joined[owner.name] = [*joined.get(owner.name, []), part.name, *joined.pop(part.name, [])]
        k = max(1, k - 1)  # the grown function may itself continue the one before it now
    if joined:
        log(f"{sum(len(v) for v in joined.values())} pieces of functions Ghidra cut off joined to the function they "
            "belong to")
    return out, joined


def _reads_from(md, code: bytes, at: int, addr: int) -> bool:
    """Whether the code (at address `at`) reads memory at `addr`: a jump table there (Ghidra made a function of the
    table after a switch), data, not code."""
    return any(op.type == x86.X86_OP_MEM and op.mem.disp & 0xFFFFFFFF == addr
               for ins in _disasm(md, code, at) for op in ins.operands)


def _ends_at(md, before: bytes, size: int) -> bool:
    """Whether the function whose code is `size` bytes, followed by what's up to the next function, ends right
    there: nothing between them, or only the rest of its own last instruction. Projects made before analyse knew
    that a `ret 8` ends with a zero byte lost that byte to no function."""
    if len(before) == size:
        return True
    if not 0 < len(before) - size < MAX_INSTRUCTION_BYTES:
        return False
    last = None
    for ins in _disasm(md, before, 0):
        last = ins
    return last is not None and last.address < size and last.address + last.size == len(before)


def eh_stub(md, code: bytes, addr: int) -> str | None:
    """Whether a function's code has the shape of an exception-handling stub: "handler" (`mov eax, <its FuncInfo>;
    jmp __CxxFrameHandler`; Ghidra merges a few into one function at times), "funclet" (it runs in its parent's frame:
    it uses [ebp±N] before it sets up a frame, calls anything or leaves, as `lea ecx, [ebp-N]; jmp <destructor>`, or
    destroys a global: `mov ecx, <global>; jmp <destructor>`), or None. A real function that builds its frame through
    __EH_prolog (`mov eax, <handler>; call __EH_prolog`) calls before it uses ebp: not a stub."""
    ins = list(_disasm(md, code[:EH_STUB_BYTES], addr))
    if len(ins) >= 2 and ins[0].id == x86.X86_INS_MOV and ins[1].id == x86.X86_INS_JMP \
            and ins[0].operands[1].type == x86.X86_OP_IMM and ins[0].operands[0].reg in (x86.X86_REG_EAX, x86.X86_REG_ECX):
        return "handler" if ins[0].operands[0].reg == x86.X86_REG_EAX else "funclet"
    for one in ins:
        first = one.operands[0] if one.operands else None
        if one.id in (x86.X86_INS_PUSH, x86.X86_INS_MOV, x86.X86_INS_LEA) and first is not None and first.type == x86.X86_OP_REG \
                and first.reg == x86.X86_REG_EBP:
            return None  # its own frame (the C runtime's __SEH_prolog: lea ebp, [esp+N])
        if any(op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_EBP for op in one.operands):
            return "funclet"
        if one.id in (x86.X86_INS_CALL, x86.X86_INS_RET, x86.X86_INS_JMP):
            return None
    return None


def exception_stubs(pe: PE, functions: list[tuple[int, int]]) -> set[int]:
    """The addresses of the exception-handling stubs among the functions ((address, size)): those with a stub's shape
    (eh_stub) in the last run of them in a code section, where the linker puts .text$x, and only when the run has a
    handler (every function with exception handling has one). Visual C++ 6.0 to 7.1 end the section with the run; from
    2005 on, a few dynamic initializers follow it, and then the run must be long (EH_STUB_FOLLOWED_RUN). Elsewhere the
    same shapes are pieces Ghidra cut off real functions, such as a lone epilogue: on a Visual C++ 6.0 game, 204 in
    the main body."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    found: set[int] = set()
    for sec in (s for s in pe.sections if s.code):
        run, kinds, gap, after = [], set(), 0, 0
        for addr, size in sorted(((a, n) for a, n in functions if sec.va <= a < sec.va + sec.size), reverse=True):
            kind = eh_stub(md, pe.read(addr, min(size, EH_STUB_BYTES)), addr)
            if kind is not None:
                run.append(addr)
                kinds.add(kind)
                gap = 0
            elif not run:
                after += 1
            elif (gap := gap + 1) > EH_STUB_GAP:
                if _stubs_run(kinds, after, len(run)):
                    break
                # not the run (a global's destructor among the initializers after it, say): code after it
                run, kinds, gap, after = [], set(), 0, after + len(run) + gap
        if _stubs_run(kinds, after, len(run)):
            found.update(run)
    return found


def _stubs_run(kinds: set[str], after: int, count: int) -> bool:
    """Whether a run of `count` stub shapes (`kinds`), with `after` functions after it in its section, is .text$x."""
    return "handler" in kinds and (after <= EH_STUB_GAP or count >= EH_STUB_FOLLOWED_RUN)


def mark_exception_stubs(root: Path, version: str) -> int | None:
    """Set the project's exception-handling stubs aside (exception_stubs): each one's entry in functions.json says why
    (`generated`, which the project's measuring reads: Console.generated_functions). Returns how many it marked; None
    when the user's copy of the program isn't in the project (yet)."""
    path = root / "config" / version / "functions.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    if missing(root, version):
        return None
    stubs: set[int] = set()
    for name, pe in programs(root, info).items():  # (each program's stubs end its own code)
        stubs |= exception_stubs(pe, [(fn["addr"], fn["size"]) for unit, fns in info["units"].items()
                                      if unit_module(unit) == name for fn in fns])
    entries = [fn for fns in info["units"].values() for fn in fns]
    marked = [fn for fn in entries if fn["addr"] in stubs and fn.get("generated") != EH_STUB_WHY]
    for fn in marked:
        fn["generated"] = EH_STUB_WHY
    if marked:
        path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return len(marked)


def _padding(pe: PE, before: Function, after: Function) -> int | None:
    """The byte the gap between two neighbouring functions is filled with, when it's only what aligns the next one
    (fewer than FUNCTION_ALIGN bytes of one filler, up to an aligned start); None: no gap, or more than that."""
    end = before.addr + before.size
    gap = pe.read(end, after.addr - end) if 0 < after.addr - end < FUNCTION_ALIGN else b""
    return gap[0] if gap and after.addr % FUNCTION_ALIGN == 0 and gap.count(gap[:1]) == len(gap) and \
        gap[0] in PADDING_BYTES else None


def linker_padding(pe: PE, functions: list[Function]) -> int | None:
    """The filler the program's linker aligns every function with, when it does (Visual C++ 2005 on: int3), or None
    (Visual C++ 6.0: its compiler pads each function itself, and the linker's int3 is rare enough to mean something)."""
    found: dict[int, int] = {}
    for a, b in zip(functions, functions[1:]):
        if (byte := _padding(pe, a, b)) is not None:
            found[byte] = found.get(byte, 0) + 1
    byte = max(found, key=found.get, default=None)
    return byte if byte is not None and found[byte] * ALIGNED_SHARE >= len(functions) else None


def units(functions: list[Function], pe: PE | None = None) -> dict[str, list[Function]]:
    """Source files: runs of neighbouring functions of about UNIT_BYTES each. A gap between two functions ends a file
    (an object's end), unless it's only the padding the program's linker puts before every function (linker_padding:
    a 2005 game got 19,028 source files for its 20,863 functions)."""
    filler = linker_padding(pe, functions) if pe is not None else None
    out: dict[str, list[Function]] = {}
    cur: list[Function] = []
    for fn in functions:
        if cur and (fn.addr + fn.size - cur[0].addr > UNIT_BYTES or fn.addr != cur[-1].addr + cur[-1].size and
                    (filler is None or _padding(pe, cur[-1], fn) != filler)):
            out[f"text_{cur[0].addr:08X}"] = cur
            cur = []
        cur.append(fn)
    if cur:
        out[f"text_{cur[0].addr:08X}"] = cur
    return out


def _entry(pe: PE, f: Function) -> dict:
    """A function as functions.json keeps it (tools/delink.py cuts it out of the program by its offset)."""
    return {"name": f.name, "addr": f.addr, "offset": pe.offset(f.addr), "size": f.size,
            "relocs": [list(r) for r in f.relocs]}


def _project_functions(root: Path, version: str) -> tuple[Path, dict, dict[str, PE], list[Function]]:
    """A project's functions.json, what it holds, its programs (`programs`) and its functions in address order, each
    with its source file (whose folder says its program: unit_module)."""
    path = root / "config" / version / "functions.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    pes = programs(root, info)
    functions = sorted((Function(fn["addr"], fn["size"], fn["name"], [tuple(r) for r in fn["relocs"]], unit)
                        for unit, fns in info["units"].items() for fn in fns), key=lambda f: f.addr)
    return path, info, pes, functions


def _save_functions(path: Path, info: dict, pes: dict[str, PE], functions: list[Function]) -> None:
    """functions.json with these functions, each in its source file, the files in their order. A function that
    keeps its extent keeps why no C makes it (`generated`, mark_exception_stubs); one that grew isn't a stub."""
    was = {(fn["addr"], fn["size"]): fn["generated"] for fns in info["units"].values() for fn in fns
           if fn.get("generated")}
    grouped: dict[str, list[dict]] = {u: [] for u in info["units"]}
    for f in functions:
        why = was.get((f.addr, f.size))
        grouped[f.unit].append(_entry(pes[unit_module(f.unit)], f) | ({"generated": why} if why else {}))
    path.write_text(json.dumps({**info, "units": grouped}, indent=1), encoding="utf-8")


def _c_of(root: Path, info: dict, f: Function) -> bool:
    """Whether the C in f's source file defines it (func_0041B470, however it's spelled, or its real name)."""
    source = root / _source(root, f.unit)
    text = source.read_text(encoding="utf-8", errors="replace") if source.is_file() else ""
    key = f"func_{f.addr:08X}"
    return any(re.search(rf"\b{re.escape(n)}\s*\([^;{{]*\)\s*\{{", text)
               for n in (key, (info.get("names") or {}).get(key)) if n)


def drop_repeated_relocations(root: Path, version: str) -> int:
    """A project made before analyse stopped a jump table where the next one starts has the second table's entries
    relocated twice (in 23 functions of a PC game): the code rebuilt from its objects had them doubled, so its
    rebuild check could never pass. Drops the repeats from functions.json; returns how many functions had them."""
    path = root / "config" / version / "functions.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    repeated = 0
    for fns in info["units"].values():
        for fn in fns:
            once = [list(r) for r in dict.fromkeys(tuple(r) for r in fn["relocs"])]
            repeated += len(once) != len(fn["relocs"])
            fn["relocs"] = once
    if repeated:
        path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return repeated


def _near_rel32(pe: PE, at: int) -> bool:
    """Whether the 4 bytes at `at` are the target of a near call or jump (e8, e9, 0f 80-8f before them)."""
    before = pe.read(at - 2, 2)
    return before[1] in (CALL, JMP_REL32) or before[0] == TWO_BYTE_OPCODE and before[1] in JCC_REL32


def drop_far_call_relocations(root: Path, version: str) -> int:
    """A project made before analyse told a far call or jump (`lcall 0x300:0`, 9a or ea, in bytes Ghidra took for
    code) from a near one relocated its address as a call to another function: the code rebuilt from its objects had
    it changed (one function of a 2005 PC game), so its rebuild check could never pass. Drops those relocations from
    functions.json; returns how many functions had one."""
    path = root / "config" / version / "functions.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    pes = programs(root, info)
    changed = 0
    for unit, fns in info["units"].items():
        for fn in fns:
            pe = pes[unit_module(unit)]
            near = [r for r in fn["relocs"] if r[1] != REL32 or _near_rel32(pe, fn["addr"] + r[0])]
            changed += len(near) != len(fn["relocs"])
            fn["relocs"] = near
    if changed:
        path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return changed


def complete_last_instructions(root: Path, version: str, keep: set[str] = frozenset()) -> int:
    """A project made before analyse kept the zero bytes a function found in a gap ends with (`ret 8` is c2 08 00,
    a `jmp` forward has zeros too) has functions ending in the middle of their last instruction (on a PC game 465 of
    11,877), which no C can match. Each gets the rest of that instruction, and the padding after it, as analyse
    gives them now; not one ending in data (a jump table), nor a matched one (`keep`). Returns how many grew."""
    path, info, pes, functions = _project_functions(root, version)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    names = {f.addr: f.name for f in functions}
    grown = 0
    for k, f in enumerate(functions):
        pe = pes[unit_module(f.unit)]
        sec = pe.section_at(f.addr)
        end = f.addr + f.size
        limit = min(functions[k + 1].addr if k + 1 < len(functions) else end, sec.va + sec.size if sec else end)
        if f.name in keep or limit <= end or _tables(md, pe, f.addr, end, end)[:2] != (end, end):
            continue
        last = None
        for ins in _disasm(md, pe.read(f.addr, limit - f.addr), f.addr):
            if ins.address >= end:
                break
            last = ins
        if last is None or last.address + last.size <= end:
            continue  # it ends on an instruction, or its code doesn't read as instructions
        whole = last.address + last.size
        if not pe.read(whole, limit - whole).strip(b"\x90"):
            whole = limit  # Visual C++ 6's padding after it, as analyse takes it
        functions[k] = join(pe, [Function(f.addr, whole - f.addr, f.name, f.relocs, f.unit)], names)
        grown += 1
    if grown:
        _save_functions(path, info, pes, functions)
    return grown


def merge_project_pieces(root: Path, version: str, keep: set[str] = frozenset(), log=print) -> dict[str, list[str]]:
    """merge_pieces on a project made before it: its functions.json changes, nothing else (each function keeps its
    source file; a piece first in its file moves to the file before only when no C there defines it). Returns each
    function that grew, with the pieces it took."""
    path, info, pes, functions = _project_functions(root, version)
    stubs = {fn["name"] for fns in info["units"].values() for fn in fns if fn.get("generated")}  # (none joined)
    out, joined = [], {}
    for name, pe in pes.items():  # (a piece is of a function of its own program)
        own, took = merge_pieces(pe, [f for f in functions if unit_module(f.unit) == name], keep | stubs,
                                 movable=lambda part: not _c_of(root, info, part), log=log)
        out += own
        joined.update(took)
    if joined:
        _save_functions(path, info, pes, sorted(out, key=lambda f: f.addr))
    return joined


def join_functions(root: Path, version: str, first: str, last: str, keep: set[str] = frozenset()) -> str:
    """Make one function of `first`, `last` and every function between them: what agents agreed Ghidra cut (join, as
    merge_pieces does). Across source files, the later files' part moves to the first one's, when their C doesn't
    define what moves and each keeps some code. Never a matched function (`keep`). Returns what changed, as a
    sentence."""
    path, info, pes, functions = _project_functions(root, version)
    at = {f.name: k for k, f in enumerate(functions)}
    if first not in at or last not in at:
        raise ForkError(f"{first if first not in at else last} isn't one of the game's functions")
    lo, hi = sorted((at[first], at[last]))
    parts = functions[lo:hi + 1]
    if lo == hi:
        raise ForkError(f"{first} and {last} must be two functions")
    if bad := [f.name for f in parts if f.name in keep]:
        raise ForkError(f"{bad[0]} is matched: joining would change it, so the join is refused")
    if len({unit_module(f.unit) for f in parts}) > 1:
        raise ForkError(f"{first} and {last} are in two programs (the .exe and a DLL, or two DLLs): only functions of "
                        "one program can be joined")
    moved = [f for f in parts if f.unit != parts[0].unit]
    for f in moved:
        if all(g in parts for g in functions if g.unit == f.unit):
            raise ForkError(f"{f.unit} would be left with no code: join only part of it")
        if _c_of(root, info, f):
            raise ForkError(f"{f.unit} has C for {f.name}: move it to {parts[0].unit} first")
    whole = join(pes[unit_module(parts[0].unit)], parts, {f.addr: f.name for f in functions})
    _save_functions(path, info, pes, [*functions[:lo], whole, *functions[hi + 1:]])
    files = sorted({f.unit for f in moved})
    return (f"{', '.join(f.name for f in parts[1:])} joined to {parts[0].name}"
            + (f" (moved from {', '.join(files)})" if files else ""))


def _source(root: Path, unit: str) -> str:
    """A unit's source file, as configure.py's source_of names it: .cpp, or .c in projects made before C++."""
    cpp = Path("src") / "auto" / f"{unit}.cpp"
    return cpp.as_posix() if (root / cpp).exists() or not (root / cpp).with_suffix(".c").exists() \
        else cpp.with_suffix(".c").as_posix()


def split_unit(root: Path, version: str, source: str, at: str, new: str) -> dict:
    """Divide `source`'s unit in functions.json at function `at` (its symbol): it and the functions after it go to
    `new`'s (src/auto/<unit>.cpp), right after it, with the file's compiler (file_compilers.json). Returns each
    config file changed, with what it held before."""
    path = root / "config" / version / "functions.json"
    text = path.read_text(encoding="utf-8")
    info = json.loads(text)
    unit = next((u for u in info["units"] if _source(root, u) == source), None)
    if unit is None:
        raise ForkError(f"{source} isn't one of the project's source files")
    m = re.fullmatch(r"src/auto/((?:[A-Za-z0-9_]+/)?[A-Za-z0-9_]+)\.(?:cpp|c)", new)
    if m is None or unit_module(m.group(1)) != unit_module(unit):
        folder = f"src/auto/{unit_module(unit)}/" if unit_module(unit) else "src/auto/"
        raise ForkError(f"{new}: this file's functions are in {folder} (a DLL's files are in a folder of its name): "
                        f"name it {folder}<name>.cpp (letters, digits, _)")
    if m.group(1) in info["units"]:
        raise ForkError(f"{new} exists already: pick another new_name")
    names = [fn["name"] for fn in info["units"][unit]]
    if at not in names[1:]:
        raise ForkError(f"{at} isn't one of {source}'s functions after its first")
    k = names.index(at)
    units = {}
    for u, fns in info["units"].items():
        units[u] = fns[:k] if u == unit else fns
        if u == unit:
            units[m.group(1)] = fns[k:]
    path.write_text(json.dumps({**info, "units": units}, indent=1), encoding="utf-8")
    saved = {path: text}
    compilers = compilers_file(root, version)
    if source in (own := file_compilers(root, version)):
        saved[compilers] = compilers.read_text(encoding="utf-8")
        _write_compilers(compilers, {**own, new: own[source]})
    return saved


def compilers_file(root: Path, version: str) -> Path:
    return root / "config" / version / "file_compilers.json"


def file_compilers(root: Path, version: str) -> dict[str, str]:
    """{source file: Visual C++ version} for the files not built with the project's own (tools/configure.py reads it)."""
    try:
        return json.loads(compilers_file(root, version).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_compilers(path: Path, found: dict[str, str]) -> None:
    tmp = path.with_suffix(".tmp")  # a build reading it meanwhile sees the old map or the new one, never half
    tmp.write_text(json.dumps(dict(sorted(found.items())), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def set_file_compiler(root: Path, version: str, source: str, compiler: str, default: str) -> None:
    """Build `source` with `compiler` ("MSVC/7.1"); the project's own (`default`) needs no line."""
    found = file_compilers(root, version)
    found.pop(source, None)
    if compiler.removeprefix("MSVC/") != default.removeprefix("MSVC/"):
        found[source] = compiler.removeprefix("MSVC/")
    _write_compilers(compilers_file(root, version), found)


def file_ranges(root: Path, version: str) -> dict[str, list[tuple[int, int]]]:
    """{source file: [(start, end)]} of its functions' code, from functions.json."""
    info = json.loads((root / "config" / version / "functions.json").read_text(encoding="utf-8"))
    return {_source(root, u): [(fns[0]["addr"], fns[-1]["addr"] + fns[-1]["size"])]
            for u, fns in info["units"].items() if fns}


def set_own_compiler(root: Path, manifest: dict, compiler: str, sources: list[str]) -> list[str]:
    """`compiler` ("MSVC/7.1") as the project's own (tools/configure.py's MSVC_VERSION and the manifest's), every
    source file keeping the version it has: the map then names the files built with another."""
    version, new = manifest["version"], compiler.removeprefix("MSVC/")
    found = file_compilers(root, version)
    old = manifest.get("compiler", f"MSVC/{MSVC_DEFAULT}").removeprefix("MSVC/")
    has = {s: found.get(s, old) for s in sources}
    path = compilers_file(root, version)
    _write_compilers(path, {s: v for s, v in has.items() if v != new})
    cfg = root / "tools" / "configure.py"
    cfg.write_text(re.sub(r'^MSVC_VERSION = "[^"]*"', f'MSVC_VERSION = "{new}"', cfg.read_text(encoding="utf-8"),
                          count=1, flags=re.M), encoding="utf-8")
    (root / ".decomp.wtf.json").write_text(json.dumps({**manifest, "compiler": compiler}, indent=2), encoding="utf-8")
    return [path.relative_to(root).as_posix(), "tools/configure.py", ".decomp.wtf.json"]


# ---------------------------------------------------------------------------------------------------- the project


def compiler_pack(version: str) -> str:
    """The pack a Visual C++ comes in (the `into` of its part in toolchains.toml)."""
    pack = next((p for p in MSVC_PACKS if any(part.get("into") == version for part in toolchains.get(p).parts)), None)
    if pack is None:
        raise ForkError(f"decompdecomp has no Visual C++ {version}; it has {', '.join(_pack())}")
    return pack


# the lines of tools/configure.py a project keeps when its tools are brought up to date: what create (and the
# Settings panel, set_own_compiler) wrote for the game, and the flags its README says the user may change
KEPT_SETTINGS = (r'^MSVC_VERSION = "[^"]*"', r"^CL_FLAGS = \[[^\]]*\]")


def tools_version() -> str:
    """The template's build tools, as a short hash: a project whose manifest says another version gets them when the
    app starts (WindowsPC.on_start), so every change to them reaches every project."""
    digest = hashlib.sha1()
    for src in sorted((TEMPLATE / "tools").glob("*.py")):
        digest.update(src.name.encode() + b"\0" + src.read_bytes())
    return digest.hexdigest()[:12]


def write_tools(root: Path, version: str) -> list[str]:
    """The template's build tools in a project made by an older decompdecomp (agents never edit tools/:
    forks/project.py), keeping its configure.py's settings (KEPT_SETTINGS), and the map of each file's compiler the
    new configure.py reads. A project kept the tools it was made with, so names.py's constructors and destructors
    never reached one. Returns the files that changed."""
    cfg = root / "tools" / "configure.py"
    now = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    changed = []
    for src in sorted((TEMPLATE / "tools").glob("*.py")):
        text = src.read_text(encoding="utf-8")
        if src.name == "configure.py":
            for setting in KEPT_SETTINGS:
                if m := re.search(setting, now, re.M):
                    text = re.sub(setting, lambda _: m.group(0), text, count=1, flags=re.M)
        dest = root / "tools" / src.name
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            dest.write_text(text, encoding="utf-8")
            changed.append(f"tools/{src.name}")
    if changed and not (compilers := compilers_file(root, version)).exists():
        compilers.write_text("{}\n", encoding="utf-8")
        changed.append(compilers.relative_to(root).as_posix())
    return changed


def tool_args() -> list[str]:
    """configure.py arguments pointing at decompdecomp's pinned tools: every installed compiler pack, 6.0-7.1's
    last, as an older project's configure.py keeps only the last --msvc (its projects build with 6.0 to 7.1)."""
    packs = [p for p in reversed(MSVC_PACKS) if toolchains.installed(p) is not None]
    if not packs:
        raise ForkError("PC games are built with Microsoft Visual C++, which is proprietary: install it in Setup "
                        "(Visual C++) if you accept that, then try again")
    args = [arg for p in packs for arg in ("--msvc", str(toolchains.path(p)))]
    if toolchains.get("wibo").supported and sys.platform != "win32":
        toolchains.ensure("wibo", log=lambda *_: None)
        args += ["--wibo", str(toolchains.binary("wibo"))]
    return args


TYPES_H = """/* Basic types for this decompilation (32-bit x86, Visual C++, written in C++). Agents add structs, classes and
 * prototypes here or in new headers next to it.
 *
 * The game's functions are func_<address> and its data D_<address>: write them as free functions or as a class's
 * methods (`void Town::func_0041B470(int id)`), with the original's calling convention (methods: this in ecx; free
 * functions: __cdecl, __stdcall or __fastcall, as the code shows). The build gives what they compile to the
 * original's symbol names, so any C++ name mangling pairs with the game's code. Data: `extern int D_00402034;`. */
#ifndef COMMON_H
#define COMMON_H

typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef __int64 s64;
typedef unsigned __int64 u64;
typedef float f32;
typedef double f64;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#ifndef NULL
#define NULL 0
#endif

#endif
"""


_PROGRAMS: dict[tuple, list[PE]] = {}  # (paths, mtimes) -> the parsed programs, for data_bytes


def _programs_of(root: Path, manifest: dict) -> list[PE]:
    """The user's copies (the .exe, then the game's DLLs: functions.json's "modules"), parsed once per change of
    them (a diff reads several constants)."""
    config = root / "config" / manifest["version"] / "functions.json"
    paths = [root / manifest["game"]["path"], *([config] if config.is_file() else [])]
    key = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
    if key not in _PROGRAMS:
        info = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        modules = programs(root, info) if info.get("modules") else {}
        _PROGRAMS.clear()
        _PROGRAMS[key] = [read_pe(paths[0].read_bytes()), *(pe for name, pe in modules.items() if name)]
    return _PROGRAMS[key]


def _program_at(root: Path, manifest: dict, addr: int) -> PE | None:
    """The program (the .exe or one of the game's DLLs) whose sections hold `addr`, or None."""
    return next((pe for pe in _programs_of(root, manifest) if pe.section_at(addr) is not None), None)


def data_bytes(root: Path, manifest: dict, addr: int, length: int) -> bytes | None:
    """The bytes at an address of the user's copy, or None (not in it, uninitialized, no copy)."""
    try:
        pe = _program_at(root, manifest, addr)
    except Exception:  # no copy, or one that doesn't parse: the diff goes without values
        return None
    if pe is None:
        return None
    data = pe.read(addr, length)
    return data if data and len(data) == length else None


def _named_data(root: Path, manifest: dict, name: str) -> str | None:
    """The D_<address> a real name stands for (functions.json's "names", kept by rename_symbol), or None: a helper
    that renamed a global could no longer read_data it by its name."""
    try:
        info = json.loads((root / "config" / manifest["version"] / "functions.json").read_text(encoding="utf-8"))
    except (OSError, KeyError, ValueError):
        return None
    return next((key for key, real in (info.get("names") or {}).items() if real == name and key.startswith("D_")), None)


def read_data(root: Path, manifest: dict, name: str, length: int = READ_DEFAULT_BYTES) -> str:
    """What read_data shows on a PC project: the bytes at a D_<address> (or any address) in the user's copy, the
    addresses its words point to, and a string or floats when it reads as one."""
    m = re.fullmatch(r"_?D_([0-9A-Fa-f]{8})|(?:0x)?([0-9A-Fa-f]{6,8})", name)
    real = None
    if m is None and (key := _named_data(root, manifest, name)) is not None:  # a real name given since
        real, m = name, re.fullmatch(r"D_([0-9A-F]{8})()", key)
    if m is None:
        raise ForkError(f"`{name}` isn't data by address or a real name of data: PC data is named D_<address> "
                        "(D_00402034) until rename_symbol names it")
    addr = int(m.group(1) or m.group(2), 16)
    pe = _program_at(root, manifest, addr)
    if pe is None:
        spans = ", ".join(f"0x{p.image_base:08X}-0x{p.image_base + p.image_size:08X}"
                          for p in _programs_of(root, manifest))
        raise ForkError(f"0x{addr:08X} isn't in the game's program or its DLLs (they span {spans})")
    sec = pe.section_at(addr)
    n = max(READ_MIN_BYTES, min(int(length or READ_DEFAULT_BYTES), READ_MAX_BYTES))
    data = pe.read(addr, n)
    out = [f"{real + ' = ' if real else ''}D_{addr:08X} ({sec.name}+0x{addr - sec.va:X}, {n} bytes)"]
    if not data:
        return out[0] + f": uninitialized data ({sec.name}), zeros at start-up."
    for i in range(0, len(data), HEX_ROW_BYTES):
        chunk = data[i:i + HEX_ROW_BYTES]
        out.append(f"  +0x{i:03X}: {chunk.hex(' '):<47}  {''.join(chr(c) if c in PRINTABLE else '.' for c in chunk)}")
    words = [w for (w,) in struct.iter_unpack("<I", data[:len(data) // 4 * 4])]
    ptrs = [(4 * i, w) for i, w in enumerate(words) if pe.contains(w)]
    if ptrs:
        out.append("pointers in it: " + ", ".join(f"+0x{at:X} -> 0x{w:08X}" for at, w in ptrs[:POINTERS_SHOWN]))
    floats = [struct.unpack("<f", struct.pack("<I", w))[0] for w in words[:FLOATS_SHOWN]]
    smallest, biggest = PLAUSIBLE_FLOAT
    if words and any(smallest < abs(x) < biggest for x in floats):
        out.append("as f32: " + ", ".join(f"{x:g}" for x in floats))
    text = data.split(b"\0", 1)[0]
    if len(text) >= MIN_STRING_CHARS and all(c in PRINTABLE or c in b"\t\n" for c in text) and not (ptrs and ptrs[0][0] == 0):
        out.append(f"as a string: {json.dumps(text.decode('latin-1'))}")
    return "\n".join(out)


# a COFF object (tools/delink.py writes them): its header, section headers, symbols and relocations
COFF_HEADER = "<HHIIIHH"  # machine, sections, time, symbol table at, symbols, optional header bytes, flags
COFF_HEADER_BYTES, COFF_SECTION_BYTES, COFF_SYMBOL_BYTES, COFF_RELOC_BYTES = 20, 40, 18, 10
CODE_SHOWN = 5  # functions a rebuild that differs names


def _coff_sections(obj: bytes) -> list[tuple[str, bytes, list[tuple[int, str, int, int]]]]:
    """Each section of a COFF object as (its function's symbol, its bytes, its relocations as (offset, symbol,
    section of a local label or 0, the label's value)), as tools/delink.py writes them: a section per function, its
    symbol the first one defined in it."""
    _, count, _, table, symbols, _, _ = struct.unpack_from(COFF_HEADER, obj, 0)
    strings = table + COFF_SYMBOL_BYTES * symbols
    syms, k = [], 0
    while k < symbols:
        raw, value, section, _, _, aux = struct.unpack_from("<8sIhHBB", obj, table + COFF_SYMBOL_BYTES * k)
        if raw[:4] == b"\0\0\0\0":
            at = strings + struct.unpack_from("<I", raw, 4)[0]
            raw = obj[at:obj.index(b"\0", at)]
        syms.append((raw.rstrip(b"\0").decode("latin-1"), value, section))
        syms.extend([("", 0, 0)] * aux)
        k += 1 + aux
    out = []
    for i in range(count):
        _, _, _, size, data, relocs, _, nrelocs, _, _ = struct.unpack_from(
            "<8sIIIIIIHHI", obj, COFF_HEADER_BYTES + COFF_SECTION_BYTES * i)
        owner = next((name for name, value, section in syms if section == i + 1 and not name.startswith("$")), "")
        rel = []
        for r in range(nrelocs):
            off, index, kind = struct.unpack_from("<IIH", obj, relocs + COFF_RELOC_BYTES * r)
            name, value, section = syms[index]
            rel.append((off, kind, name, section, value))
        out.append((owner, obj[data:data + size], rel))
    return out


def check_code(root: Path, version: str, log=print) -> dict:
    """The rebuild check of a PC project: the program's code rebuilt from the build's delinked objects (each function
    placed at its address, every relocation filled in with the address its symbol names) compared with the user's
    copy, byte for byte: {"identical", "detail", "what"}. There's no relink of the whole .exe yet, so this is what a
    change to the project's functions (a join) is checked against: a function cut too long or too short, or a
    relocation to the wrong place, comes out different."""
    info = json.loads((root / "config" / version / "functions.json").read_text(encoding="utf-8"))
    pes = programs(root, info)
    functions = {fn["name"]: fn["addr"] for fns in info["units"].values() for fn in fns}
    # a program's imports are its own slots: a DLL's call into another goes through its import table too
    wheres = {name: {**functions, **{f"__imp__{imp}": slot for slot, imp in pe.imports.items()}}
              for name, pe in pes.items()}
    placed, differ = [], []
    for unit in info["units"]:
        pe, where = pes[unit_module(unit)], wheres[unit_module(unit)]
        obj = root / "build" / version / "target" / f"{unit}.obj"
        if not obj.is_file():
            differ.append(f"{unit} (no object: the build didn't make it)")
            continue
        sections = _coff_sections(obj.read_bytes())
        starts = [where.get(name) for name, _, _ in sections]
        for (name, data, relocs), start in zip(sections, starts):
            if start is None:
                differ.append(f"{name} (not in functions.json)")
                continue
            code = bytearray(data)
            for off, kind, symbol, section, value in relocs:
                data_name = _DATA_SYMBOL.fullmatch(symbol)
                if section > 0:  # a label in its own object ($L, a jump table's entry)
                    target = None if starts[section - 1] is None else starts[section - 1] + value
                else:
                    target = where.get(symbol, int(data_name.group(1), 16) if data_name else None)
                if target is None:
                    differ.append(f"{name} (refers to {symbol}, which names no address)")
                    break
                inline = struct.unpack_from("<i", code, off)[0]
                address = target + inline - (start + off + 4 if kind == REL32 else 0)
                struct.pack_into("<I", code, off, address & 0xFFFFFFFF)
            else:
                if bytes(code) != pe.read(start, len(code)):
                    differ.append(name)
                placed.append((start, len(code), name))
    placed.sort()
    differ += [f"{b[2]} (overlaps {a[2]})" for a, b in zip(placed, placed[1:]) if b[0] < a[0] + a[1]]
    if differ:
        detail = (f"{len(differ)} functions rebuilt from the delinked objects differ from your copy: "
                  f"{', '.join(differ[:CODE_SHOWN])}{'…' if len(differ) > CODE_SHOWN else ''}")
        log(f"note: {detail}")
        return {"identical": False, "detail": detail}
    covered = sum(size for _, size, _ in placed)
    each = ""
    if len(pes) > 1:  # each program's own count: a DLL's rebuild is checked against that DLL
        files = {name: Path(path).name for name, (path, _, _) in _delink_tool().programs(info).items()}
        count = {name: sum(len(fns) for unit, fns in info["units"].items() if unit_module(unit) == name)
                 for name in pes}
        each = f"; by program: {', '.join(f'{files[name]} {n:,}' for name, n in count.items())}"
    log(f"the game's code rebuilt from its {len(placed):,} delinked functions is your copy's, byte for byte{each}")
    return {"identical": True,
            "detail": f"identical: the code of its {len(placed):,} functions ({covered:,} bytes){each}",
            "what": "The game's code, rebuilt from its delinked functions,"}


def _program_units(pe: PE, found: list[dict], log) -> dict[str, list[Function]]:
    """One program's functions (from Ghidra's rows, `found`), its exception-handling stubs kept whole and the
    pieces Ghidra cut off joined to their function, in source files."""
    functions = analyse(pe, found)
    # the stubs first, as a project made before gets them (WindowsPC.on_start): no join takes one
    stubs = exception_stubs(pe, [(f.addr, f.size) for f in functions])
    functions, _ = merge_pieces(pe, functions, keep={f.name for f in functions if f.addr in stubs}, log=log)
    return units(functions, pe)


def _run(cmd: list[str], root: Path, what: str, log) -> None:
    ok, tail = ForkProject(root).run(cmd, log=log, timeout=TOOL_SECONDS)
    if not ok:
        raise ForkError(f"{what} failed:\n{tail[-ERROR_TAIL:]}")


def create(root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
    """Make the project in `root` (which must not exist yet) and build it once. Returns facts about the game."""
    from ...forks import ghidra

    info = game_info(game)
    version = info["id"]
    compiler = (compiler or f"MSVC/{info['compiler']}").removeprefix("MSVC/")
    log(f"the program: {info['summary']}")
    for why in info["cannot_match"]:
        log(f"what can't match: {why}")
    if not info["cannot_match"]:
        log(f"it can match: decompdecomp builds with Visual C++ {compiler}")
    pack = compiler_pack(compiler)
    if not (toolchains.path(pack) / compiler).is_dir():
        raise ForkError(f"this game builds with Visual C++ {compiler}, which comes in {toolchains.get(pack).name}: "
                        "install it in Setup (Compilers & tools) if you accept its licence, then try again")
    args = tool_args()
    if not ghidra.available():
        if ghidra.java_home() is not None and toolchains.installed("jdk") is None:
            log("installing a Java development kit for Ghidra (200 MB, once): this computer has none of its own")
            toolchains.ensure("jdk", log=log)
        if toolchains.installed("ghidra") is None:
            log("installing Ghidra (540 MB, once): it finds the functions in PC programs")
            toolchains.ensure("ghidra", log=log)
    pe = read_pe(game.read_bytes())
    modules, left_out = find_modules(game, pe)
    if modules or left_out:
        log(f"the game's own DLLs beside {game.name}: {', '.join(m.rel for m in modules) or 'none'}")
    for line in left_out_lines(left_out):
        log(line)
    log(f"analysing {game.name} with Ghidra (finds every function; a big game takes a few minutes)")
    found = ghidra.functions(game)
    if not found:
        why = (info["cannot_match"] or ["a program that is packed or protected in a way decompdecomp doesn't recognise "
                                        "shows none: decompdecomp never unpacks or gets around protection"])[0]
        raise ForkError(f"Ghidra found no functions in this program: {why}.")
    groups = _program_units(pe, found, log)
    log(f"{sum(map(len, groups.values()))} functions; {info['compiler_why']}: Visual C++ {compiler}")
    pes, own, real = {"": pe}, {}, {}
    taken = {re.sub(r"^[_@]|@\d+$", "", f.name) for fns in groups.values() for f in fns}
    for m in modules:
        pes[m.name] = m.pe
        moved = m.pe.image_base - m.preferred
        where = (f"at 0x{m.pe.image_base:08X}, its own address" if not moved else
                 f"at 0x{m.pe.image_base:08X}: the program or a DLL before it has its own address, 0x{m.preferred:08X}, "
                 "so it's moved by its base relocations, as Windows moves it")
        log(f"analysing {m.rel} with Ghidra: {m.built.summary()}, {where}")
        # Ghidra reads it at the address its header prefers; its own names (its exports) go to "names" below, as
        # several DLLs export the same names (an engine's and a game's DLL both export their interface)
        rows = [{**r, "entry": r["entry"] + moved, **({"end": r["end"] + moved} if "end" in r else {}),
                 "source": "DEFAULT"} for r in ghidra.functions(m.path)]
        made = {f"{m.name}/{u}": fns for u, fns in _program_units(m.pe, rows, log).items()}
        groups.update(made)
        starts = {f.addr for fns in made.values() for f in fns}
        named = 0
        for addr, name in sorted(exports(m.pe).items()):
            # a C name another program exports too (an engine's and a game's DLL both export their interface) gets
            # the DLL's name in front; a method's other overloads stay by address
            real_name = next((n for n in (name, *([f"{m.name}_{name}"] if "::" not in name else [])) if n not in taken),
                             None)
            if real_name is not None:
                real[f"func_{addr:08X}" if addr in starts else f"D_{addr:08X}"] = real_name
                taken.add(real_name)
                named += addr in starts
        version_of = m.built.pack_version
        if version_of != compiler:  # a DLL built by another Visual C++ than the program builds with its own
            if (toolchains.path(compiler_pack(version_of)) / version_of).is_dir():
                own.update({f"src/auto/{u}.cpp": version_of for u in made})
            else:
                log(f"{m.rel} was built with {m.built.compiler}: its files build with Visual C++ {compiler} until "
                    f"{toolchains.get(compiler_pack(version_of)).name} is installed (Setup), then with try_compiler")
        for why in m.built.cannot_match():
            log(f"what can't match in {m.rel}: {why}")
        log(f"{m.rel}: {len(starts)} functions, {named} named by its exports")

    shutil.copytree(TEMPLATE, root)
    (root / "orig").mkdir()
    for source, dest in ((game, root / "orig" / f"{version}.exe"), *((m.path, root / "orig" / m.rel) for m in modules)):
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.hardlink_to(source)
        except OSError:
            shutil.copyfile(source, dest)
    (root / "include").mkdir()
    (root / "include" / "common.h").write_text(TYPES_H, encoding="utf-8")
    cfg = root / "tools" / "configure.py"
    text = cfg.read_text(encoding="utf-8").replace(f'MSVC_VERSION = "{MSVC_DEFAULT}"', f'MSVC_VERSION = "{compiler}"')
    if pack != "msvc":  # /GX is /EHsc from 2005 on, and saying /GX warns on every file
        text = text.replace('"/GX"]  # /GX:', '"/EHsc"]  # /EHsc (/GX before 2005):')
    cfg.write_text(text, encoding="utf-8")
    conf = root / "config" / version
    conf.mkdir(parents=True)
    (conf / "functions.json").write_text(json.dumps({
        "exe": f"orig/{version}.exe", "sha1": info["sha1"], "image_base": pe.image_base,
        **({"modules": [{"name": m.name, "exe": f"orig/{m.rel}", "sha1": hashlib.sha1(m.path.read_bytes()).hexdigest(),
                         "image_base": m.pe.image_base, "preferred_base": m.preferred} for m in modules]}
           if modules else {}),
        "units": {u: [_entry(pes[unit_module(u)], f) for f in fns] for u, fns in groups.items()},
        **({"names": dict(sorted(real.items()))} if real else {}),
    }, indent=1), encoding="utf-8")
    _write_compilers(compilers_file(root, version), own)  # (the DLLs built by another Visual C++; the rest: its own)
    if stubs := mark_exception_stubs(root, version):
        log(f"{stubs} of them are exception-handling code Visual C++ makes from the function they belong to: set aside")
    for u, fns in groups.items():
        src = root / "src" / "auto" / f"{u}.cpp"  # C++, as PC games of the time were written (C compiles too)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"/* {len(fns)} function{'s' if len(fns) != 1 else ''} from 0x{fns[0].addr:08X} to "
                       f"0x{fns[-1].addr + fns[-1].size:08X}, found by Ghidra. */\n#include \"common.h\"\n",
                       encoding="utf-8")
    log(f"{len(groups)} source files for agents to write into"
        + (f" (each DLL's in src/auto/<its name>/: {', '.join(m.name for m in modules)})" if modules else ""))

    setup = [[sys.executable, "tools/configure.py", version, *args], ["ninja", "prepare"]]
    for cmd in setup:
        _run(cmd, root, "the first build", log)
    if not (root / "objdiff.json").exists():
        raise ForkError("the first build finished without writing objdiff.json")
    rebuild = check_code(root, version, log=log)

    (root / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "pc", "version": version, "compiler": f"MSVC/{compiler}",
        "analysis": 4,  # (WindowsPC.ANALYSIS: nothing to repair, its stubs set aside, its pieces joined)
        "tools": tools_version(),
        "game": {"what": "your copy of the game's program (.exe)", "path": f"orig/{version}.exe", "sha1": info["sha1"]},
        "setup": [["python", "tools/configure.py", version, *args], ["ninja", "prepare"]],
        "rebuild": rebuild,
    }, indent=2), encoding="utf-8")
    (root / ".gitignore").write_text("# made from your copy of the game, or by the build\norig/\nbuild/\nbuild.ninja\n"
                                     ".ninja_*\nobjdiff.json\n__pycache__/\n.decomp-wtf/\n", encoding="utf-8")
    (root / "README.md").write_text(f"""# {info['title']} decompilation

Started from scratch with [decompdecomp](https://github.com/n-delic/decompdecomp): the game's functions, found by Ghidra, are cut out of your
copy of its program and compared with what `src/` compiles to, function by function, with objdiff.

1. Put your own copy of the game's program here as `orig/{version}.exe` (never commit it).
2. `python tools/configure.py {version} --msvc <Visual C++ folder> [--wibo <wibo>]`, then `ninja`.
3. Decompiled code goes in `src/`; `objdiff.json` shows each function's progress in [objdiff](https://github.com/encounter/objdiff).

Compiler: Visual C++ {compiler} (change `MSVC_VERSION` and `CL_FLAGS` in tools/configure.py if the game was built
with another version or other flags; `config/{version}/file_compilers.json` names the files built with another
version). `config/{version}/functions.json` holds what the analysis found: every function's address, size, symbol
and relocations, by source file.
""" + (f"""
The game's own DLLs are programs of this project too, each with its source files in `src/auto/<its name>/`:
{chr(10).join(f"- `orig/{m.rel}` ({m.name}, at 0x{m.pe.image_base:08X})" for m in modules)}

Put your copies of them there too. A DLL at another address than its own (the program or a DLL before it has that
one) is moved by its base relocations at build time, as Windows moves it.
""" if modules else ""), encoding="utf-8")
    if shutil.which("git"):
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=decompdecomp", "-c", "user.email=noreply@decompdecomp.invalid", "commit", "-q", "-m",
                     f"Start the {info['title']} decompilation (decompdecomp, Ghidra)"]):
            subprocess.run(cmd, cwd=root, capture_output=True, creationflags=NO_WINDOW)
    return {"platform": "pc", "id": version, "title": info["title"], "compiler": f"MSVC/{compiler}",
            "sources": len(groups), "sha1": info["sha1"]}
