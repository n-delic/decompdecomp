#!/usr/bin/env python3
"""Rebuild the game's program from this project with the game's own linker, and check it against your copy byte for
byte. The matched functions come from what src/ compiles to; everything else from your copy of the game.

    python tools/export.py config/<version>/functions.json OUT_DIR --link LINK [--objdiff objdiff.json] [--xdk DIR]

On Linux and macOS the linker (and imagebld) run through wine, as tools/ltcg.py's harness links do. An Xbox game's
project holds its XBE inside a Windows view of it (orig/): the XBE is what's rebuilt, written as default.xbe.

How the program is cut up (every byte of it ends up in an object the linker places):
- every function in functions.json is a section of its own, as tools/delink.py cuts it: calls, jumps and data
  addresses are relocations, so the linker works each of them out again;
- a function src/ compiles to the same bytes as the original (the same instructions, relocations at the same
  places) is taken from the compiled object instead: its code, as the compiler (or, for a game built with link-time
  code generation, the unit's harness link) made it. Its references keep their names when they name something of
  the game (func_<address>, D_<address>, the original's symbols); what the compiler made itself (floats, strings,
  jump table labels) refers to the original's data at the same instruction, so nothing is duplicated and the data
  stays where it was;
- everything between and around the functions (data sections, padding, code nobody found) is carried as it is,
  with the symbols the functions refer to defined at their addresses; addresses inside it stay as they were
  (shortcut: the data has no relocations of its own, so the rebuilt program can't move yet; a modding-ready export
  needs the data's pointers as relocations too).

Every piece is a section aligned to one byte, in the original's order, so the linker lays them out at the original's
addresses. An Xbox game's kernel imports come from the XDK's xboxkrnl.lib in the original's order; the linker writes
a PE and the XDK's imagebld makes the XBE of it. The headers are the one part that can't be rebuilt (an Xbox game's
certificate and Microsoft's RSA signature; timestamps), so once every section is identical to the original's, the
original's headers go on top, and the result's SHA-1 is compared with your copy's.

Writes OUT_DIR/<the program's name> and OUT_DIR/export.json (what was rebuilt from what, and every difference).
Exits with 0 when the program is identical to your copy. Standard library only. Made by decompdecomp.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DIR32, REL32 = 0x06, 0x14
CNT_UNINIT = 0x80
CNT_CODE, CNT_INIT, MEM_DISCARDABLE, MEM_EXEC, MEM_READ, MEM_WRITE = \
    0x20, 0x40, 0x02000000, 0x20000000, 0x40000000, 0x80000000
ALIGN_1, ALIGN_4 = 0x00100000, 0x00300000
XBE_KEYS = {"retail": (0xA8FC57AB, 0x5B6D40B6), "debug": (0x94859D4B, 0xEFB1F152)}
# XBE sections imagebld adds itself: the title and save images (/TITLEIMAGE, /DEFAULTSAVEIMAGE) and the table of the
# XDK's library functions it finds in the program (.XTLID), given here as the original's bytes (/INSERTFILE)
XBE_INSERTED = ("$$XTIMAGE", "$$XSIMAGE", ".XTLID")
ADDRESS_NAME = re.compile(r"^[_@]?(?:D|func)_([0-9A-Fa-f]{8})(?:@\d+)?$")  # what tools/names.py names them
MAX_SECTIONS = 30000  # sections per object: COFF numbers them in 16 bits (an Xbox game's .text alone is ~14,000 pieces)


# ---------------------------------------------------------------------------------------------------- programs


@dataclass
class Section:
    name: str
    va: int
    vsize: int
    offset: int
    size: int  # its bytes in the file
    writable: bool

    @property
    def length(self) -> int:
        """Its length in memory, as the linker must make it (the file holds `size` bytes of it, the rest is zeros)."""
        return max(self.vsize, self.size) if self.vsize else self.size


@dataclass
class Program:
    kind: str  # "xbe" or "pe"
    data: bytes
    base: int
    entry: int
    sections: list[Section]
    tls: int = 0  # the TLS directory's address (0: none)
    kernel_thunk: int = 0  # Xbox: the kernel thunk table
    pe: dict = field(default_factory=dict)  # PC: optional header fields the link reproduces

    def section_at(self, addr: int) -> Section | None:
        return next((s for s in self.sections if s.va <= addr < s.va + s.length), None)

    def u32(self, addr: int) -> int:
        s = self.section_at(addr)
        if s is None or addr - s.va + 4 > s.size:
            return 0
        return struct.unpack_from("<I", self.data, s.offset + addr - s.va)[0]

    def contents(self, s: Section) -> bytes:
        return self.data[s.offset:s.offset + min(s.size, s.length)].ljust(s.length, b"\0")

    @property
    def first_raw(self) -> int:
        """Where the headers end in the file: the first section's bytes."""
        return min(s.offset for s in self.sections if s.size)


