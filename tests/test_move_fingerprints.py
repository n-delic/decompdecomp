"""A function's register moves point the deep pass at the compiler that built it (move_fingerprints)."""
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import deep_profiles, move_fingerprints

KMC = ('gcc-2.7.2-kmc', ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2'])
CFG = {'platform': 'n64', 'compiler': {'toolchain': KMC[0], 'flags': KMC[1]}}


def words(*ws):
    return b''.join(w.to_bytes(4, 'big') for w in ws)


def test_moves_are_read_from_the_code():
    JR_RA, NOP = 0x03E00008, 0
    assert move_fingerprints.moves(words(0x00801025, JR_RA, NOP), True) == 'or'  # or v0, a0, zero
    assert move_fingerprints.moves(words(0x00801021, JR_RA, NOP), True) == 'addu'  # addu v0, a0, zero
    assert move_fingerprints.moves(words(0x00801025, 0x00A01821, JR_RA, NOP), True) is None  # both
    assert move_fingerprints.moves(words(JR_RA, NOP), True) is None  # none


def test_no_target_or_no_project_adds_nothing():
    own = {'toolchain': KMC[0], 'flags': KMC[1]}
    assert move_fingerprints.profiles(None, {'big_endian': 1}, CFG, {'target': b''}, own) == []
    net = SimpleNamespace(cfg=lambda p: CFG, compiler_profile_usage=lambda p: [])
    # (deep_profiles' callers without a project row keep their old behaviour)
    assert deep_profiles.selected(net, {}, 1, {'name': 'f', 'vram': 0x80000100, 'rom_offset': 10,
                                               'target': words(0x00801025)}) == [{'toolchain': KMC[0], 'flags': KMC[1]}]


def _ido_code(source):
    from elftools.elf.elffile import ELFFile

    from decompnet import consoles
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    with tempfile.TemporaryDirectory() as work:
        cfg = compiler_config(Path(work), {'compiler': {'toolchain': 'ido-5.3',
                                                        'flags': consoles.default_flags(CFG, 'ido-5.3')}})
        result = compile_source(cfg, '', source)
        assert result.ok, result.log
        return ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()


def test_an_ido_function_in_a_gcc_game_gets_an_ido_probe():
    if not (toolchains.installed(KMC[0]) and toolchains.installed('ido-5.3')):
        pytest.skip('Pinned KMC and IDO compilers are not installed; never download them here.')
    assert move_fingerprints.compiler_moves(*KMC, True) == 'addu'
    # homemade C built with IDO stands for the library function: its moves are `or`
    code = _ido_code('extern int g(int, int);\nint f(int a, int b) { return g(b, a) + a; }\n')
    assert move_fingerprints.moves(code, True) == 'or'
    net = SimpleNamespace(cfg=lambda p: CFG, compiler_profile_usage=lambda p: [])
    fn = {'name': 'f', 'vram': 0x80000100, 'rom_offset': 10, 'target': code}
    chosen = deep_profiles.selected(net, {'big_endian': 1}, 1, fn)
    assert chosen[0] == {'toolchain': KMC[0], 'flags': KMC[1]}
    assert any(p['toolchain'].startswith('ido') for p in chosen[1:])
    # a GCC function gets none
    gcc_fn = {**fn, 'target': words(0x00801021, 0x03E00008, 0)}
    assert deep_profiles.selected(net, {'big_endian': 1}, 1, gcc_fn) == [{'toolchain': KMC[0], 'flags': KMC[1]}]


def test_a_function_without_moves_takes_its_neighbours_way():
    OR, ADDU, JR_RA, NOP = 0x00801025, 0x00801021, 0x03E00008, 0
    rows = {'before': [{'target': words(JR_RA, NOP)}, {'target': words(OR, JR_RA, NOP)}],
            'after': [{'target': words(OR, JR_RA, NOP)}]}
    store = SimpleNamespace(all=lambda query, *args: rows['before' if 'vram<' in query else 'after'])
    net = SimpleNamespace(project_store=lambda pid: store)
    fn = {'vram': 0x80000100, 'target': words(JR_RA, NOP)}
    assert move_fingerprints._neighbours(net, {'id': 1}, fn, True) == 'or'
    rows['after'] = [{'target': words(ADDU, JR_RA, NOP)}]
    assert move_fingerprints._neighbours(net, {'id': 1}, fn, True) is None  # the sides disagree
    assert move_fingerprints._neighbours(None, {}, fn, True) is None


LBU_T6_1_A1, SRA_T7_T6_6, ANDI_T8_T7_3 = 0x90AE0001, 0x000E7983, 0x31F80003  # lbu/sra/andi from a real IDO unpacker's shape
SRL_T7_T6_6, NOP, ADDIU_T6 = 0x000E7982, 0, 0x25CE0001  # srl t7,t6,6 / nop / addiu t6,t6,1


def test_a_shifted_loaded_byte_is_read_from_the_code():
    # lbu t6,1(a1); sra t7,t6,6; andi t8,t7,3: what IDO writes and no GCC does
    assert move_fingerprints.shifted_loads(words(LBU_T6_1_A1, SRA_T7_T6_6, ANDI_T8_T7_3), True) == 1
    # the same through a scheduler's gap, and twice
    assert move_fingerprints.shifted_loads(
        words(LBU_T6_1_A1, NOP, NOP, SRA_T7_T6_6, LBU_T6_1_A1, SRA_T7_T6_6), True) == 2
    # GCC's srl, a shift of something else, a reload or an overwrite in between, and nothing at all
    assert move_fingerprints.shifted_loads(words(LBU_T6_1_A1, SRL_T7_T6_6), True) == 0
    assert move_fingerprints.shifted_loads(words(LBU_T6_1_A1, NOP, NOP, NOP, NOP, SRA_T7_T6_6), True) == 0
    assert move_fingerprints.shifted_loads(words(LBU_T6_1_A1, ADDIU_T6, SRA_T7_T6_6), True) == 0
    assert move_fingerprints.shifted_loads(words(SRA_T7_T6_6, ANDI_T8_T7_3), True) == 0
    assert move_fingerprints.shifted_loads(b'', True) == 0
    # a little-endian reading of the same bytes is another function
    little = b''.join(w.to_bytes(4, 'little') for w in (LBU_T6_1_A1, SRA_T7_T6_6))
    assert move_fingerprints.shifted_loads(little, False) == 1


def test_a_byte_unpacker_without_moves_gets_an_ido_probe():
    if not (toolchains.installed(KMC[0]) and toolchains.installed('ido-5.3')):
        pytest.skip('Pinned KMC and IDO compilers are not installed; never download them here.')
    assert move_fingerprints.compiler_extracts(*KMC) is False  # KMC folds it to srl
    assert move_fingerprints.compiler_extracts('ido-5.3', list(__import__('decompnet').consoles.default_flags(CFG, 'ido-5.3'))) is True
    # homemade C built with IDO: no register move in it, only bit fields read out of bytes
    code = _ido_code('void f(unsigned char *p, short *h) { h[0] = (p[1] >> 6) & 3; h[1] = (p[2] >> 3) & 0x1F; }\n')
    assert move_fingerprints.moves(code, True) is None
    assert move_fingerprints.shifted_loads(code, True) >= 2
    net = SimpleNamespace(cfg=lambda p: CFG, compiler_profile_usage=lambda p: [])
    fn = {'name': 'f', 'vram': 0x80000100, 'rom_offset': 10, 'target': code}
    chosen = deep_profiles.selected(net, {'big_endian': 1}, 1, fn)
    assert chosen[0] == {'toolchain': KMC[0], 'flags': KMC[1]}
    assert any(p['toolchain'].startswith('ido') for p in chosen[1:])
    # the same bytes built with KMC itself add nothing
    gcc_fn = {**fn, 'target': words(0x03E00008, 0)}
    assert [p for p in deep_profiles.selected(net, {'big_endian': 1}, 1, gcc_fn) if p['toolchain'].startswith('ido')] == []
