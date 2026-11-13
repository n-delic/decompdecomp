"""Judging C that defines one of the game's data symbols (docs/PORTING.md, phase 1: data migration).

The game's data is labelled assembly at fixed addresses; a port needs it as C. A definition is accepted only when
the compiled object, placed at the symbol's address, holds the game's bytes there: every byte of the definition,
its pointers resolved to the addresses the game's words hold (another symbol, or data the definition brings with it,
like the strings of a table of names), and the definition as long as the game's datum (to where the next one
starts), so four bytes can't stand for a table of four hundred. The function judge's linker (match._Linker) does
the placing and resolving; this module asks it about one datum instead of one function.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from elftools.elf.elffile import ELFFile

from ..candidate_work import elf as candidate_elf
from ..elfobj import unmangle
from .match import R_MIPS_32, ReadFn, RomTarget, _Linker
from .symbols import AUTO_NAME, SymbolTable

MAX_ALIGN = 8  # the most a compiler pads before a datum (a double, a long long): MIPS types align to 8 at most


@dataclass
class DataResult:
    exact: bool
    problems: list[str] = field(default_factory=list)
    address: int | None = None
    size: int = 0  # bytes the definition covers
    span: int | None = None  # bytes the game's datum spans, to where the next one starts (None: not known)
    initialised: bool = True  # False: a .bss definition, which has no bytes to compare


def _defined(elf, name: str):
    """The global data object `name` defines: (symbol, section index or "SHN_COMMON"), else None."""
    symtab = elf.get_section_by_name(".symtab")
    for s in symtab.iter_symbols() if symtab is not None else ():
        if s.name == name and s["st_shndx"] != "SHN_UNDEF" and s["st_info"]["bind"] != "STB_LOCAL":
            return s
    return None


def _others(elf, name: str) -> list[str]:
    """Other global things the object defines: a unit defines one datum (and what it brings: strings, statics)."""
    symtab = elf.get_section_by_name(".symtab")
    return sorted({s.name for s in (symtab.iter_symbols() if symtab is not None else ())
                   if s.name and s.name != name and s["st_shndx"] != "SHN_UNDEF"
                   and s["st_info"]["bind"] != "STB_LOCAL" and s["st_info"]["type"] in ("STT_OBJECT", "STT_FUNC")})


def link_data(obj: bytes, name: str, symbols: SymbolTable, read: ReadFn, *, big_endian: bool = True,
              end: int | None = None, ends: frozenset[int] = frozenset()) -> DataResult:
    """Place the datum `name` the object defines at its address and compare it with the game's bytes.

    `end` is where the next datum the game names starts (the definition mustn't run into it); `ends` are the other
    addresses the game's code refers to, any of which can end a datum (a struct's fields are referred to inside it,
    so they don't cut it short). Without either, the definition's own size is taken as the datum's."""
    obj = unmangle(obj, lambda n: n in symbols.by_name or bool(AUTO_NAME.match(n)))
    elf = candidate_elf(obj, ELFFile)
    address = symbols.resolve(name)
    result = DataResult(False, address=address)
    if address is None:
        result.problems.append(f"`{name}` is not a known symbol: use the name the game's code refers to it by "
                               "(like D_80012340), or give that address this name first with rename_symbol.")
        return result
    if elf.little_endian == big_endian:
        result.problems.append("the compiler produced data in the wrong byte order for this game")
        return result
    sym = _defined(elf, name)
    if sym is None or sym["st_info"]["type"] == "STT_FUNC":
        result.problems.append(f"the object defines no data named `{name}`: write its definition "
                               f"(`s32 {name}[4] = {{ ... }};`), not only a declaration")
        return result
    if others := _others(elf, name):
        result.problems.append(f"define only `{name}` here; {', '.join(others)} "
                               f"{'is a datum' if len(others) == 1 else 'are data'} of its own (static helpers are fine)")
    size = result.size = int(sym["st_size"])
    if size <= 0:
        result.problems.append(f"the compiler recorded no size for `{name}`")
        return result
    stop = end if end is not None and end > address else None
    if stop is not None and address + size > stop:
        result.problems.append(f"`{name}` is {size} bytes, but the next datum ({symbols.label(stop)}) starts "
                               f"{stop - address} bytes after it: define less, or check the game's data there")
    else:
        # The datum ends at the first place after the definition the game names or refers to; the definition must
        # reach it, but for the zero padding the next datum's alignment leaves (less than that alignment: eight zero
        # bytes before an 8-aligned datum are the definition's to say, not padding).
        bounds = sorted({e for e in ends if address < e and (stop is None or e < stop)} | ({stop} if stop else set()))
        after = next((e for e in bounds if e >= address + size), None)
        if after is not None:
            gap = read(address + size, after - address - size) if after > address + size else b""
            if after - address - size >= min(MAX_ALIGN, after & -after) or (gap and any(gap)):
                result.problems.append(f"`{name}` is {size} bytes, but the game's datum runs on to "
                                       f"{symbols.label(after)} ({after - address} bytes): define all of it")
            else:
                result.span = after - address
    common = sym["st_shndx"] == "SHN_COMMON"
    section = None if common else elf.get_section(sym["st_shndx"])
    rom = read(address, size)
    if common or section["sh_type"] == "SHT_NOBITS":
        result.initialised = False
        # The game keeps it in initialised data: without an initialiser the compiler puts it in .bss instead, and a
        # rebuilt game lays out differently, even where the game's bytes are all zero.
        if rom is not None:
            result.problems.append(f"the game keeps `{name}` in initialised data ({rom[:16].hex(' ')}"
                                   f"{'…' if len(rom) > 16 else ''}): give it an initialiser, `= {{0}}` for zeros")
        result.exact = not result.problems
        return result
    if rom is None:
        result.problems.append(f"the game has no bytes at {symbols.label(address)}: it may be .bss (define it "
                               "without an initialiser) or outside the ROM's code segments")
        return result

    # The function judge's linker, with the datum as the "function" it places: pointers to the datum itself resolve
    # to its address, and its own strings and statics go where the game's pointers point.
    linker = _Linker(obj, name, RomTarget(name, address, b"", [], big_endian), symbols, read)
    shndx, off = sym["st_shndx"], int(sym["st_value"])
    linker.fn_shndx = None
    linker.data[(shndx, off)] = linker.local_map[(shndx, off)] = address
    linker.widths[(shndx, off)] = None
    linker.place_pointees()
    mine = bytearray(section.data()[off:off + size])
    for at, rtype, s in linker._relocs_in(shndx):
        at -= off
        if not 0 <= at < size:
            continue
        if rtype != R_MIPS_32 or at > size - 4:
            linker.problem(f"`{name}` holds an address the judge can't place (relocation type {rtype})")
            continue
        dest, own = linker._pointee(s, int.from_bytes(mine[at:at + 4], linker.target.order))
        if own is not None:
            dest = linker.local_map.get(own)
        if dest is None:
            linker.problem(f"can't place the pointer at `{name}`+0x{at:X}: the game's word there points nowhere "
                           "this definition brings")
            continue
        mine[at:at + 4] = (dest & 0xFFFFFFFF).to_bytes(4, linker.target.order)
    for (dshndx, doff), daddr in sorted(linker.data.items()):
        if (dshndx, doff) != (shndx, off):
            linker.verify_data(dshndx, doff, daddr)  # the strings and statics it points at
    if bytes(mine) != rom:
        first = next(i for i, (a, b) in enumerate(zip(mine, rom)) if a != b)
        linker.problem(f"`{name}` differs from the game's bytes from +0x{first:X}: yours "
                       f"{bytes(mine[first:first + 16]).hex(' ')}, the game's {rom[first:first + 16].hex(' ')}")
    result.problems += linker.problems
    result.exact = not result.problems
    return result