def u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def read_xbe(data: bytes) -> Program:
    base = u32(data, 0x104)
    count, headers = u32(data, 0x11C), u32(data, 0x120)
    sections = []
    for i in range(count):
        flags, va, vsize, offset, size, name_at = struct.unpack_from("<6I", data, headers - base + 56 * i)
        name = data[name_at - base:data.index(b"\0", name_at - base)].decode("latin-1")
        sections.append(Section(name, va, vsize, offset, size, bool(flags & 1)))
    for entry_key, thunk_key in XBE_KEYS.values():
        entry, thunk = u32(data, 0x128) ^ entry_key, u32(data, 0x158) ^ thunk_key
        prog = Program("xbe", data, base, entry, sections, tls=u32(data, 0x12C), kernel_thunk=thunk)
        if prog.section_at(entry) and prog.section_at(thunk):
            return prog
    sys.exit("this XBE's entry point doesn't decode with the retail or debug keys")


def read_pe(data: bytes) -> Program:
    at = u32(data, 0x3C)
    count, opt_size = struct.unpack_from("<H", data, at + 6)[0], struct.unpack_from("<H", data, at + 20)[0]
    opt = at + 24
    base = u32(data, opt + 28)
    pe = {"section_alignment": u32(data, opt + 32), "file_alignment": u32(data, opt + 36),
          "subsystem": struct.unpack_from("<H", data, opt + 68)[0],
          "stack": (u32(data, opt + 72), u32(data, opt + 76)), "heap": (u32(data, opt + 80), u32(data, opt + 84)),
          "dirs": [struct.unpack_from("<II", data, opt + 96 + 8 * i) for i in range(u32(data, opt + 92))]}
    sections = []
    for i in range(count):
        h = opt + opt_size + 40 * i
        name = data[h:h + 8].rstrip(b"\0").decode("latin-1")
        vsize, rva, size, offset = struct.unpack_from("<4I", data, h + 8)
        # a PE section's file data is padded to the file alignment: its contents are its first vsize bytes
        sections.append(Section(name, base + rva, vsize, offset, min(size, vsize) if vsize else size,
                                bool(u32(data, h + 36) & MEM_WRITE)))
    tls = pe["dirs"][9][0] if len(pe["dirs"]) > 9 and pe["dirs"][9][0] else 0
    return Program("pe", data, base, base + u32(data, opt + 16), sections, tls=base + tls if tls else 0, pe=pe)


XBE_MARK = 0x28  # where an Xbox game's PE view says where its XBE is (decompdecomp's consoles/xbox/scratch.py pe_view)


def inner_xbe(data: bytes) -> bytes | None:
    """The XBE an Xbox game's PE view carries (the project's orig/ program), or None for any other program."""
    if data[:2] != b"MZ" or data[XBE_MARK:XBE_MARK + 4] != b"XBEH":
        return None
    at, size = struct.unpack_from("<II", data, XBE_MARK + 4)
    return data[at:at + size]


def read_program(data: bytes) -> Program:
    if data[:4] == b"XBEH":
        return read_xbe(data)
    if data[:2] == b"MZ":
        return read_pe(data)
    sys.exit("the game's program is neither an XBE nor a PE")


def kernel_slots(prog: Program) -> list[int]:
    """An Xbox game's kernel thunk table: one `0x80000000 | ordinal` word per import, then a zero."""
    out = []
    while (word := prog.u32(prog.kernel_thunk + 4 * len(out))) & 0x80000000:
        out.append(word & 0x7FFFFFFF)
    return out


# ---------------------------------------------------------------------------------------------------- COFF


class Coff:
    """A COFF object written from scratch: sections, their relocations, symbols."""

    def __init__(self):
        self.sections: list[dict] = []
        self.symbols: list[tuple[str, int, int, int]] = []  # (name, value, section number, storage class)
        self.index: dict[str, int] = {}
        self.functions: set[str] = set()

    def section(self, name: str, data: bytes, chars: int) -> int:
        """A section; with CNT_UNINIT, `data` only gives its length (zeros the file doesn't hold, like .bss)."""
        self.sections.append({"name": name, "data": bytearray(data), "chars": chars, "relocs": []})
        return len(self.sections)

    def symbol(self, name: str, section: int, value: int, storage: int = 2) -> int:
        """The symbol's index; a definition (section > 0) of a name referred to before takes the reference's place."""
        if storage == 2 and name in self.index:
            k = self.index[name]
            if section and not self.symbols[k][2]:
                self.symbols[k] = (name, value, section, storage)
            return k
        self.symbols.append((name, value, section, storage))
        if storage == 2:
            self.index[name] = len(self.symbols) - 1
        return len(self.symbols) - 1

    def reloc(self, section: int, offset: int, symbol: int, kind: int) -> None:
        self.sections[section - 1]["relocs"].append((offset, symbol, kind))

    def bytes(self) -> bytes:
        strings = bytearray(4)

        def name_field(raw: bytes) -> bytes:
            if len(raw) <= 8:
                return raw.ljust(8, b"\0")
            at = len(strings)
            strings.extend(raw + b"\0")
            return struct.pack("<II", 0, at)

        at = 20 + 40 * len(self.sections)
        headers, body = bytearray(), bytearray()
        for s in self.sections:
            relocs = b"".join(struct.pack("<IIH", o, sym, k) for o, sym, k in sorted(s["relocs"]))
            if len(s["relocs"]) > 0xFFFF:
                sys.exit(f"a piece of the program has {len(s['relocs'])} relocations, more than one section holds")
            raw = s["name"].encode()
            if len(raw) > 8:  # a long section name: "/<offset>" into the string table
                field8 = f"/{len(strings)}".encode().ljust(8, b"\0")
                strings.extend(raw + b"\0")
            else:
                field8 = raw.ljust(8, b"\0")
            raw = b"" if s["chars"] & CNT_UNINIT else bytes(s["data"])
            headers += struct.pack("<8sIIIIIIHHI", field8, 0, 0, len(s["data"]), at if raw else 0,
                                   at + len(raw) if relocs else 0, 0, len(s["relocs"]), 0, s["chars"])
            body += raw + relocs
            at += len(raw) + len(relocs)
        table = bytearray()
        for name, value, section, storage in self.symbols:
            kind = 0x20 if name in self.functions else 0  # a function, as compilers mark them (the map's "f")
            table += struct.pack("<8sIHHBB", name_field(name.encode()), value & 0xFFFFFFFF, section, kind, storage, 0)
        struct.pack_into("<I", strings, 0, len(strings))
        return struct.pack("<HHIIIHH", 0x14C, len(self.sections), 0, at, len(self.symbols), 0, 0) + headers + body + \
            table + strings


