"""The candidate's own literal pool is placed by the base the target's nearby references vote for, so a literal
whose instruction sits a few words away from the target's still names the game's address (rom/match.py)."""
import struct
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom import match
from decompnet.rom.symbols import SymbolTable

POOL = 0x80002000
# SN's assembler expands each `li.s` into `lui $at` and `lwc1` of a word in its own .rdata, in code order
SOURCE = 'float f(float x) { return x * 1.1f + 2.3f; }\n'


def compiled(tmp_path):
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'sn-n64-281', 'flags': [
        '-G0', '-mips3', '-O2', '-mgas', '-meb', '-mcpu=VR4300', '-mhard-float', '-mfp32']}})
    out = compile_source(cfg, '', SOURCE)
    assert out.ok, out.log
    elf = ELFFile(BytesIO(out.obj))
    start = next(s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name == 'f')
    text = elf.get_section(start['st_shndx'])
    refs = []  # (offset in f, type, offset in the literal pool) of each half of each literal's address
    for sec in elf.iter_sections():
        if not isinstance(sec, RelocationSection) or sec['sh_info'] != start['st_shndx']:
            continue
        symtab = elf.get_section(sec['sh_link'])
        for r in sec.iter_relocations():
            if r['r_info_type'] not in (match.R_MIPS_HI16, match.R_MIPS_LO16):
                continue
            sym = symtab.get_symbol(r['r_info_sym'])
            word = struct.unpack('>I', text.data()[r['r_offset']:r['r_offset'] + 4])[0]
            low = word & 0xFFFF
            refs.append((r['r_offset'] - start['st_value'], r['r_info_type'], sym['st_value'], low))
    return out.obj, start['st_size'], sorted(refs)


def pool_offsets(refs):
    """The literal-pool offset each reference names (the low half carries it; a high half takes its pair's)."""
    lows = [low - (0x10000 if low & 0x8000 else 0) for off, kind, value, low in refs if kind == match.R_MIPS_LO16]
    return sorted(set(lows))


def read(addr, n):
    data = struct.pack('>ff', 1.1, 2.3)
    at = addr - POOL
    return data[at:at + n] if 0 <= at and at + n <= len(data) else None


@pytest.mark.skipif(toolchains.installed('sn-n64-281') is None, reason='needs the pinned SN compiler')
def test_a_literal_off_by_some_instructions_takes_the_pools_voted_address(tmp_path):
    obj, size, refs = compiled(tmp_path)
    offsets = pool_offsets(refs)
    assert len(offsets) == 2, refs  # 1.1f and 2.3f, each its own pool word
    hi = [off for off, kind, *_ in refs if kind == match.R_MIPS_HI16]
    lo = [off for off, kind, *_ in refs if kind == match.R_MIPS_LO16]
    # The target refers to the first literal from the same instructions and to the second one two words later,
    # as when the code above it differs.
    target = [(hi[0], match.R_MIPS_HI16, POOL + offsets[0]), (lo[0], match.R_MIPS_LO16, POOL + offsets[0]),
              (hi[1] + 8, match.R_MIPS_HI16, POOL + offsets[1]), (lo[1] + 8, match.R_MIPS_LO16, POOL + offsets[1])]
    result = match.link(obj, 'f', match.RomTarget('f', 0x80001000, bytes(size), target), SymbolTable(), read)
    assert not any('has no address' in p for p in result.problems), result.problems
    assert sorted(next(iter(result.placements.values())).values()) == [POOL + offsets[0], POOL + offsets[1]]
    assert not any('differs from the ROM' in p for p in result.problems), result.problems


@pytest.mark.skipif(toolchains.installed('sn-n64-281') is None, reason='needs the pinned SN compiler')
def test_a_single_reference_is_no_vote(tmp_path):
    obj, size, refs = compiled(tmp_path)
    offsets = pool_offsets(refs)
    hi = [off for off, kind, *_ in refs if kind == match.R_MIPS_HI16]
    # Only one reference of the target's: no base wins two votes, so the literal the target has nothing at stays
    # unplaced, as before.
    target = [(hi[0] + 0x40, match.R_MIPS_HI16, POOL + offsets[0])]
    result = match.link(obj, 'f', match.RomTarget('f', 0x80001000, bytes(size), target), SymbolTable(), read)
    assert any('has no address' in p for p in result.problems), result.problems


@pytest.mark.skipif(toolchains.installed('sn-n64-281') is None, reason='needs the pinned SN compiler')
def test_a_literal_where_the_target_refers_to_another_datum_still_takes_the_pool(tmp_path):
    obj, size, refs = compiled(tmp_path)
    offsets = pool_offsets(refs)
    hi = [off for off, kind, *_ in refs if kind == match.R_MIPS_HI16]
    lo = [off for off, kind, *_ in refs if kind == match.R_MIPS_LO16]
    # The target's code is two words further on from the second literal: at our instructions it refers to a global.
    other = 0x80300000
    target = [(hi[0], match.R_MIPS_HI16, POOL + offsets[0]), (lo[0], match.R_MIPS_LO16, POOL + offsets[0]),
              (hi[1], match.R_MIPS_HI16, other), (lo[1], match.R_MIPS_LO16, other),
              (hi[1] + 8, match.R_MIPS_HI16, POOL + offsets[1]), (lo[1] + 8, match.R_MIPS_LO16, POOL + offsets[1])]
    result = match.link(obj, 'f', match.RomTarget('f', 0x80001000, bytes(size), target), SymbolTable(), read)
    assert sorted(next(iter(result.placements.values())).values()) == [POOL + offsets[0], POOL + offsets[1]]
    assert not any('differs from the ROM' in p for p in result.problems), result.problems
