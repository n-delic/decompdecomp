"""Synthetic MIPS helpers and their nested tables: no game data or installed compiler needed."""
import struct

import pytest

from decompnet.rom.match import RomTarget, link
from decompnet.rom.symbols import Symbol, SymbolTable


def fixture(big, *, bad_code=False, bad_table=False, missing_lo=False, external=False):
    order = ">" if big else "<"
    def pack(fmt, *values):
        return struct.pack(order + fmt, *values)

    caller, helper, table = 0x100000, 0x100100, 0x218004
    caller_code = pack("III", 0x0C000000 | (helper >> 2), 0x03E00008, 0)
    helper_code = pack("IIII", 0x3C030022, 0x24638004, 0x03E00008, 0)
    table_code = pack("II", helper + 8, helper + 12)
    images = {helper: helper_code, table: table_code}
    def read(addr, size):
        for start, data in images.items():
            if start <= addr and addr + size <= start + len(data):
                return data[addr - start:addr - start + size]
        return None

    names = b"\0caller\0helper\0.table\0known_table\0"
    def sym(name, value, size, kind, section):
        return pack("IIIBBH", names.index(name.encode()) if name else 0, value, size, kind, 0, section)

    symbols = bytes(16) + sym("caller", 0, 12, 0x12, 1) + sym("helper", 0, 16, 2, 2)
    symbols += sym("known_table" if external else "", 0, 0, 0x10 if external else 3, 0 if external else 3)
    symbols += sym(".table", 4, 8, 1, 3)
    # Caller references a local helper in another section; helper references anonymous data with addend 4.
    sections = [
        (".text.caller", 1, 6, pack("III", 0x0C000000, 0x03E00008, 0), 0, 0, 0),
        (".text.helper", 1, 6, pack("IIII", 0x3C030000, 0x24630004, 0x03E00008, int(bad_code)), 0, 0, 0),
        (".rodata", 1, 2, pack("III", 0, 8, 4 if bad_table else 12), 0, 0, 0),
        (".rel.caller", 9, 0, pack("II", 0, (2 << 8) | 4), 7, 1, 8),
        (".rel.helper", 9, 0, pack("II", 0, (3 << 8) | 5) +
         (b"" if missing_lo else pack("II", 4, (3 << 8) | 6)), 7, 2, 8),
        (".rel.rodata", 9, 0, pack("IIII", 4, (2 << 8) | 2, 8, (2 << 8) | 2), 7, 3, 8),
        (".symtab", 2, 0, symbols, 8, 1, 16),
        (".strtab", 3, 0, names, 0, 0, 0),
    ]
    shnames = b"\0" + b"".join(s[0].encode() + b"\0" for s in sections) + b".shstrtab\0"
    sections.append((".shstrtab", 3, 0, shnames, 0, 0, 0))
    obj = bytearray(52)
    headers = [bytes(40)]
    for name, kind, flags, data, linked, info, entsize in sections:
        obj.extend(bytes(-len(obj) % 4))
        headers.append(pack("IIIIIIIIII", shnames.index(name.encode()), kind, flags, 0, len(obj), len(data),
                            linked, info, 4, entsize))
        obj.extend(data)
    obj.extend(bytes(-len(obj) % 4))
    shoff = len(obj)
    obj.extend(b"".join(headers))
    obj[:52] = b"\x7fELF" + bytes([1, 2 if big else 1, 1]) + bytes(9) + pack(
        "HHIIIIIHHHHHH", 1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(headers), 9)
    syms = [Symbol("caller", caller, 12, "func"), Symbol("helper", helper, 16, "func")]
    if external:
        syms.append(Symbol("known_table", table - 4, 12, "data"))
    target = RomTarget("caller", caller, caller_code, [(0, 4, helper)], big)
    return bytes(obj), target, SymbolTable(syms), read


@pytest.mark.parametrize("big", [True, False])
def test_helper_hi_lo_places_and_checks_nested_jump_table(big):
    obj, target, symbols, read = fixture(big)
    result = link(obj, "caller", target, symbols, read)
    assert result.exact and not result.problems
    assert result.placements[".rodata"][4] == 0x218004


@pytest.mark.parametrize("big", [True, False])
@pytest.mark.parametrize("damage", ["bad_code", "bad_table", "missing_lo"])
def test_helper_or_nested_data_must_still_match(big, damage):
    obj, target, symbols, read = fixture(big, **{damage: True})
    result = link(obj, "caller", target, symbols, read)
    assert not result.exact and result.problems


@pytest.mark.parametrize("big", [True, False])
def test_helper_hi_lo_resolves_external_symbol_and_addend(big):
    obj, target, symbols, read = fixture(big, external=True)
    result = link(obj, "caller", target, symbols, read)
    assert result.exact and not result.problems