@dataclass
class ObjSection:
    name: str
    data: bytes
    relocs: list[tuple[int, int, int]]  # (offset, symbol index, type)


@dataclass
class ObjSymbol:
    name: str
    value: int
    section: int  # 1-based; 0 undefined, -1 absolute
    storage: int


def read_coff(data: bytes) -> tuple[list[ObjSection], dict[int, ObjSymbol]]:
    """A compiled object's sections and symbols (by their index in its symbol table)."""
    count, sym_at, nsyms = struct.unpack_from("<H", data, 2)[0], u32(data, 8), u32(data, 12)
    opt = struct.unpack_from("<H", data, 16)[0]
    strings = sym_at + 18 * nsyms

    def name(raw: bytes) -> str:
        if raw[:4] == b"\0\0\0\0":
            at = strings + u32(raw, 4)
            return data[at:data.index(b"\0", at)].decode("latin-1")
        return raw.rstrip(b"\0").decode("latin-1")

    sections = []
    for i in range(count):
        h = 20 + opt + 40 * i
        raw = data[h:h + 8]
        sname = name(raw) if not raw.startswith(b"/") else \
            data[strings + int(raw[1:].rstrip(b"\0")):].split(b"\0", 1)[0].decode("latin-1")
        size, at, rel_at = u32(data, h + 16), u32(data, h + 20), u32(data, h + 24)
        nrel = struct.unpack_from("<H", data, h + 32)[0]
        relocs = [struct.unpack_from("<IIH", data, rel_at + 10 * k) for k in range(nrel)]
        sections.append(ObjSection(sname, data[at:at + size] if at else bytes(size), relocs))
    symbols, i = {}, 0
    while i < nsyms:
        at = sym_at + 18 * i
        value, section, _, storage, aux = struct.unpack_from("<IhHBB", data, at + 8)
        symbols[i] = ObjSymbol(name(data[at:at + 8]), value, section, storage)
        i += 1 + aux
    return sections, symbols


def import_names(lib: bytes) -> dict[int, str]:
    """Ordinal -> import symbol (`__imp__NtClose@4`), from an import library's short import members."""
    out: dict[int, str] = {}
    at = 8
    while at + 60 <= len(lib):
        size = int(lib[at + 48:at + 58].decode().strip() or 0)
        body = lib[at + 60:at + 60 + size]
        if body[:4] == b"\0\0\xff\xff" and len(body) > 20:
            ordinal, kind = struct.unpack_from("<HH", body, 16)
            if (kind >> 2) & 7 == 0:  # imported by ordinal
                out[ordinal] = "__imp_" + body[20:body.index(b"\0", 20)].decode("latin-1")
        at += 60 + size + (size & 1)
    return out


# ---------------------------------------------------------------------------------------------------- the pieces


def functions_of(info: dict) -> list[dict]:
    """Every function, by address. A relocation listed twice at one place (a jump table two jumps go through, in
    projects made before decompdecomp listed each once) is one: the linker would add the address twice."""
    fns = []
    for unit, functions in info["units"].items():
        for fn in functions:
            once = {}
            for r in fn["relocs"]:
                once.setdefault(r[0], r)
            fns.append(dict(fn, unit=unit, relocs=sorted(once.values(), key=lambda r: r[0])))
    return sorted(fns, key=lambda f: f["addr"])


def symbol_addresses(prog: Program, fns: list[dict]) -> dict[str, int]:
    """Every name the functions refer to, at the address the original's code shows for it: a DIR32 field holds the
    address (plus the reference's addend), a REL32 field its distance from the next instruction."""
    out = {fn["name"]: fn["addr"] for fn in fns}
    for fn in fns:
        for off, kind, name, addend in fn["relocs"]:
            if name is None:
                continue
            value = prog.u32(fn["addr"] + off)
            target = (value if kind == DIR32 else fn["addr"] + off + 4 + value) - addend
            out.setdefault(name, target & 0xFFFFFFFF)
    return out


