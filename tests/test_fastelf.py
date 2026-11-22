"""The Cython symbol decoder answers exactly as pyelftools does, for either byte order, and is skipped when unbuilt."""
import struct
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import candidate_work, fastelf

pytestmark = pytest.mark.skipif(not fastelf.available(), reason='the Cython symbol decoder is not built')

NAMES = ['', 'main', 'gcc2_compiled.', '.text', 'ünï', 'x' * 200]


def elf32(little, symbols, class_bits=1):
    """A minimal ELF32 object: null, .symtab, .strtab, .shstrtab. `symbols` are (name_offset, value, size, info,
    other, shndx)."""
    end = '<' if little else '>'
    strtab = b'\x00' + b'\x00'.join(name.encode() for name in NAMES[1:]) + b'\x00'
    shstr = b'\x00.symtab\x00.strtab\x00.shstrtab\x00'
    table = b''.join(struct.pack(end + 'IIIBBH', *s) for s in symbols)
    body = table + strtab + shstr
    base = 52
    sections = [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                (1, 2, 0, 0, base, len(table), 2, 1, 4, 16),
                (9, 3, 0, 0, base + len(table), len(strtab), 0, 0, 1, 0),
                (17, 3, 0, 0, base + len(table) + len(strtab), len(shstr), 0, 0, 1, 0)]
    shoff = base + len(body)
    header = (b'\x7fELF' + bytes([class_bits, 1 if little else 2, 1]) + bytes(9)
              + struct.pack(end + 'HHIIIIIHHHHHH', 1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 4, 3))
    return header + body + b''.join(struct.pack(end + 'IIIIIIIIII', *s) for s in sections)


SYMBOLS = [(0, 0, 0, 0, 0, 0), (1, 0x40, 0x20, 0x12, 0, 1), (6, 0, 0, 0x04, 0, 0xFFF1),
           (21, 7, 4, 0x01, 2, 0xFFF2), (27, 0x10, 0, 0x2F, 0x03, 0xFF03), (35, 0xFFFFFFFF, 0xFFFFFFFF, 0xFD, 0xFF, 7),
           (999, 1, 2, 0x13, 0, 0)]  # (the last name offset is past the table: pyelftools' empty name)


@pytest.mark.parametrize('little', [True, False])
def test_symbols_match_pyelftools_field_for_field(little):
    data = elf32(little, SYMBOLS)
    plain = ELFFile(BytesIO(data)).get_section_by_name('.symtab')
    fast = fastelf.FastELFFile(BytesIO(data)).get_section_by_name('.symtab')
    assert isinstance(fast, fastelf.FastSymbolTable)
    assert fast.num_symbols() == plain.num_symbols() == len(SYMBOLS)
    for n in range(len(SYMBOLS)):
        a, b = plain.get_symbol(n), fast.get_symbol(n)
        assert a.name == b.name
        for key in ('st_name', 'st_value', 'st_size', 'st_shndx'):
            assert a[key] == b[key], (n, key)
        assert dict(a['st_info']) == b['st_info'] if isinstance(a['st_info'], dict) else (
            a['st_info']['bind'], a['st_info']['type']) == (b['st_info']['bind'], b['st_info']['type'])
        assert (a['st_other']['local'], a['st_other']['visibility']) == (
            b['st_other']['local'], b['st_other']['visibility'])
    assert [s.name for s in plain.iter_symbols()] == [s.name for s in fast.iter_symbols()]


def test_a_symbol_past_the_table_raises_like_pyelftools():
    data = elf32(True, SYMBOLS)
    fast = fastelf.FastELFFile(BytesIO(data)).get_section_by_name('.symtab')
    plain = ELFFile(BytesIO(data)).get_section_by_name('.symtab')
    with pytest.raises(Exception) as expected:
        plain.get_symbol(len(SYMBOLS) + 50)
    with pytest.raises(type(expected.value)):
        fast.get_symbol(len(SYMBOLS) + 50)


def test_elf64_keeps_pyelftools(tmp_path):
    # (an ELF64 file isn't decoded here: the table stays empty and pyelftools answers)
    elf = fastelf.FastELFFile(BytesIO(elf32(True, SYMBOLS)))
    elf.elfclass = 64
    assert elf.get_section_by_name('.symtab')._table() == ()


def test_candidate_work_hands_out_the_fast_reader_only_for_plain_elffile():
    data = elf32(False, SYMBOLS)
    assert isinstance(candidate_work.elf(data, ELFFile), fastelf.FastELFFile)

    class Other(ELFFile):
        pass

    other = candidate_work.elf(data, Other)
    assert type(other) is Other
