"""Names from a PS3 program's ELF symbol table, when the program kept it (a debug build, or a homebrew program).

Like the PS2's (consoles/ps2/symtab.py) with one difference the PowerPC64 ABI makes: a function's symbol points at its
descriptor in `.opd`, not at its code, so the address of the function is the entry the descriptor holds (8 bytes in a
program with the usual 24-byte descriptors, 4 in Sony's compact ones). A symbol in an executable section (the dotted
`.name` of some compilers) is the code itself. Data is a symbol of an object in a section that isn't code.
"""

from __future__ import annotations

import re
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from ...rom import RomError
from ..ps2 import symtab
from ..ps2.symtab import Named
from . import elf as ps3elf

_LINE = re.compile(r"^(\w+) = 0x([0-9A-Fa-f]+);(?: // (.*))?$")


def read(program: bytes) -> list[Named]:
    """Every named function and datum, one name per address, by address; [] when the program has no symbol table."""
    try:
        elf = ELFFile(BytesIO(program))
        table = elf.get_section_by_name(".symtab")
        if not isinstance(table, SymbolTableSection):
            return []
        info = ps3elf.parse(program)
        descriptor = ps3elf.module_descriptor(program, info)
        opd = elf.get_section_by_name(".opd")
        opd_bytes, entry = (opd.data(), 8 if descriptor is None or descriptor.abi == "lp64" else 4) if opd else (b"", 8)
    except (ELFError, ValueError, KeyError, RomError):
        return []
    best: dict[int, tuple[tuple, Named]] = {}
    for sym in table.iter_symbols():
        index, kind = sym["st_shndx"], sym["st_info"]["type"]
        if not isinstance(index, int) or not index or index >= symtab.SHN_LORESERVE or not sym.name \
                or symtab._LOCAL_LABEL.match(sym.name):
            continue
        section = elf.get_section(index)
        if not section["sh_flags"] & SH_FLAGS.SHF_ALLOC:
            continue
        code, name, address = bool(section["sh_flags"] & SH_FLAGS.SHF_EXECINSTR), sym.name, sym["st_value"]
        if kind == "STT_FUNC" and section.name == ".opd":
            at = address - opd.header["sh_addr"]
            if at < 0 or at + entry > len(opd_bytes):
                continue
            address, code = int.from_bytes(opd_bytes[at:at + entry], "big"), True
        elif kind == "STT_FUNC" and code:
            name = name.removeprefix(".")  # (a dotted code symbol belongs to the same-named descriptor)
        elif not (kind == "STT_OBJECT" and not code):
            continue
        rank = (sym["st_info"]["bind"] == "STB_GLOBAL", sym["st_size"] > 0)
        if address not in best or rank > best[address][0]:
            best[address] = (rank, Named(name, address, sym["st_size"], code, section.name))
    used: set[str] = set()
    out = []
    for _, named in sorted(best.values(), key=lambda b: (not b[0][0], b[1].address)):  # globals first
        label = symtab.c_name(named.name)
        if label in used:
            label = f"{label}_{named.address:08X}"
        used.add(label)
        named.name = label
        out.append(named)
    return sorted(out, key=lambda n: n.address)


def from_lines(lines: list[str] | None) -> tuple[dict[int, str], set[int]]:
    """({address: name}, the addresses that are functions) from the symbol lines the import hands the splitter
    (symtab.symbol_addrs)."""
    names, functions = {}, set()
    for line in lines or []:
        m = _LINE.match(line)
        if m:
            names[int(m[2], 16)] = m[1]
            if "type:func" in (m[3] or ""):
                functions.add(int(m[2], 16))
    return names, functions