def section_chars(prog: Program, s: Section, code_group: bool) -> int:
    """PE characteristics for a piece of section `s`. The linker lays out the code group first, then the data
    (each in the order the objects give it), and imagebld marks every XBE section executable, so the original's
    order says which group a section was in: everything before the first data section is code. .rdata must be what
    the linker makes its own .rdata (an Xbox game's kernel thunk table), so the two are one section."""
    if s.name == ".rdata":
        return CNT_INIT | MEM_READ | ALIGN_1
    chars = MEM_READ | ALIGN_1 | ((CNT_CODE | MEM_EXEC) if code_group else CNT_INIT)
    return chars | (MEM_WRITE if s.writable else 0)


def code_group(prog: Program) -> set[str]:
    names = [s.name for s in prog.sections]
    data = next((i for i, n in enumerate(names) if n in (".rdata", ".data", ".idata", ".bss") or n.endswith("_RD")),
                len(names))
    return set(names[:data])


@dataclass
class Piece:
    start: int
    end: int
    fn: dict | None = None  # the function it is, or None: bytes carried as they are
    compiled: tuple | None = None  # (ObjSection, symbols, section number): the function as src/ compiled it


def pieces(prog: Program, s: Section, fns: list[dict], skip: tuple[int, int] | None) -> list[Piece]:
    """Section `s` cut into functions and the bytes between them. `skip`: a range the linker writes itself (the
    kernel thunk table)."""
    lo, hi = s.va, s.va + s.length
    if skip and skip[0] == lo:
        lo = skip[1]
    out, at = [], lo
    for fn in fns:
        if not lo <= fn["addr"] < hi:
            continue
        if fn["addr"] > at:
            out.append(Piece(at, fn["addr"]))
        if fn["addr"] + fn["size"] > hi:  # the last function of a PE section, with the file's padding after it
            fn = dict(fn, size=hi - fn["addr"], relocs=[r for r in fn["relocs"] if r[0] + 4 <= hi - fn["addr"]])
        out.append(Piece(fn["addr"], fn["addr"] + fn["size"], fn))
        at = fn["addr"] + fn["size"]
    if at < hi:
        out.append(Piece(at, hi))
    if prog.kind == "pe":  # a PE section's bytes past its file data are .bss: a piece of their own, left out of the file
        raw_end = s.va + s.size
        for k, p in enumerate(out):
            if p.fn is None and p.start < raw_end < p.end:
                out[k:k + 1] = [Piece(p.start, raw_end), Piece(raw_end, p.end)]
                break
    return out


# ---------------------------------------------------------------------------------------------------- compiled code


def compiled_functions(objdiff: Path | None) -> dict[str, tuple]:
    """Symbol -> (section, symbols, section number) for every function in the objects src/ compiles to (objdiff.json's
    base objects: compiled with the game's compiler, or cut out of a unit's harness link)."""
    out: dict[str, tuple] = {}
    if objdiff is None or not objdiff.exists():
        return out
    for unit in json.loads(objdiff.read_text(encoding="utf-8")).get("units", []):
        path = Path(unit.get("base_path") or "")
        if not path.is_file():
            continue
        sections, symbols = read_coff(path.read_bytes())
        refused = judged_not_matching(path)
        for sym in symbols.values():
            if sym.name in refused:
                continue
            if sym.storage == 2 and sym.section > 0 and sym.value == 0 and \
                    sections[sym.section - 1].name.startswith(".text"):
                out.setdefault(sym.name, (sections[sym.section - 1], symbols, sym.section))
    return out


def judged_not_matching(obj: Path) -> set[str]:
    """Functions of an LTCG unit's object that its address mapping or data check fails (dll2coff's <obj>.ltcg.json):
    their bytes and relocations can agree with the original's while one refers to another function or global (a
    callback naming the wrong function), which only those checks see. The judge doesn't count them as matched, so
    the export keeps the original's code for them too."""
    try:
        side = json.loads(obj.with_suffix(".ltcg.json").read_text(encoding="utf-8")).get("functions") or {}
    except (OSError, ValueError):
        return set()
    return {name for name, f in side.items() if f.get("conflicts") or f.get("data")}


def same_code(prog: Program, fn: dict, sec: ObjSection) -> bool:
    """The compiled function is the original's: relocations at the same places and of the same kinds, and the same
    bytes everywhere else. The original's extent may end in padding the compiled section doesn't hold (zeros, int3s
    or nops up to the next function: compiled_piece puts it back)."""
    tail = fn_bytes(prog, fn)[len(sec.data):]
    if len(sec.data) > fn["size"] or tail.strip(b"\0\xcc\x90"):
        return False
    if sorted((o, k) for o, _, k in sec.relocs) != sorted((o, k) for o, k, _, _ in fn["relocs"]):
        return False
    original = bytearray(fn_bytes(prog, fn)[:len(sec.data)])
    ours = bytearray(sec.data)
    for o, _, _ in sec.relocs:
        original[o:o + 4] = ours[o:o + 4] = b"\0\0\0\0"
    return original == ours


def fn_bytes(prog: Program, fn: dict) -> bytes:
    """A function's bytes, by its address: functions.json's file offsets are the project's program's, and an Xbox
    game's is the Windows view of the XBE this rebuilds, where the same address sits at another offset."""
    s = prog.section_at(fn["addr"])
    if s is None:
        return prog.data[fn["offset"]:fn["offset"] + fn["size"]]
    at = s.offset + fn["addr"] - s.va
    return prog.data[at:at + fn["size"]]


