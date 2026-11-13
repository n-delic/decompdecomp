"""Synthetic MIPS objects: relocatable calls inside a submitted helper must be linked before judging."""

import struct

import pytest

from decompnet.rom import match
from decompnet.rom.symbols import Symbol, SymbolTable


def fixture(big=False, rela=False, addend=0, opcode=3, recursive=False):
    order = ">" if big else "<"
    def pack(fmt, *values):
        return struct.pack(order + fmt, *values)
    def words(*values):
        return pack("I" * len(values), *values)
    strings = b"\0helper\0caller\0external\0"
    names = b"\0.text\0.helper\0.rel.text\0.rel.helper\0.symtab\0.strtab\0.shstrtab\0"
    def symbol(name, value, size, info, section):
        return pack("IIIBBH", strings.index(name.encode()) if name else 0, value, size, info, 0, section)
    symbols = (bytes(16) + symbol("helper", 4, 16, 2, 2) + symbol("caller", 0, 8, 18, 1) +
               symbol("external", 0, 0, 18, 0))
    def reloc(offset, sym, value):
        return pack("IIi", offset, (sym << 8) | 4, value) if rela else pack("II", offset, (sym << 8) | 4)
    caller = words(0x0C000000, 0)
    helper = words(0, (opcode << 26) | (0 if rela else addend >> 2), 0x24020001, 0x03E00008, 0)
    sections = [
        ("", 0, b"", 0, 0, 0, 0),
        (".text", 1, caller, 6, 0, 0, 0),
        (".helper", 1, helper, 6, 0, 0, 0),
        (".rel.text", 4 if rela else 9, reloc(0, 1, 0), 0, 5, 1, 12 if rela else 8),
        (".rel.helper", 4 if rela else 9, reloc(4, 1 if recursive else 3, addend), 0, 5, 2, 12 if rela else 8),
        (".symtab", 2, symbols, 0, 6, 2, 16),
        (".strtab", 3, strings, 0, 0, 0, 0),
        (".shstrtab", 3, names, 0, 0, 0, 0),
    ]
    image = bytearray(52)
    headers = []
    for name, typ, data, flags, link, info, entsize in sections:
        image.extend(bytes(-len(image) % 4))
        offset = len(image)
        image.extend(data)
        headers.append(pack("IIIIIIIIII", names.index(name.encode()) if name else 0, typ, flags, 0,
                            offset if typ else 0, len(data), link, info, 4 if typ else 0, entsize))
    image.extend(bytes(-len(image) % 4))
    shoff = len(image)
    image.extend(b"".join(headers))
    image[:52] = (b"\x7fELF" + bytes([1, 2 if big else 1, 1]) + bytes(9) +
                  pack("HHIIIIIHHHHHH", 1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(sections), 7))
    helper_addr, external_addr = 0x100100, 0x100200
    destination = (helper_addr if recursive else external_addr) + addend
    target = match.RomTarget("caller", 0x100000, words(0x0C000000 | (helper_addr >> 2), 0),
                             [(0, 4, helper_addr)], big_endian=big)
    rom = words((opcode << 26) | (destination >> 2), 0x24020001, 0x03E00008, 0)
    syms = SymbolTable([Symbol("helper", helper_addr, 16, "func"), Symbol("external", external_addr, 16, "func")])
    def judge(data=rom, table=syms):
        def read(addr, n):
            at = addr - helper_addr
            return data[at:at + n] if 0 <= at and at + n <= len(data) else None
        return match.link(bytes(image), "caller", target, table, read)
    return judge, rom, order


@pytest.mark.parametrize("big", [False, True])
@pytest.mark.parametrize("rela", [False, True])
@pytest.mark.parametrize("addend", [0, 8])
@pytest.mark.parametrize("opcode", [2, 3])
def test_helper_calls_and_tail_jumps(big, rela, addend, opcode):
    judge, _, _ = fixture(big, rela, addend, opcode)
    result = judge()
    assert result.exact
    assert result.problems == []


@pytest.mark.parametrize("recursive", [False, True])
def test_relocated_helper_still_requires_identical_bytes(recursive):
    judge, rom, order = fixture(recursive=recursive)
    assert judge().exact
    # A different destination, jump opcode, or delay-slot instruction must all fail.
    for offset, mask in [(0, 1), (0, 1 << 26), (4, 1)]:
        bad = bytearray(rom)
        word = struct.unpack_from(order + "I", bad, offset)[0]
        struct.pack_into(order + "I", bad, offset, word ^ mask)
        assert not judge(bytes(bad)).exact


def test_unknown_helper_callee_is_not_accepted():
    judge, _, _ = fixture()
    result = judge(table=SymbolTable([Symbol("helper", 0x100100, 16, "func")]))
    assert not result.exact
    assert any("external" in problem and "not a known symbol" in problem for problem in result.problems)
