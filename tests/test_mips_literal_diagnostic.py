from io import BytesIO
from types import SimpleNamespace

import pytest
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom import match
from decompnet.rom.symbols import Symbol, SymbolTable


@pytest.mark.parametrize('gp', [0, 0x80008000])
def test_literal_failure_is_actionable_without_inventing_a_gp_or_placement(gp):
    linker = match._Linker.__new__(match._Linker)
    linker.target = SimpleNamespace(gp=gp)
    linker.problems = []
    raw = [{'type': 8, 'value': 0xD794C000}]
    assert linker.address(raw, 0, 1, 0) is None
    assert linker.address(raw, 0, 1, 0) is None
    assert len(linker.problems) == 1
    note = linker.problems[0]
    assert 'R_MIPS_LITERAL (8)' in note and 'guess' in note
    assert ('not configured' in note and '-G0' in note) if not gp else 'not supported' in note
    assert linker.target.gp == gp and raw == [{'type': 8, 'value': 0xD794C000}]


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs pinned KMC compiler')
@pytest.mark.parametrize('gp', [0, 0x80008000])
def test_period_compiler_literal_object_still_fails_the_ordinary_judge(tmp_path, gp):
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                        'flags': ['-G8', '-mips3', '-mgp32', '-mfp32', '-O2', '-g3']}})
    compiled = compile_source(cfg, '', 'extern void consume(double);\nvoid sample(void) { consume(0.123456789); }\n')
    assert compiled.ok, compiled.log
    elf = ELFFile(BytesIO(compiled.obj))
    kinds = [r['r_info_type'] for sec in elf.iter_sections() if isinstance(sec, RelocationSection) for r in sec.iter_relocations()]
    assert 8 in kinds
    symbol = next(s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name == 'sample')
    target = match.RomTarget('sample', 0x80001000, bytes(symbol['st_size']), [], gp=gp)
    result = match.link(compiled.obj, 'sample', target,
                        SymbolTable([Symbol('consume', 0x80002000, 4, 'func')]), lambda *_: None)
    assert not result.exact and not result.placements
    expected = 'not configured' if not gp else 'not supported'
    assert any('R_MIPS_LITERAL (8)' in issue and expected in issue for issue in result.problems)
