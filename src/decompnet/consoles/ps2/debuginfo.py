"""What a PS2 program's debug information gives its import (#3: Champions of Norrath ships its debug symbols).

- The ELF symbol table (symtab.py): real names and exact sizes for functions and data, for splat to split by.
- .mdebug, the ECOFF symbol table Sony's EE-GCC and SN Systems' ProDG write with -g (mdebug.py), with STABS inside
  (stabs.py): the source files (they become the project's units), each function's prototype with its parameters'
  names and types and where its locals live, global variables' types, and every struct, union, enum, typedef and
  class with its fields' offsets, written as C for the shared header (cheader.py).
- DWARF (.debug from CodeWarrior, .debug_info from later GCCs) is recognised and said in the import log, not read.

The import calls read() before splitting, then gives each function its unit and the brief its debug lines.
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass, field
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from . import mdebug, stabs, symtab
from .cheader import Header

SMALL_DATA = (".sdata", ".sbss", ".scommon", ".lit4", ".lit8")  # where $gp-relative globals live
SMALL_DATUM_BYTES = 8  # -G8, PS2 games' setting: globals this small are read through $gp
GP_REACH = 0x8000  # a $gp-relative load reaches 32 KiB either side
DWARF_SECTIONS = {".debug": "DWARF 1", ".debug_info": "DWARF 2 or later"}


@dataclass
class DebugInfo:
    named: list[symtab.Named] = field(default_factory=list)
    units: dict[int, str] = field(default_factory=dict)  # a function's address -> its source file
    functions: dict[int, dict] = field(default_factory=dict)  # address -> what the brief says about it
    header: str = ""  # the game's types and globals, as C
    types: int = 0
    sources: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)  # debug formats found and not read

    def symbol_addrs(self) -> list[str]:
        return symtab.symbol_addrs(self.named)

    def summary(self) -> str:
        """What the import log says: "debug symbols: 3,412 functions named, 812 types, 64 source files"."""
        functions = sum(n.function for n in self.named)
        parts = [f"{functions:,} function{'s' if functions != 1 else ''} named"]
        if self.sources:
            parts += [f"{self.types:,} type{'s' if self.types != 1 else ''}",
                      f"{len(self.sources):,} source file{'s' if len(self.sources) != 1 else ''}"]
        parts += [f"{kind} not read yet" for kind in self.unread]
        return "debug symbols: " + ", ".join(parts)


def _short_names(names: list[str]) -> dict[str, str]:
    """Source paths without the folders they all share (C:\\dev\\game\\src\\ped.cpp -> src/ped.cpp)."""
    clean = {name: name.replace("\\", "/") for name in names}
    folders = [posixpath.dirname(path) for path in clean.values() if "/" in path]
    try:
        common = posixpath.commonpath(folders) if folders and len(folders) == len(clean) else ""
    except ValueError:  # absolute and relative paths mixed: nothing they all share
        common = ""
    if len(set(clean.values())) == 1:
        common = posixpath.dirname(next(iter(clean.values())))
    return {name: posixpath.relpath(path, common) if common else path.lstrip("/") for name, path in clean.items()}


def _gp(elf: ELFFile) -> int | None:
    symtab_section = elf.get_section_by_name(".symtab")
    found = symtab_section.get_symbol_by_name("_gp") if symtab_section is not None else None
    return found[0]["st_value"] if found else None


def _small(named: symtab.Named, gp: int | None) -> bool:
    """Whether the game reads a global through $gp: it's in a small-data section, or (a linker script that puts
    .sbss into .bss, like the demo's) small and within $gp's reach."""
    if named.section in SMALL_DATA:
        return True
    return gp is not None and named.size <= SMALL_DATUM_BYTES and abs(named.address - gp) < GP_REACH


def _dwarf(elf: ELFFile) -> list[str]:
    found = []
    for section, kind in DWARF_SECTIONS.items():
        if elf.get_section_by_name(section) is not None:
            found.append(f"{kind} ({section})")
    return found


def read(data: bytes, reserved: set[str] = frozenset()) -> DebugInfo | None:
    """The debug information of a PS2 program, or None when it has none. `reserved`: names the shared header
    already defines (see cheader.Header)."""
    try:
        elf = ELFFile(BytesIO(data))
        info = DebugInfo(named=symtab.read(elf), unread=_dwarf(elf))
        section = elf.get_section_by_name(".mdebug")
        files = mdebug.read(data, section["sh_offset"], section["sh_size"]) if section is not None else []
        gp = _gp(elf)
    except (ELFError, ValueError, KeyError):
        return None
    if files:
        try:
            _read_stabs(info, files, reserved, gp)
        except Exception:  # a heuristic over the game's own data: never stands between the user and the import
            logging.getLogger("decompdecomp").exception("reading the program's STABS failed")
    if not info.named and not info.sources and not info.unread:
        return None
    return info


def _read_stabs(info: DebugInfo, files: list[mdebug.SourceFile], reserved: set[str], gp: int | None) -> None:
    short = _short_names([f.name for f in files])
    sources = [stabs.read_file(short[f.name], f.stabs()) for f in files]
    for f in files:
        for _, address in f.procedures():
            info.units.setdefault(address, short[f.name])
    info.sources = sorted({short[f.name] for f in files if f.procedures()})
    header = Header(sources, reserved)
    info.types = len(header.entities)
    by_address = {n.address: n for n in info.named}
    by_name = {}
    for n in info.named:
        by_name.setdefault(n.name, n)
    variables, seen = [], set()
    for source in sources:
        for variable in source.globals:
            n = by_name.get(symtab.c_name(variable.name))
            if n is None or n.function or n.name in seen:
                continue
            seen.add(n.name)
            variables.append((n.name, variable.type, not _small(n, gp)))  # read through $gp: no extern (GP_NOTE)
        for function in source.functions:
            n = by_address.get(function.address)
            name = n.name if n is not None else symtab.c_name(function.name)
            info.functions[function.address] = {
                "source": source.name,
                "prototype": header.prototype(function, name),
                "params": [f"{p.name} in {p.where}" for p in function.params],
                "locals": [f"{header.variable(v)} ({v.where})" for v in function.locals],
            }
    info.header = header.text(variables)


def brief_lines(debug: dict) -> list[str]:
    """What a function's brief says from the game's debug information (kept in the function's meta at import)."""
    lines = ["", "### From the game's debug information",
             f"source file {debug['source']}; its types are in context.h already",
             f"prototype: {debug['prototype']};"]
    if debug.get("params"):
        lines.append("parameters live in: " + ", ".join(debug["params"]))
    if debug.get("locals"):
        lines.append("locals: " + "; ".join(debug["locals"]))
    return lines
