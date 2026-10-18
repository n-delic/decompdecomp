"""The .mdebug section: the MIPS ECOFF symbol table that Sony's EE-GCC and SN Systems' ProDG leave in a PS2 program
built with -g (#3: Champions of Norrath ships with it). pyelftools doesn't read it, so this does.

The section starts with a symbolic header, which says where the other tables are. What decompdecomp reads:
- one file descriptor per source file the program was linked from (its name, its symbols, its procedures);
- each file's local symbols: procedures with their addresses (also for hand-written assembly), and the STABS
  strings GCC wrote for types, functions, parameters and variables (stabs.py reads those);
- the string tables those symbols point into.

The layout is the one binutils documents in include/coff/sym.h (little-endian here, like the EE). The offsets in the
header are offsets into the whole file, not into the section.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = 0x7009
HEADER = struct.Struct("<hh23i")  # magic, version stamp, then 23 counts and offsets
FILE_DESCRIPTOR = struct.Struct("<I9iHh4iI2i")
LOCAL_SYMBOL = struct.Struct("<iiI")  # string, value, packed type/class/index
# the packed word of a symbol (little-endian bit order): symbol type, storage class, a reserved bit, an index
TYPE_BITS, CLASS_SHIFT, CLASS_MASK, INDEX_SHIFT = 0x3F, 6, 0x1F, 12

# symbol types (st) and storage classes (sc) decompdecomp uses
ST_NIL, ST_PROC, ST_END, ST_STATIC_PROC = 0, 6, 8, 14
SC_TEXT, SC_INFO = 1, 11
# a symbol that holds a STABS string has this added to its stab code in its index (binutils: ECOFF_MARK_STAB)
STAB_MARK = 0x8F300
STAB_MASK = 0xFFF00


@dataclass
class Symbol:
    name: str
    value: int
    kind: int  # st
    storage: int  # sc
    index: int

    @property
    def stab(self) -> int | None:
        """The stab code (N_FUN, N_LSYM, ...) when this symbol carries a STABS string."""
        return self.index - STAB_MARK if self.index & STAB_MASK == STAB_MARK else None


@dataclass
class SourceFile:
    name: str
    address: int
    symbols: list[Symbol] = field(default_factory=list)

    def procedures(self) -> list[tuple[str, int]]:
        """(name, address) of the procedures this file defines, assembly ones included."""
        return [(s.name, s.value) for s in self.symbols if s.kind in (ST_PROC, ST_STATIC_PROC) and s.storage == SC_TEXT
                and s.stab is None]

    def stabs(self) -> list[tuple[int, str, int]]:
        """(stab code, string, value) of every STABS entry, with GCC's continued strings (a trailing backslash) joined."""
        out: list[tuple[int, str, int]] = []
        pending = ""
        for s in self.symbols:
            code = s.stab
            if code is None or s.name == "@stabs":
                continue
            text = pending + s.name
            if text.endswith("\\"):
                pending = text[:-1]
                continue
            pending = ""
            out.append((code, text, s.value))
        return out


def _string(data: bytes, at: int) -> str:
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1")


def read(data: bytes, section_offset: int, section_size: int) -> list[SourceFile]:
    """Every source file in the .mdebug section found at `section_offset` of the program `data`. Empty when the section
    isn't a symbolic header decompdecomp can read."""
    if section_size < HEADER.size or section_offset + HEADER.size > len(data):
        return []
    (magic, _, _, _, _, _, _, _, _, _, symbols_at, _, _, _, _, _, strings_at, _, _, files, files_at,
     *_) = HEADER.unpack_from(data, section_offset)
    if magic != MAGIC:
        return []
    # the offsets are the file's: ones that would point outside the section can only be relative to it
    shift = 0 if section_offset <= files_at < section_offset + section_size else section_offset
    out = []
    for number in range(files):
        at = shift + files_at + FILE_DESCRIPTOR.size * number
        if at + FILE_DESCRIPTOR.size > len(data):
            break
        address, name_at, strings_base, _, first_symbol, symbol_count, *_ = FILE_DESCRIPTOR.unpack_from(data, at)
        strings = shift + strings_at + strings_base
        source = SourceFile(_string(data, strings + name_at), address)
        for k in range(symbol_count):
            sym_at = shift + symbols_at + LOCAL_SYMBOL.size * (first_symbol + k)
            if sym_at + LOCAL_SYMBOL.size > len(data):
                break
            name, value, packed = LOCAL_SYMBOL.unpack_from(data, sym_at)
            source.symbols.append(Symbol(_string(data, strings + name) if name >= 0 else "", value, packed & TYPE_BITS,
                                         (packed >> CLASS_SHIFT) & CLASS_MASK, packed >> INDEX_SHIFT))
        out.append(source)
    return out
