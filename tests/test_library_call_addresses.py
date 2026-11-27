"""A compiler-emitted library call (sqrtf) takes the address of the game's call at the same place."""
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom import export, match
from decompnet.rom.symbols import Symbol, SymbolTable


def linker(relocs, symbols=()):
    obj = match._Linker.__new__(match._Linker)
    obj.problems, obj.library_names = [], {}
    obj.symbols = SymbolTable(list(symbols))
    obj.target_at = {off: (kind, addr) for off, kind, addr in relocs}
    return obj


CALL = {'type': match.R_MIPS_26, 'off': 0x10}


def test_name_takes_the_address_of_the_games_call():
    one = linker([(0x10, match.R_MIPS_26, 0x80055B40)], [Symbol('func_80055B40', 0x80055B40, 8, 'func')])
    assert one.library_call('sqrtf', CALL, 0) == 0x80055B40
    assert one.problems == []


@pytest.mark.parametrize('name', ['sqrtf', '__divdi3', 'MULT_HI'])
def test_compiler_library_names(name):
    assert match.compiler_library_name(name)


@pytest.mark.parametrize('name', ['color', 'func_80001234', 'D_80012340', 'myhelper'])
def test_the_programmers_names_are_not_guessed(name):
    assert not match.compiler_library_name(name)
    assert linker([(0x10, match.R_MIPS_26, 0x80055B40)]).library_call(name, CALL, 0) is None


def test_no_call_in_the_target_there_means_no_guess():
    assert linker([]).library_call('sqrtf', CALL, 0) is None
    assert linker([(0x10, match.R_MIPS_HI16, 0x80055B40)]).library_call('sqrtf', CALL, 0) is None


def test_a_name_already_in_the_table_resolves_normally():
    known = linker([(0x10, match.R_MIPS_26, 0x80099999)], [Symbol('sqrtf', 0x80055B40, 8, 'func')])
    assert known.library_call('sqrtf', CALL, 0) is None


def test_one_name_for_two_addresses_is_reported():
    two = linker([(0x10, match.R_MIPS_26, 0x80055B40), (0x20, match.R_MIPS_26, 0x80066000)])
    assert two.library_call('sqrtf', CALL, 0) == 0x80055B40
    assert two.library_call('sqrtf', {'type': match.R_MIPS_26, 'off': 0x20}, 0) is None
    assert 'two different ROM addresses' in two.problems[0]



@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs pinned KMC compiler')
def test_a_real_candidate_calling_a_builtin_links_to_the_games_routine(tmp_path):
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                          'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    compiled = compile_source(cfg, '', 'extern float sqrtf(float);\nfloat f(float x) { return sqrtf(x); }\n')
    assert compiled.ok, compiled.log
    elf = ELFFile(BytesIO(compiled.obj))
    start = next(s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name == 'f')
    call = next(r['r_offset'] for sec in elf.iter_sections() if isinstance(sec, RelocationSection)
                for r in sec.iter_relocations() if r['r_info_type'] == match.R_MIPS_26)
    target = match.RomTarget('f', 0x80001000, bytes(start['st_size']), [(call - start['st_value'], match.R_MIPS_26, 0x80055B40)])
    result = match.link(compiled.obj, 'f', target, SymbolTable(), lambda *_: None)
    assert not any('not a known symbol' in p for p in result.problems), result.problems
    assert result.inferred_library_calls == {'sqrtf': 0x80055B40}
    exact_target = match.RomTarget('f', 0x80001000, result.linked_code, target.relocs)
    exact = match.link(compiled.obj, 'f', exact_target, SymbolTable(), lambda *_: None)
    assert exact.exact and exact.inferred_library_calls == {'sqrtf': 0x80055B40}
    assert export._needs_entry_only(exact, None, True, False)
    # A native judge can report exact bytes without returning inferred library bindings.
    token = match.rom_linker.set(lambda *_: match.LinkResult(None, True, []))
    try:
        exported = export._link_export(None, compiled.obj, 'f', exact_target, SymbolTable(), lambda *_: None)
        assert exported.exact and exported.inferred_library_calls == {'sqrtf': 0x80055B40}
        assert export._needs_entry_only(exported, None, True, False)
    finally:
        match.rom_linker.reset(token)
    known = match.link(compiled.obj, 'f', exact_target,
                       SymbolTable([Symbol('sqrtf', 0x80055B40, 8, 'func')]), lambda *_: None)
    assert known.exact and not export._needs_entry_only(known, None, True, False)


def test_export_keeps_console_specific_targets_on_the_console_linker():
    class Console:
        def link_function(self, obj, name, target, symbols, read):
            return target

    target = object()
    assert export._link_export(Console(), b'', 'f', target, SymbolTable(), lambda *_: None) is target