# ---------------------------------------------------------------------------------------------------- the export


class Export:
    def __init__(self, prog: Program, info: dict, compiled: dict[str, tuple], imports: dict[int, str] | None):
        self.prog, self.info = prog, info
        self.fns = functions_of(info)
        self.function_names = {fn["name"] for fn in self.fns}
        self.addresses = symbol_addresses(prog, self.fns)
        self.compiled = compiled
        self.imports = imports  # Xbox: ordinal -> import symbol, when xboxkrnl.lib makes the kernel thunk table
        self.report = {"functions": len(self.fns), "compiled": [], "kept_original": [], "paired": 0,
                       "absolute": [], "conflicts": []}
        self.thunk: tuple[int, int] | None = None
        self.rename: dict[str, str] = {}
        if prog.kind == "xbe" and imports is not None:
            slots = kernel_slots(prog)
            rdata = prog.section_at(prog.kernel_thunk)
            if rdata is not None and rdata.name == ".rdata" and rdata.va == prog.kernel_thunk:
                self.thunk = (prog.kernel_thunk, prog.kernel_thunk + 4 * (len(slots) + 1))
                self.slots = slots
                missing = [o for o in slots if o not in imports]
                if missing:
                    sys.exit(f"xboxkrnl.lib has no import for the kernel ordinals {missing[:8]}: is it your XDK's?")
                # the import symbols are xboxkrnl.lib's: a name functions.json gives a slot (decorated another way,
                # say) refers to the library's symbol for it
                slot_names = {prog.kernel_thunk + 4 * i: imports[o] for i, o in enumerate(slots)}
                self.rename = {n: slot_names[a] for n, a in self.addresses.items() if a in slot_names}
                self.addresses.update({n: a for a, n in slot_names.items()})
        self.report["kernel_imports"] = "xboxkrnl.lib" if self.thunk else "the original's table"

    def defined_here(self, name: str) -> bool:
        return not (self.thunk and name.startswith("__imp_") and self.thunk[0] <= self.addresses[name] < self.thunk[1])

    def cut(self) -> tuple[list[tuple[Section, list[Piece]]], dict[tuple[int, int], list[tuple[str, int]]]]:
        """The sections the link rebuilds, cut into pieces, and each piece's symbols: every name at an address inside
        it (a name at a section's very end goes with its last piece). A name at no piece's address (in an XBE's
        headers, say: its certificate) is kept as the original's bytes where it's referred to (fixed_distance)."""
        cut = [(s, pieces(self.prog, s, self.fns, self.thunk)) for s in self.prog.sections
               if not (self.prog.kind == "xbe" and s.name in XBE_INSERTED)]
        starts = sorted((p.start, i, j) for i, (_, ps) in enumerate(cut) for j, p in enumerate(ps))
        keys = [k[0] for k in starts]
        where: dict[tuple[int, int], list[tuple[str, int]]] = {}
        self.absolute = set()
        for name, addr in sorted(self.addresses.items(), key=lambda kv: kv[1]):
            if not self.defined_here(name):
                continue
            k = bisect.bisect_right(keys, addr) - 1
            for kk in (k, k - 1):
                if kk < 0:
                    continue
                _, i, j = starts[kk]
                p = cut[i][1][j]
                if p.start <= addr < p.end or (addr == p.end and j == len(cut[i][1]) - 1):
                    where.setdefault((i, j), []).append((name, addr - p.start))
                    break
            else:
                self.absolute.add(name)
        self.report["absolute"] = sorted(self.absolute)
        return cut, where

    def objects(self, out: Path) -> list[Path]:
        """One object per section the link rebuilds, its pieces as sections of the same name in address order."""
        prog = self.prog
        groups = code_group(prog)
        cut, where = self.cut()
        paths = []
        entry_placed = tls_placed = False
        def write(obj: Coff, i: int, s: Section) -> None:
            if obj.sections:
                path = out / f"s{i:02d}_{s.name.strip('.$') or 'section'}_{len(paths):03d}.obj"
                path.write_bytes(obj.bytes())
                paths.append(path)

        for i, (s, ps) in enumerate(cut):
            obj = Coff()
            obj.functions = self.function_names
            chars = section_chars(prog, s, s.name in groups)
            for j, p in enumerate(ps):
                if len(obj.sections) == MAX_SECTIONS:  # the next object goes on where this one ends
                    write(obj, i, s)
                    obj = Coff()
                    obj.functions = self.function_names
                if p.fn is not None:
                    idx = self.function_piece(obj, s.name, chars, p)
                elif prog.kind == "pe" and p.start >= s.va + s.size:
                    idx = obj.section(s.name, bytes(p.end - p.start),
                                      (chars & ~(CNT_INIT | CNT_CODE | MEM_EXEC)) | CNT_UNINIT)
                else:
                    idx = obj.section(s.name, prog.contents(s)[p.start - s.va:p.end - s.va], chars)
                for name, value in where.get((i, j), []):
                    obj.symbol(name, idx, value)
                if p.start <= prog.entry < p.end:
                    obj.symbol("_export_entry", idx, prog.entry - p.start)
                    entry_placed = True
                if prog.tls and p.start <= prog.tls < p.end:
                    obj.symbol("__tls_used", idx, prog.tls - p.start)
                    tls_placed = True
            write(obj, i, s)
        if not entry_placed:
            sys.exit(f"the entry point 0x{prog.entry:08X} isn't in any section")
        if prog.tls and not tls_placed:
            sys.exit(f"the TLS directory 0x{prog.tls:08X} isn't in any section")
        return paths

    def function_piece(self, obj: Coff, sname: str, chars: int, p: Piece) -> int:
        fn = p.fn
        got = self.compiled.get(fn["name"])
        if got is not None and same_code(self.prog, fn, got[0]):
            idx = self.compiled_piece(obj, sname, chars, fn, *got)
            if idx is not None:
                self.report["compiled"].append(fn["name"])
                return idx
        elif got is not None:
            self.report["kept_original"].append(fn["name"])
        return self.delinked_piece(obj, sname, chars, fn)

    def delinked_piece(self, obj: Coff, sname: str, chars: int, fn: dict) -> int:
        """The function as tools/delink.py cuts it: its relocation fields hold the addend, the symbol the rest."""
        code = bytearray(fn_bytes(self.prog, fn))
        idx = obj.section(sname, b"", chars)
        for off, kind, name, addend in fn["relocs"]:
            if self.fixed_distance(name, kind):
                continue
            sym, inline = self.target(obj, idx, fn, name, addend)
            struct.pack_into("<i", code, off, inline)
            obj.reloc(idx, off, sym, kind)
        obj.sections[idx - 1]["data"] = code
        return idx

    def fixed_distance(self, name: str | None, kind: int) -> bool:
        """A reference to an address in no section: the headers (an XBE's certificate, which the program reads), or
        nowhere (code Ghidra took for a function, jumping into the void). The field keeps the original's bytes, with
        no relocation: the linker can't make a relative reference to an absolute symbol, and imagebld calls a map
        with absolute symbols in it corrupted (shortcut: right only while the code stays where it was)."""
        return name is not None and name in self.absolute

    def target(self, obj: Coff, idx: int, fn: dict, name: str | None, addend: int) -> tuple[int, int]:
        if name is None:  # an address inside the function itself (a jump table): a label
            return obj.symbol(f"$L{fn['addr'] + addend:08X}", idx, addend, 3), 0
        return obj.symbol(self.rename.get(name, name), 0, 0), addend

    def compiled_piece(self, obj: Coff, sname: str, chars: int, fn: dict, sec: ObjSection, symbols: dict,
                       number: int) -> int | None:
        """The function as src/ compiled it. Its references to the game keep their names, and must be to the address
        the original's code refers to there; what the compiler made itself refers to the original's data at the
        same instruction. None if a reference disagrees with the original (the function then stays the original's,
        and the report says where)."""
        original = {off: (kind, name, addend) for off, kind, name, addend in fn["relocs"]}
        code = bytearray(sec.data) + fn_bytes(self.prog, fn)[len(sec.data):]  # and the padding after it, if any
        plan = []
        for off, si, kind in sec.relocs:
            sym = symbols[si]
            inline = struct.unpack_from("<i", code, off)[0]
            if sym.section == number:  # into its own section (a jump table, its entries)
                plan.append((off, kind, None, sym.value + (inline if sym.storage == 3 and sym.value == 0 else 0),
                             inline if not (sym.storage == 3 and sym.value == 0) else 0))
                continue
            named = self.addresses.get(sym.name)
            if named is None and (m := ADDRESS_NAME.search(sym.name)):
                named = int(m.group(1), 16)  # D_<address> or func_<address>: the address the code means
            if named is not None:
                want = self.prog.u32(fn["addr"] + off)
                got = (named + inline) if kind == DIR32 else (named + inline - (fn["addr"] + off + 4))
                if got & 0xFFFFFFFF != want:
                    self.report["conflicts"].append(f"{fn['name']}+0x{off:X}: refers to {sym.name} "
                                                    f"(0x{named:08X}{inline:+d}), the original to 0x{want:08X}")
                    return None
                if sym.name in self.addresses:
                    plan.append((off, kind, sym.name, None, inline))
                    continue
                # the same address by a name nothing defines (data+4 written as its own D_): the original's reference
            kind0, name0, addend0 = original[off]
            self.report["paired"] += 1
            plan.append((off, kind, name0, addend0 if name0 is None else None, addend0 if name0 is not None else 0))
        idx = obj.section(sname, b"", chars)
        for off, kind, name, label, inline in plan:
            if self.fixed_distance(name, kind):
                code[off:off + 4] = fn_bytes(self.prog, fn)[off:off + 4]
                continue
            if name is None:
                s = obj.symbol(f"$L{fn['addr'] + label:08X}", idx, label, 3)
            else:
                s = obj.symbol(self.rename.get(name, name), 0, 0)
            struct.pack_into("<i", code, off, inline)
            obj.reloc(idx, off, s, kind)
        obj.sections[idx - 1]["data"] = code
        return idx

    def imports_object(self, out: Path) -> Path | None:
        """Xbox: the kernel imports referred to in the original table's order (the linker builds the table in the
        order of first reference), from a section the link drops; and the library version records, as the XDK
        libraries' .XBLD$V sections would give them to imagebld."""
        if self.prog.kind != "xbe":
            return None
        obj = Coff()
        if self.thunk:
            si = obj.section(".idref", bytes(4 * len(self.slots)), CNT_INIT | MEM_READ | MEM_DISCARDABLE | ALIGN_4)
            for i, o in enumerate(self.slots):
                obj.reloc(si, 4 * i, obj.symbol(self.imports[o], 0, 0), DIR32)
        data = self.prog.data
        count, at = u32(data, 0x160), u32(data, 0x164) - self.prog.base
        obj.section(".XBLD$V", data[at:at + 16 * count], CNT_INIT | MEM_READ | MEM_WRITE | MEM_DISCARDABLE | ALIGN_4)
        path = out / "imports.obj"
        path.write_bytes(obj.bytes())
        return path


