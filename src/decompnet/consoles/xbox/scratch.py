"""Start an original Xbox decompilation from scratch: the game's program (an XBE, or the default.xbe on its disc
image) read, then made into a Windows program decompdecomp's PC path reads as any other (pe_view), so its import,
analysis, delinker, rebuild check and every feature built on them work on Xbox games unchanged.

An XBE is a Windows-style x86 program built with Visual C++ from Microsoft's Xbox Development Kit (the XDK), in a
format of its own:

- the headers load at the base address (0x10000) and every pointer in them is an address, not a file offset;
- the entry point and the kernel thunk table's address are stored XORed with a key, one pair for retail images and
  another for debug ones;
- there's no import directory: the kernel thunk table is a zero-terminated list of `0x80000000 | ordinal` words,
  which is exactly how a PE's import address table imports by ordinal, so pe_view only adds a directory naming
  them (xboxkrnl.py);
- there's no Rich header either: the library version table names each XDK library the program linked (XAPILIB,
  D3D8, DSOUND...) and the XDK build it came from, which is what picks the compiler.

The PE view is the XBE file itself behind PE headers, each section at its own address, the XBE's headers a section
of their own at the base address (so the PE's image base is a page below it: nothing moves), and the kernel imports'
names in a section after the program. It's made on the user's computer, from their copy, into the project's orig/
(never committed), each time from the same bytes the same way: its SHA-1 says it's the same game.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ... import kits
from ...forks.project import ForkError
from . import xboxkrnl

MAGIC = b"XBEH"
# (entry point key, kernel thunk key) by kind of image: an XBE stores both addresses XORed with one pair
KEYS = {"retail": (0xA8FC57AB, 0x5B6D40B6), "debug": (0x94859D4B, 0xEFB1F152)}
BASE = 0x10000  # where every XBE loads
KIT = "xdk"  # the kit Xbox games take: the user's own XDK (kits/xdk.py)
HEAD_BYTES = 0x1000  # what tells an XBE (its image header)
IMAGE_HEADER = 0x178  # the image header's fields this reads, there since the earliest XDKs
SECTION_HEADER = 56
LIBRARY_VERSION = 16
EXECUTABLE = 0x4  # XBE section flag
MAX_SECTIONS = 256
MAX_KERNEL_IMPORTS = 1024
# What the XDK's release projects build with: /G6 for the Pentium III, /arch:SSE, /Gy (a section per function) and
# _XBOX, which the XDK's headers expect. The demo (projects/demo-xbox) is built with exactly these.
CL_FLAGS = ["/nologo", "/c", "/O2", "/G6", "/GF", "/Gy", "/arch:SSE", "/W3", "/D_XBOX"]


def cl_flags(compiler: str, flags: list[str] = CL_FLAGS) -> list[str]:
    """`flags` for a game's compiler: Visual C++ 7.0 and the early XDKs' builds of it have no /arch (7.1's)."""
    return flags if compiler == "7.1" else [f for f in flags if not f.startswith("/arch:")]
# Visual C++ by the XDK build that shipped it: XDK 5849 (2003 on) is Visual C++ 7.1 (13.10.3077, the msvc pack's
# 7.1); the XDKs before it were Visual C++ 7.0's (XDK 4627's is 13.00.9466, the pack's 7.0; the first XDKs, 3911 to
# 4361, had earlier 13.00 builds, of which 7.0 is the nearest decompdecomp has). Later builds than 5849 are 7.1's too.
FIRST_71 = 5233  # the first XDK whose compiler is Visual C++ 7.1
EXACT_70 = 4627  # the XDK whose compiler is the pack's 7.0 build
DEFAULT_COMPILER = "7.1"
# Data sections by name: the Xbox linker marks .rdata and .data executable too, so the flags don't tell code from data
_DATA_NAME = re.compile(r"(?i)^(\.r?data\w*|\.bss|\.tls|\.xtlid|\$\$x\w*|\w+_RD)$")
# Sections the XDK's big libraries link their code into (by the libraries' own names): Microsoft's code, set aside
# whole. Any section named after a library in the program's version table counts too.
# shortcut: XDK library code linked into .text (the C runtime, XAPI) stays open to agents: telling it from the game's
# needs the user's XDK libraries to match against (ROADMAP.md, Xbox).
LIBRARY_SECTIONS = frozenset({"XMV", "DSOUND", "WMADEC", "XONLINE", "XNET", "D3D", "D3DX", "XGRPH", "XPP", "DOLBY",
                              "XACTENG"})
REGIONS = {1: "North America", 2: "Japan", 4: "rest of the world", 0x80000000: "manufacturing"}


@dataclass
class XbeSection:
    name: str
    flags: int
    va: int
    vsize: int
    offset: int  # in the file
    size: int  # bytes in the file

    @property
    def code(self) -> bool:
        return bool(self.flags & EXECUTABLE) and not _DATA_NAME.match(self.name)


@dataclass
class Library:
    name: str  # "XAPILIB", "D3D8", "XBOXKRNL"...
    major: int
    minor: int
    build: int  # the XDK build: 5849 for the December 2003 XDK
    flags: int


@dataclass
class XBE:
    data: bytes
    base: int
    headers: int  # the headers' size, from the base address
    image_size: int
    timestamp: int
    entry: int
    kernel_thunk: int
    keys: str  # "retail" or "debug"
    sections: list[XbeSection]
    title_id: int = 0
    title: str = ""
    region: int = 0
    libraries: list[Library] = field(default_factory=list)
    imports: dict[int, int] = field(default_factory=dict)  # kernel thunk slot -> ordinal

    @property
    def title_code(self) -> str:
        """The title ID as Xbox people write it: publisher letters and number, 0x44510001 -> "DQ-001" (the demo's)."""
        a, b = chr(self.title_id >> 24), chr(self.title_id >> 16 & 0xFF)
        return f"{a}{b}-{self.title_id & 0xFFFF:03d}" if (a + b).isalnum() and (a + b).isascii() \
            else f"{self.title_id:08X}"


def is_xbe(head: bytes) -> bool:
    """An original Xbox program (its first 4 KiB are enough)."""
    return len(head) >= IMAGE_HEADER and head[:4] == MAGIC and struct.unpack_from("<I", head, 0x104)[0] & 0xFFF == 0


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def _at(data: bytes, base: int, addr: int, n: int, what: str) -> int:
    """The file offset of `n` header bytes at `addr` (headers load at the base address), or which field is outside."""
    at = addr - base
    if at < 0 or at + n > len(data):
        raise ForkError(f"this XBE is damaged or cut short: its {what} (0x{addr:08X}) isn't in the file")
    return at


def read_xbe(data: bytes) -> XBE:
    """The program's sections, entry point, kernel imports, certificate and library versions."""
    if not is_xbe(data[:HEAD_BYTES]):
        raise ForkError("this isn't an original Xbox program (XBE): Xbox games start from default.xbe or their disc "
                        "image")
    base, headers, image_size, _, timestamp, cert, count, table = struct.unpack_from("<8I", data, 0x104)
    entry_enc, thunk_enc = _u32(data, 0x128), _u32(data, 0x158)
    lib_count, libs_at = struct.unpack_from("<II", data, 0x160)
    if count > MAX_SECTIONS:
        raise ForkError(f"this XBE says it has {count} sections, more than any real one: it's damaged")
    sections = []
    at = _at(data, base, table, SECTION_HEADER * count, "section headers")
    for i in range(count):
        flags, va, vsize, offset, raw, name_at = struct.unpack_from("<6I", data, at + SECTION_HEADER * i)
        n = _at(data, base, name_at, 1, f"section {i}'s name")
        name = data[n:n + 64].split(b"\0", 1)[0].decode("latin-1")
        sections.append(XbeSection(name, flags, va, vsize, offset, min(raw, max(0, len(data) - offset))))

    def inside(addr: int, code: bool = False) -> bool:
        return any(s.va <= addr < s.va + max(s.vsize, s.size) and (s.code or not code) for s in sections)
    # a wrong key gives an address nowhere near the program, so only one pair fits
    keys = next(((kind, entry_enc ^ ek, thunk_enc ^ tk) for kind, (ek, tk) in KEYS.items()
                 if inside(entry_enc ^ ek, code=True) and inside(thunk_enc ^ tk)), None)
    if keys is None:
        raise ForkError("this XBE's entry point doesn't decode with the retail or debug keys: it may be from a "
                        "prototype kit decompdecomp doesn't know, or damaged")
    c = _at(data, base, cert, 0xA4, "certificate")
    libraries = []
    if lib_count:
        at = _at(data, base, libs_at, LIBRARY_VERSION * min(lib_count, MAX_SECTIONS), "library version table")
        for i in range(min(lib_count, MAX_SECTIONS)):
            raw = data[at + LIBRARY_VERSION * i:at + LIBRARY_VERSION * (i + 1)]
            libraries.append(Library(raw[:8].split(b"\0", 1)[0].decode("latin-1"), *struct.unpack_from("<4H", raw, 8)))
    xbe = XBE(data, base, headers, image_size, timestamp, keys[1], keys[2], keys[0], sections,
              title_id=_u32(data, c + 8),
              title=data[c + 0x0C:c + 0x5C].decode("utf-16-le", "replace").split("\0", 1)[0].strip(),
              region=_u32(data, c + 0xA0), libraries=libraries)
    xbe.imports = _kernel_imports(xbe)
    return xbe


def _read(xbe: XBE, addr: int, n: int) -> bytes:
    s = next((s for s in xbe.sections if s.va <= addr < s.va + s.size), None)
    return b"" if s is None else xbe.data[s.offset + addr - s.va:s.offset + min(addr - s.va + n, s.size)]


def _kernel_imports(xbe: XBE) -> dict[int, int]:
    """Kernel thunk slot -> ordinal: the table's words up to its zero (or anything that isn't an ordinal)."""
    out = {}
    for k in range(MAX_KERNEL_IMPORTS):
        word = _read(xbe, xbe.kernel_thunk + 4 * k, 4)
        if len(word) < 4 or not _u32(word, 0) & 0x80000000:
            break
        out[xbe.kernel_thunk + 4 * k] = _u32(word, 0) & 0x7FFFFFFF
    return out


def xdk_build(xbe: XBE) -> int | None:
    """The XDK build the program was made with: XAPILIB's (every game links it), else the build most libraries share."""
    builds = [lib.build for lib in xbe.libraries if lib.name == "XAPILIB"] or \
        [b for b, _ in Counter(lib.build for lib in xbe.libraries if lib.name != "XBOXKRNL").most_common(1)]
    return builds[0] if builds else None


MIDDLEWARE = re.compile(r"(?i)^BINK")  # RAD's Bink video: licensed middleware in sections of its own


def library_sections(xbe: XBE) -> set[str]:
    """The program's code sections that are XDK libraries outright: the known ones, and any named after a library
    in its version table (`XGRPH` is xgraphics, `D3D` is d3d8: the table and the section can differ, hence the list)."""
    libs = {lib.name.upper() for lib in xbe.libraries}
    return {s.name for s in xbe.sections if s.code and (s.name.upper() in LIBRARY_SECTIONS or s.name.upper() in libs
                                                        or MIDDLEWARE.match(s.name))}


# ---------------------------------------------------------------------------------------------------- the PE view

PE_HEADERS = 0x1000  # the PE's own headers, a page below the XBE's base address (where the image base goes)
FILE_ALIGN = 0x200
SECTION_ALIGN = 0x1000
CODE, DATA, BSS = 0x60000020, 0xC0000040, 0xC0000080  # section characteristics: code, data, uninitialised data
KERNEL_DLL = b"xboxkrnl.exe\0"
XBE_MARK = 0x28  # tools/rebuild.py reads the XBE's place in the view here


def pe_view(data: bytes) -> bytes:
    """The XBE as a 32-bit Windows program at the same addresses (the module docstring says why and how)."""
    xbe = read_xbe(data)
    image = xbe.base - PE_HEADERS
    rows = [("XBEH", xbe.base, xbe.headers, 0, min(xbe.headers, len(data)), DATA)]
    rows += [(s.name, s.va, max(s.vsize, s.size), s.offset, s.size, CODE if s.code else DATA if s.size else BSS)
             for s in xbe.sections]
    end = max(va + size for _, va, size, _, _, _ in rows)
    tail = _align(len(data), FILE_ALIGN)
    idata, idata_va = b"", _align(end, SECTION_ALIGN)
    if xbe.imports:
        idata = _import_directory(xbe, idata_va - image)
        rows.append((".idata", idata_va, len(idata), tail, len(idata), DATA))
        end = idata_va + len(idata)
    if 0x40 + 0xF8 + 40 * len(rows) > PE_HEADERS:
        raise ForkError(f"this XBE has {len(xbe.sections)} sections, more than decompdecomp reads yet")
    head = bytearray(PE_HEADERS)
    head[:2], head[0x3C:0x40] = b"MZ", struct.pack("<I", 0x40)
    # (in the DOS header's reserved words: where the XBE is, so the rebuild writes an XBE too: tools/rebuild.py)
    head[XBE_MARK:XBE_MARK + 12] = MAGIC + struct.pack("<II", PE_HEADERS, len(data))
    struct.pack_into("<4sHHIIIHH", head, 0x40, b"PE\0\0", 0x14C, len(rows), xbe.timestamp, 0, 0, 0xE0, 0x0103)
    opt = 0x58
    struct.pack_into("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", head, opt, 0x10B, 7, 10, 0, 0, 0, xbe.entry - image, 0, 0, image,
                     SECTION_ALIGN, FILE_ALIGN, 4, 0, 0, 0, 4, 0, 0, _align(end - image, SECTION_ALIGN), PE_HEADERS,
                     0, 3, 0, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    if xbe.imports:
        struct.pack_into("<II", head, opt + 0x68, idata_va - image, 40)
    for i, (name, va, size, offset, raw, flags) in enumerate(rows):
        struct.pack_into("<8sIIIIIIHHI", head, opt + 0xE0 + 40 * i, name.encode("latin-1")[:8], size, va - image,
                         raw, PE_HEADERS + offset if raw else 0, 0, 0, 0, 0, flags)
    return bytes(head) + data + bytes(tail - len(data)) + idata


def _align(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def _import_directory(xbe: XBE, rva: int) -> bytes:
    """The import directory of the kernel's exports (at `rva` in the PE view): one DLL, xboxkrnl.exe, whose import
    address table is the XBE's kernel thunk table as it is, and a lookup table naming each slot's function, so every
    reader names `call [slot]` as a Windows program's import (`__imp__NtClose`)."""
    slots = sorted(xbe.imports)
    lookup = rva + 40  # (the descriptor and its zero one)
    names = lookup + 4 * (len(slots) + 1)
    out, entries = bytearray(), []
    for slot in slots:
        entries.append(names + len(out))
        out += b"\0\0" + xboxkrnl.name(xbe.imports[slot]).encode() + b"\0"
        out += b"\0" * (len(out) & 1)
    dll = names + len(out)
    out += KERNEL_DLL
    head = struct.pack("<IIIII", lookup, 0, 0, dll, xbe.kernel_thunk - (xbe.base - PE_HEADERS)) + bytes(20)
    return head + struct.pack(f"<{len(slots) + 1}I", *entries, 0) + bytes(out)


# ---------------------------------------------------------------------------------------------------- the game


def program_bytes(path: Path) -> bytes:
    """The XBE's bytes, from the file itself or from default.xbe on a disc image."""
    from . import xdvdfs

    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
    if is_xbe(head):
        return path.read_bytes()
    if xdvdfs.is_xbox_disc(path):
        return xdvdfs.read_file(path, "default.xbe")
    raise ForkError("this isn't an original Xbox program (default.xbe) or disc image")


def _xdk_compiler(build: int | None) -> kits.OwnCompiler | None:
    """The Visual C++ of the user's copy of XDK `build` (of their XDK copies, the one that is that build)."""
    kit = kits.get(KIT)
    return next((c for at in kits.folders(KIT) if kit.build(at) == str(build) for c in kit.compilers(at)), None)


def game_info(path: Path) -> dict:
    """What PC's create reads of a game (consoles/pc/scratch.py game_info), from the XBE."""
    data = program_bytes(path)
    xbe = read_xbe(data)
    build = xdk_build(xbe)
    compiler = DEFAULT_COMPILER if build is None or build >= FIRST_71 else "7.0"
    if build is None:
        why = f"the program has no library version table to say which XDK built it: most were Visual C++ {compiler}"
    elif build >= FIRST_71 or build == EXACT_70:
        why = f"its libraries come from XDK {build}, which ships Visual C++ {compiler}"
    elif (own := _xdk_compiler(build)) is not None:  # (its own 7.0 build)
        compiler, why = own.version, f"its libraries come from XDK {build}: your XDK {build}'s own Visual C++ builds it"
    else:
        why = (f"its libraries come from XDK {build}, whose compiler is an early Visual C++ 7.0 build: the nearest "
               f"decompdecomp has is Visual C++ 7.0 (13.00.9466), so some functions may differ by an instruction; "
               f"set up your own XDK {build} in Setup to build with its own")
    regions = ", ".join(n for bit, n in REGIONS.items() if xbe.region & bit) or "unknown region"
    title = xbe.title or path.stem
    return {"id": re.sub(r"[^a-z0-9]+", "", title.lower()) or "game", "title": title, "compiler": compiler,
            "compiler_why": why, "cannot_match": [], "xdk_build": build,
            "summary": f"an Xbox program ({xbe.title_code}, {regions}, {xbe.keys} keys, "
                       f"XDK {build or 'unknown'}, {len(xbe.sections)} sections, {len(xbe.imports)} kernel imports)",
            "sha1": hashlib.sha1(pe_view(data)).hexdigest(), "xbe_sha1": hashlib.sha1(data).hexdigest(),
            "library_sections": sorted(library_sections(xbe)), "libraries": [lib.name for lib in xbe.libraries]}


def set_aside_libraries(root: Path, info: dict) -> str | None:
    """Set aside a project's library code: the XDK's and middleware's own sections, and with the user's own XDK (set
    up in Setup) its functions linked into .text, found by its libraries' bytes (xdklib). Says what it set aside."""
    from . import xdklib

    said = []
    if n := set_aside_library_sections(root, info["id"], info["library_sections"]):
        said.append(f"{n} functions are library code in sections of its own ({', '.join(info['library_sections'])})")
    # the copy of the game's own XDK build, else the first (none: .text's stay open to agents)
    xdk = next((f for f in kits.folders(KIT) if kits.get(KIT).build(f) == str(info.get("xdk_build"))), kits.folder(KIT))
    lib = xdk and kits.find(xdk, xdklib.XAPILIB).parent
    if lib is not None and (n := xdklib.set_aside_xdk_functions(root, info["id"], lib, info["libraries"])):
        said.append(f"{n} functions in .text are the XDK's library code, as your XDK's libraries match them")
    return "; ".join(said) + ": set aside" if said else None


def set_aside_library_sections(root: Path, version: str, sections: list[str]) -> int:
    """Set aside every function of a project in the XDK's library sections (library_sections): each one's entry in
    functions.json says why (`generated`, as PC's exception-handling stubs). Returns how many it marked."""
    import json

    from ..pc import scratch as pc

    if not sections:
        return 0
    path = root / "config" / version / "functions.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    pe = pc.programs(root, info)[""]
    marked = 0
    for fn in (fn for fns in info["units"].values() for fn in fns if not fn.get("generated")):
        if (s := pe.section_at(fn["addr"])) is not None and s.name in sections:
            fn["generated"] = (f"RAD's Bink video library (its {s.name} section): licensed middleware, not the "
                               "game's" if MIDDLEWARE.match(s.name) else
                               f"the XDK's library code (its {s.name} section): Microsoft's, not the game's")
            marked += 1
    if marked:
        path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return marked
