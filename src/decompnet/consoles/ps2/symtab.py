"""Names from a PS2 program's ELF symbol table (#3: Champions of Norrath ships with it): every function and datum the
linker kept a name for, with its exact size, given to splat before it splits (symbol_addrs.txt), so functions get
their real names and boundaries instead of func_... and splat's guesses.

The app works with C identifiers, so each name is one: a C++ name GCC mangled into one (Update__4CPedi,
_ZN4CPed6UpdateEv) stays exactly as it is, which is what the judge needs to pair it with an agent's C++ method. GCC
2.9x's special names (destructors _$_4CPed, vtables _vt$4CPed, statics in functions count.0) get _ for the
characters a C name can't have; elfobj.unmangle turns a compiled object's names back the same way. Local labels
(.L12, $LC0) and the linker's markers aren't names of anything. Two names that come out the same (static
functions of two files) are told apart by the address: init_00123450. A global keeps its name over a static.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

_NOT_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]")
_LOCAL_LABEL = re.compile(r"^(?:\.L|\$L)")
SHN_LORESERVE = 0xFF00  # section indexes from here are special (absolute, common, MIPS small common), not sections


@dataclass
class Named:
    name: str
    address: int
    size: int
    function: bool
    section: str


def c_name(name: str) -> str:
    """A symbol's name as a C identifier: other characters become _."""
    name = _NOT_IDENTIFIER.sub("_", name)
    return f"_{name}" if name[:1].isdigit() else name


def read(elf: ELFFile) -> list[Named]:
    """Every named function and datum, one name per address, by address."""
    symtab = elf.get_section_by_name(".symtab")
    if not isinstance(symtab, SymbolTableSection):
        return []
    best: dict[int, tuple[tuple, Named, bool]] = {}
    for sym in symtab.iter_symbols():
        index, kind, bind = sym["st_shndx"], sym["st_info"]["type"], sym["st_info"]["bind"]
        if not isinstance(index, int) or not index or index >= SHN_LORESERVE or not sym.name \
                or _LOCAL_LABEL.match(sym.name):
            continue  # undefined, absolute (the linker's _gp, _end) or common (MIPS: small common too)
        section = elf.get_section(index)
        flags = section["sh_flags"]
        if not flags & SH_FLAGS.SHF_ALLOC:
            continue
        code = bool(flags & SH_FLAGS.SHF_EXECINSTR)
        # functions in code, data outside it; a static from assembly (.lcomm) has no type, a label in code neither
        if not (kind == "STT_FUNC" and code or kind == "STT_OBJECT" and not code
                or kind == "STT_NOTYPE" and bind == "STB_LOCAL" and not code):
            continue
        rank = (kind != "STT_NOTYPE", bind == "STB_GLOBAL", sym["st_size"] > 0)
        named = Named(sym.name, sym["st_value"], sym["st_size"], code, section.name)
        if sym["st_value"] not in best or rank > best[sym["st_value"]][0]:
            best[sym["st_value"]] = (rank, named, bind == "STB_GLOBAL")
    used: set[str] = set()
    out = []
    for _, named, _ in sorted(best.values(), key=lambda b: (not b[2], b[1].address)):  # globals first
        name = c_name(named.name)
        if name in used:
            name = f"{name}_{named.address:08X}"
        used.add(name)
        named.name = name
        out.append(named)
    return sorted(out, key=lambda n: n.address)


def symbol_addrs(named: list[Named]) -> list[str]:
    """splat's symbol_addrs.txt lines for `named`: functions with their type and size, data with its size."""
    lines = []
    for n in named:
        attributes = (["type:func"] if n.function else []) + ([f"size:0x{n.size:X}"] if n.size else [])
        lines.append(f"{n.name} = 0x{n.address:08X};" + (f" // {' '.join(attributes)}" if attributes else ""))
    return lines