# ---------------------------------------------------------------------------------------------------- link, check


def run(cmd: list[str], what: str, env: dict | None = None) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600, env=env,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        sys.exit(f"{what} took over an hour")
    text = (res.stdout + res.stderr).strip()
    if res.returncode:
        sys.exit(f"{what} failed:\n{subprocess.list2cmdline(cmd)}\n{text[-3000:]}")
    return text


def imagebld_args(prog: Program, exe: Path, xbe: Path, work: Path) -> list[str]:
    """imagebld options that give the original's certificate and stack, and the sections imagebld adds itself (the
    original's bytes)."""
    d, base = prog.data, prog.base
    c = u32(d, 0x118) - base
    title = d[c + 0xC:c + 0x5C].decode("utf-16-le").split("\0")[0]
    # /NOMAP: imagebld would find the XDK's library functions by name in the link's map to make .XTLID; the pieces'
    # names aren't the libraries', so the original's table goes in as it is (and a map it can't read is a warning,
    # which crashes imagebld under wibo)
    args = ["/NOLOGO", "/NOMAP", f"/IN:{exe}", f"/OUT:{xbe}", f"/STACK:0x{u32(d, 0x130):X}"]
    if u32(d, c + 8):  # a program built without a title ID or name (a test program) has none
        args.append(f"/TESTID:0x{u32(d, c + 8):08X}")
    if title:
        args.append(f"/TESTNAME:{title}")
    args += [f"/TESTREGION:0x{u32(d, c + 0xA0):X}", f"/TESTRATINGS:0x{u32(d, c + 0xA4):X}",
            f"/TESTMEDIATYPES:0x{u32(d, c + 0x9C):X}", f"/TESTVERSION:0x{u32(d, c + 0xAC):X}",
            f"/TESTLANKEY:{d[c + 0xB0:c + 0xC0].hex()}", f"/TESTSIGNKEY:{d[c + 0xC0:c + 0xD0].hex()}", "/NOLIBWARN"]
    if u32(d, 0x124) & 4:
        args.append("/LIMITMEM")
    for s in prog.sections:
        if s.name not in XBE_INSERTED:
            continue
        path = work / (s.name.strip("$.") + ".bin")
        path.write_bytes(d[s.offset:s.offset + s.size])
        if s.name == "$$XTIMAGE":
            args.append(f"/TITLEIMAGE:{path}")
        elif s.name == "$$XSIMAGE":
            args.append(f"/DEFAULTSAVEIMAGE:{path}")
        else:
            args.append(f"/INSERTFILE:{path},{s.name},RN")
    return args


