"""COFF objects, as Visual C++ writes them and as a PC project's tools/delink.py cuts the game's functions into: a
section per function (/Gy), its symbol the first one defined in it, relocations by symbol. Twins (app/twins.py)
fingerprint their functions; the rebuild check reads them with the project's own tools/rebuild.py, which a project
runs without decompdecomp. Standard library only.
"""

from __future__ import annotations

import re
import struct

MACHINE_I386 = 0x14C
HEADER = "<HHIIIHH"  # machine, sections, time, symbol table at, symbols, optional header bytes, flags
HEADER_BYTES, SECTION_BYTES, SYMBOL_BYTES, RELOC_BYTES = 20, 40, 18, 10
# how Visual C++ decorates a C name: _f (__cdecl), _f@8 (__stdcall), @f@8 (__fastcall)
_DECORATED = re.compile(r"^[_@]([A-Za-z_$?][\w$?]*?)(?:@\d+)?$")


def is_coff(obj: bytes) -> bool:
    """A 32-bit x86 COFF object (what Visual C++ writes)."""
    return len(obj) >= HEADER_BYTES and struct.unpack_from("<H", obj, 0)[0] == MACHINE_I386


def sections(obj: bytes) -> list[tuple[str, bytes, list[tuple[int, int, str, int, int]]]]:
    """Each section of a COFF object as (its function's symbol, its bytes, its relocations as (offset, type, symbol,
    section of a local label or 0, the label's value)), as tools/delink.py writes them: a section per function, its
    symbol the first one defined in it."""
    _, count, _, table, symbols, _, _ = struct.unpack_from(HEADER, obj, 0)
    strings = table + SYMBOL_BYTES * symbols
    syms, k = [], 0
    while k < symbols:
        raw, value, section, _, _, aux = struct.unpack_from("<8sIhHBB", obj, table + SYMBOL_BYTES * k)
        if raw[:4] == b"\0\0\0\0":
            at = strings + struct.unpack_from("<I", raw, 4)[0]
            raw = obj[at:obj.index(b"\0", at)]
        syms.append((raw.rstrip(b"\0").decode("latin-1"), value, section))
        syms.extend([("", 0, 0)] * aux)
        k += 1 + aux
    out = []
    for i in range(count):
        _, _, _, size, data, relocs, _, nrelocs, _, _ = struct.unpack_from(
            "<8sIIIIIIHHI", obj, HEADER_BYTES + SECTION_BYTES * i)
        owner = next((name for name, value, section in syms if section == i + 1 and not name.startswith("$")), "")
        rel = []
        for r in range(nrelocs):
            off, index, kind = struct.unpack_from("<IIH", obj, relocs + RELOC_BYTES * r)
            name, value, section = syms[index]
            rel.append((off, kind, name, section, value))
        out.append((owner, obj[data:data + size], rel))
    return out


def c_name(symbol: str) -> str:
    """The name C writes for a symbol Visual C++ decorated (`_func_0041B470@8`, `@f@8`: func_0041B470, f); a C++
    mangled name (`?f@@YAXH@Z`) or an import's pointer (`__imp__Sleep@4`) as it is."""
    m = _DECORATED.match(symbol)
    return m.group(1) if m and not symbol.startswith("__imp_") else symbol
