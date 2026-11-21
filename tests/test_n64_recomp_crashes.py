"""Only a body that independently reproduces a native crash can be set aside from static recompilation."""
import signal
import subprocess
import tomllib
from pathlib import Path

import pytest

from decompnet.consoles.n64 import recomp
from tests.test_n64_recomp import SEGMENTS, SPLIT


def setup(tmp_path):
    functions = SPLIT + [{'name': 'bad_switch', 'vram': 0x80000480, 'rom': 0x1080, 'size': 0x10}]
    (tmp_path / 'syms.toml').write_text(recomp.symbols(SEGMENTS, functions))
    return {'entry', 'listed', 'bad_switch'}


def test_a_native_crash_is_isolated_without_guessing_from_a_partial_output(tmp_path, monkeypatch):
    names = setup(tmp_path)
    calls = []
    def run(argv, **kwargs):
        cfg = tomllib.loads((tmp_path / 'recomp.toml').read_text())
        active = names - set(cfg['patches']['stubs'])
        calls.append(active)
        return subprocess.CompletedProcess(argv, -signal.SIGSEGV if 'bad_switch' in active else 0, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom.z64'), None, refused, set(), lambda: False, lambda *a: None)
    assert set(refused) == {'bad_switch'} and 'SIGSEGV' in refused['bad_switch']
    assert set() in calls and {'bad_switch'} in calls
    assert calls[-1] == {'entry', 'listed'}, 'the final output must translate every non-refused body'


def test_a_global_crash_does_not_blame_an_arbitrary_body(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(recomp.subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, -signal.SIGSEGV, '', ''))
    refused = {}
    with pytest.raises(recomp.RecompError, match='SIGSEGV.*no diagnostic output'):
        recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert not refused


def test_resource_kills_and_plain_failures_are_reported_without_body_probes(tmp_path, monkeypatch):
    setup(tmp_path)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, -9, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    with pytest.raises(recomp.RecompError, match='exit -9'):
        recomp._translate('tool', tmp_path, Path('rom'), None, {}, set(), lambda: False, lambda *a: None)
    assert len(calls) == 1


def test_a_normal_refusal_during_a_probe_does_not_hide_a_later_crash(tmp_path, monkeypatch):
    names = setup(tmp_path)
    def run(argv, **kwargs):
        active = names - set(tomllib.loads((tmp_path / 'recomp.toml').read_text())['patches']['stubs'])
        if 'listed' in active:
            return subprocess.CompletedProcess(argv, 1, '', 'unsupported instruction\nError recompiling listed\n')
        return subprocess.CompletedProcess(argv, -signal.SIGSEGV if 'bad_switch' in active else 0, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert set(refused) == {'listed', 'bad_switch'}
    assert 'unsupported instruction' in refused['listed'] and 'SIGSEGV' in refused['bad_switch']


def test_entrypoint_probes_use_the_clis_renamed_body(tmp_path):
    setup(tmp_path)
    assert recomp._function_names(tmp_path, 0x80000400) == {'recomp_entrypoint', 'listed', 'bad_switch'}


def test_stop_and_the_run_budget_apply_to_probes_too(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(recomp.subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, -signal.SIGSEGV, '', ''))
    monkeypatch.setattr(recomp, 'MAX_PROBE_RUNS', 0)
    with pytest.raises(recomp.RecompError, match='safety limit'):
        recomp._translate('tool', tmp_path, Path('rom'), None, {}, set(), lambda: False, lambda *a: None)
    with pytest.raises(recomp.RecompError, match='Stopped'):
        recomp._translate('tool', tmp_path, Path('rom'), None, {}, set(), lambda: True, lambda *a: None)


def test_builtin_cli_renames_are_resolved_before_body_isolation(tmp_path, monkeypatch):
    names = setup(tmp_path)
    sections = tomllib.loads((tmp_path / 'syms.toml').read_text())['section']
    functions = sections[0]['functions'] + [{'name': '__moddi3', 'vram': 0x800004A0, 'rom': 0x10A0, 'size': 8}]
    (tmp_path / 'syms.toml').write_text(recomp.symbols(SEGMENTS, [dict(f, rom=0x1000+f['vram']-0x80000400) for f in functions]))
    def run(argv, **kwargs):
        stubs = set(tomllib.loads((tmp_path / 'recomp.toml').read_text())['patches']['stubs'])
        if '__moddi3' in stubs:
            return subprocess.CompletedProcess(argv, 1, '', 'Function __moddi3 is stubbed out in the config file but does not exist!')
        active = names - stubs
        return subprocess.CompletedProcess(argv, -signal.SIGSEGV if 'bad_switch' in active else 0, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert set(refused) == {'bad_switch'}


def test_cli_name_resolution_does_not_exhaust_the_body_probe_budget(tmp_path, monkeypatch):
    setup(tmp_path)
    path = tmp_path / 'syms.toml'
    sections = tomllib.loads(path.read_text())['section']
    names = {f'library_{i}' for i in range(12)}
    aliases = [dict(name=name, vram=0x80000500 + 4*i, size=4) for i, name in enumerate(sorted(names))]
    functions = sections[0]['functions'] + aliases
    path.write_text(recomp.symbols(
        SEGMENTS, [dict(f, rom=0x1000 + f['vram'] - 0x80000400) for f in functions]))
    monkeypatch.setattr(recomp, 'MAX_PROBE_RUNS', 8)

    def run(argv, **kwargs):
        stubs = set(tomllib.loads((tmp_path / 'recomp.toml').read_text())['patches']['stubs'])
        if unresolved := sorted(stubs & names):
            return subprocess.CompletedProcess(
                argv, 1, '', f'Function {unresolved[0]} is stubbed out in the config file but does not exist!')
        return subprocess.CompletedProcess(argv, -signal.SIGSEGV if 'bad_switch' not in stubs else 0, '', '')

    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert set(refused) == {'bad_switch'}


def test_a_crash_requiring_two_bodies_is_not_guessed_to_be_one_body(tmp_path, monkeypatch):
    names = setup(tmp_path)
    def run(argv, **kwargs):
        active = names - set(tomllib.loads((tmp_path / 'recomp.toml').read_text())['patches']['stubs'])
        return subprocess.CompletedProcess(argv, -signal.SIGSEGV if {'entry', 'bad_switch'} <= active else 0, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    with pytest.raises(recomp.RecompError, match='SIGSEGV'):
        recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert not refused


def test_a_homemade_invalid_jump_table_keeps_healthy_functions(tmp_path):
    from decompnet import limits, toolchains
    from tests.test_n64_recomp import homemade_rom
    if toolchains.installed('n64recomp') is None:
        pytest.skip('needs the pinned N64Recomp tool')
    limits.no_core_dumps()
    rom = bytearray(homemade_rom())
    # lui t9,0x9000; addu t9,t9,a0; lw t9,0(t9); jr t9; nop. The CLI's linear analyser treats this as a table
    # outside the ROM and used to take down the entire job without reporting which body it was analysing.
    for i, word in enumerate([0x3C199000, 0x0324C821, 0x8F390000, 0x03200008, 0]):
        rom[0x1060+4*i:0x1064+4*i] = word.to_bytes(4, 'big')
    image = tmp_path / 'game.z64'
    image.write_bytes(rom)
    functions = SPLIT + [{'name': 'bad_switch', 'vram': 0x80000460, 'rom': 0x1060, 'size': 20}]
    summary = recomp.recompile(toolchains.binary('n64recomp'), image, SEGMENTS, functions, tmp_path/'out', log=lambda *a: None)
    assert [r['name'] for r in summary['refused']] == ['bad_switch']
    assert 'SIGSEGV' in summary['refused'][0]['why'] or 'SIGBUS' in summary['refused'][0]['why']
    assert summary['error_count'] == 0
    assert 'RECOMP_FUNC void listed' in ''.join(f.read_text() for f in (tmp_path/'out/funcs').glob('*.c'))


def test_generated_static_failures_are_isolated_to_a_patchable_input_body(tmp_path, monkeypatch):
    names = setup(tmp_path)
    def run(argv, **kwargs):
        active = names - set(tomllib.loads((tmp_path / 'recomp.toml').read_text())['patches']['stubs'])
        if 'bad_switch' in active:
            return subprocess.CompletedProcess(argv, 1, '', 'unsupported instruction\nError recompiling static_0_80000488\n')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda: False, lambda *a: None)
    assert set(refused) == {'bad_switch'}
    assert 'generated body static_0_80000488' in refused['bad_switch']


def test_a_large_game_finishes_all_normal_refusals_in_one_run(tmp_path, monkeypatch):
    functions = [dict(name=f'body_{i}', vram=0x80001000+4*i, rom=0x1000+4*i, size=4) for i in range(405)]
    segments = [dict(name='main', vram=0x80001000, rom_start=0x1000, rom_end=0x2000)]
    (tmp_path/'syms.toml').write_text(recomp.symbols(segments, functions))
    def run(argv, **kwargs):
        stubs = set(tomllib.loads((tmp_path/'recomp.toml').read_text())['patches']['stubs'])
        name = next((f['name'] for f in functions if f['name'] not in stubs), None)
        return subprocess.CompletedProcess(argv, 1 if name else 0, '', f'unsupported instruction\nError recompiling {name}\n' if name else '')
    monkeypatch.setattr(recomp.subprocess, 'run', run)
    refused = {}
    recomp._translate('tool', tmp_path, Path('rom'), None, refused, set(), lambda:False, lambda *a:None)
    assert len(refused) == 405


def test_only_compiler_errors_inside_a_generated_body_can_refuse_it(tmp_path):
    folder = tmp_path/'funcs'
    folder.mkdir()
    source = folder/'unit.c'
    source.write_text('RECOMP_FUNC void listed(void) {\n goto missing;\n}\nextern missing_type global;\n')
    errors = [f'{source}:2:2: error: use of undeclared label',
              f'{source}:4:8: error: unknown type name', f'{folder}/recomp.h:2:1: error: header error']
    assert recomp._invalid_bodies(tmp_path, errors) == {'listed': 'use of undeclared label'}
    assert not recomp._invalid_bodies(tmp_path, errors[1:])


def test_bad_generated_c_is_retranslated_as_an_explicit_stub(tmp_path, monkeypatch):
    from tests.test_n64_recomp import homemade_rom
    image = tmp_path/'game.z64'
    image.write_bytes(homemade_rom())
    out = tmp_path/'out'
    calls = []
    def translate(program, folder, rom, entry, refused, renamed, stop, log):
        calls.append(dict(refused))
        (folder/'funcs').mkdir(exist_ok=True)
        (folder/'funcs/unit.c').write_text('RECOMP_FUNC void listed(void) {\n goto missing;\n}\n')
    def check(folder, include, stop):
        errors = [] if calls[-1] else [f'{folder}/funcs/unit.c:2:2: error: use of undeclared label']
        return [], set(), errors
    monkeypatch.setattr(recomp, '_translate', translate)
    monkeypatch.setattr(recomp, 'check', check)
    summary = recomp.recompile(Path('tool'), image, SEGMENTS, SPLIT, out, log=lambda *a:None)
    assert len(calls) == 2 and 'listed' in calls[-1]
    assert summary['error_count'] == 0
    assert summary['refused'][0]['name'] == 'listed'
    assert 'generated C does not compile' in summary['refused'][0]['why']