def compare(orig: Program, built: Program, pieces_of) -> list[str]:
    """Every way the built program's sections differ from the original's (none: it's the same program)."""
    out = []
    if [s.name for s in built.sections] != [s.name for s in orig.sections]:
        return [f"sections {[s.name for s in built.sections]} instead of {[s.name for s in orig.sections]}"]
    for s, t in zip(orig.sections, built.sections):
        for k in ("va", "vsize", "offset", "size"):
            if getattr(s, k) != getattr(t, k):
                out.append(f"{s.name}: {k} 0x{getattr(t, k):X} instead of 0x{getattr(s, k):X}")
        a, b = orig.data[s.offset:s.offset + s.size], built.data[t.offset:t.offset + t.size]
        if a != b:
            at = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            out.append(f"{s.name}: differs from 0x{s.va + at:08X} ({pieces_of(s.va + at)})")
    if len(built.data) != len(orig.data):
        out.append(f"the file is {len(built.data)} bytes instead of {len(orig.data)}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Rebuild the game's program and check it byte for byte")
    p.add_argument("config", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--link", required=True, help="the game's linker (the one beside its compiler)")
    p.add_argument("--objdiff", type=Path, default=Path("objdiff.json"), help="where the compiled objects are")
    p.add_argument("--xdk", default="", help="an Xbox game: your XDK's xbox folder (lib/xboxkrnl.lib, bin/imagebld.exe)")
    p.add_argument("--delinked", action="store_true", help="rebuild from the original's code only (no src/)")
    a = p.parse_args()
    info = json.loads(a.config.read_text(encoding="utf-8"))
    if info.get("modules"):  # (their functions are the project's too: checking the program alone isn't the game)
        sys.exit("ninja export relinks the game's program only, and this game has DLLs of its own (functions.json's "
                 "modules), whose functions it can't check yet: it can't say the game is identical. `ninja rebuild` "
                 "rebuilds and checks the program and its DLLs.")
    data = Path(info["exe"]).read_bytes()
    if hashlib.sha1(data).hexdigest() != info["sha1"]:
        sys.exit(f"{info['exe']} isn't the copy this project was made from (its SHA-1 should be {info['sha1']})")
    name, expected = Path(info["exe"]).name, info["sha1"]
    if (xbe := inner_xbe(data)) is not None:  # an Xbox game: its XBE is rebuilt, and compared with the one inside
        data, name, expected = xbe, "default.xbe", hashlib.sha1(xbe).hexdigest()
    prog = read_program(data)
    wrap, env = [], None
    if sys.platform != "win32":  # Visual C++'s linker and imagebld need more of Windows than wibo has: wine runs them
        import compile as compiler_runner  # (tools/compile.py: how wine is found and started)

        wine, env = compiler_runner.wine_setup()
        wrap = [wine]
    a.out.mkdir(parents=True, exist_ok=True)
    for old in a.out.glob("*.obj"):
        old.unlink()

    imports = None
    if prog.kind == "xbe":
        if not a.xdk:
            sys.exit("an Xbox game is rebuilt with your XDK's xboxkrnl.lib and imagebld: pass --xdk")
        imports = import_names((Path(a.xdk) / "lib" / "xboxkrnl.lib").read_bytes())
    ex = Export(prog, info, {} if a.delinked else compiled_functions(a.objdiff), imports)
    objs = ex.objects(a.out)
    first = ex.imports_object(a.out)
    objs = ([first] if first else []) + objs

    exe = a.out / (Path(name).stem + ".exe")
    rsp = a.out / "link.rsp"
    # relative: through wine an absolute POSIX path would start with a slash, which the linker reads as an option
    libs = [os.path.relpath(Path(a.xdk) / "lib" / "xboxkrnl.lib")] if ex.thunk else []
    rsp.write_text("\n".join(f'"{o}"' for o in [*map(str, objs), *libs]) + "\n", encoding="utf-8")
    flags = ["/NOLOGO", "/MACHINE:I386", "/NODEFAULTLIB", "/ENTRY:export_entry", "/OPT:REF", "/INCREMENTAL:NO",
             f"/MAP:{exe.with_suffix('.map')}", f"/OUT:{exe}"]  # imagebld reads the map next to the PE
    if prog.kind == "xbe":
        flags.append("/SUBSYSTEM:XBOX")
    else:
        pe = prog.pe
        flags += [f"/SUBSYSTEM:{ {2: 'WINDOWS', 3: 'CONSOLE'}.get(pe['subsystem'], 'WINDOWS') }",
                  f"/BASE:0x{prog.base:X}", f"/ALIGN:{pe['section_alignment']}", f"/FILEALIGN:{pe['file_alignment']}",
                  f"/STACK:{pe['stack'][0]},{pe['stack'][1]}", f"/HEAP:{pe['heap'][0]},{pe['heap'][1]}", "/FIXED"]
    log = run([*wrap, a.link, *flags, f"@{rsp}"], "the link", env)
    built_path = exe
    if prog.kind == "xbe":
        xbe = a.out / (Path(name).stem + ".imagebld.xbe")
        imagebld = Path(a.xdk) / "bin" / "imagebld.exe"
        log += "\n" + run([*wrap, str(imagebld), *imagebld_args(prog, exe, xbe, a.out)], "imagebld", env)
        built_path = xbe
    built = read_program(built_path.read_bytes())

    owners = sorted((f["addr"], f["addr"] + f["size"], f["name"]) for f in ex.fns)

    def piece_of(addr: int) -> str:
        k =bisect.bisect_right(owners, (addr, 1 << 33)) - 1
        if k >= 0 and owners[k][0] <= addr < owners[k][1]:
            return f"in {owners[k][2]}" + (", compiled" if owners[k][2] in ex.report["compiled"] else "")
        return "in the bytes between functions"

    differences = compare(prog, built, piece_of)
    header = sum(x != y for x, y in zip(prog.data[:prog.first_raw], built.data[:prog.first_raw]))
    result = bytes(prog.data[:prog.first_raw]) + built.data[prog.first_raw:] if not differences else built.data
    final = a.out / name
    final.write_bytes(result)
    sha1 = hashlib.sha1(result).hexdigest()
    rep = dict(ex.report)
    rep.update(identical=sha1 == expected and not differences, sha1=sha1, expected=expected,
               path=str(final), differences=differences, header_bytes_rebuilt_differently=header,
               compiled_count=len(rep["compiled"]), link_log=log[-2000:])
    (a.out / "export.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"{len(rep['compiled'])} of {rep['functions']} functions from src/ ({rep['paired']} references to the "
          f"compiler's own data put on the original's), the rest from your copy")
    for line in rep["conflicts"][:20]:
        print("  kept the original's code:", line)
    for line in differences[:20]:
        print("  ", line)
    print(f"{final}: SHA-1 {sha1} " + ("identical to your copy" if rep["identical"] else f"(your copy: {expected})"))
    sys.exit(0 if rep["identical"] else 1)


if __name__ == "__main__":
    main()
