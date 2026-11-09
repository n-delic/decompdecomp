"""Distinct N64 code generators retain their assemblers and use the ordinary judge."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app.engine.compilers import CompilerMixin
from decompnet.app.permute import weight_family
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import N64
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config
from decompnet.rom.match import RomTarget, link
from decompnet.rom.symbols import Symbol, SymbolTable

EXTRA = ['gcc-2.8.1-pm-sn-as', 'gcc-2.8.1-pm', 'gcc-egcs-2.91.66', 'sn-n64-272-0004', 'sn-n64-272-0006', 'sn-n64-272-970404']


@pytest.mark.parametrize('tid', EXTRA)
def test_extra_n64_profiles_are_pinned_and_reach_matching_and_flag_search(tid):
    tc = toolchains.get(tid)
    assert tid in [c.id for c in N64.compilers]
    assert tc.arch == 'mips' and tc.platform == 'N64'
    assert tc.family == 'gcc'
    assert all(len(p['sha256']) == 64 for p in tc.parts)
    assert N64.m2c_target(tid) == 'mips-gcc'
    assert N64.up_front_guide(tid)
    assert ['-O2', '-g3'] in CompilerMixin.debug_flag_variants(tid, ['-O2'])
    assert weight_family({'compiler': {'toolchain': tid}}, None, False) == 'gcc'
    assert tc.parts_for('linux-x86_64') and tc.parts_for('macos-arm64') and tc.parts_for('macos-x86_64')


@pytest.mark.parametrize('tid', EXTRA)
def test_extra_compilers_generate_real_elf_and_external_relocations(tmp_path, tid):
    if any(toolchains.installed(t) is None for t in [tid, *toolchains.get(tid).requires]):
        pytest.skip('requires locally installed compiler and dependencies (proprietary packs require acceptance)')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    for source in ['int probe(int a,int b){return a+b;}',
                   'extern int target(int); extern int value; int probe(int x){return target(x)+value;}',
                   'float probe(float x){return x*1.25f;}',
                   'int probe(int x){if(x<0)return -x;return x+3;}']:
        result = compile_source(cfg, '', source)
        assert result.ok, result.log
        fn = extract_function(result.obj, 'probe')
        assert fn.code and result.obj[:6] == b'\x7fELF\x01\x02'
        if 'target(' in source:
            assert {'target', 'value'} <= {r.symbol for r in fn.relocs}
        if source.startswith('int probe(int a'):
            assert fn.code == bytes.fromhex('03e0000800851021')
            target = RomTarget('probe', 0x80001000, fn.code, [])
            verdict = link(result.obj, 'probe', target, SymbolTable(), lambda *_: None)
            assert verdict.exact, verdict.problems
        if 'target(' in source:
            target = RomTarget('probe', 0x80001000,
                               bytes.fromhex('27bdffe8afbf00100c004000000000003c0380038c6380008fbf00100043102103e0000827bd0018'),
                               [(8, 4, 0x80010000), (16, 5, 0x80028000), (20, 6, 0x80028000)])
            symbols = SymbolTable([Symbol('target', 0x80010000, kind='func'), Symbol('value', 0x80028000)])
            verdict = link(result.obj, 'probe', target, symbols, lambda *_: None)
            assert verdict.exact, verdict.problems


def test_reconstructed_sn_native_build_supplies_only_host_assertion_helper(tmp_path, monkeypatch):
    archive = tmp_path / 'source.tar.gz'
    dest = tmp_path / 'out'
    dest.mkdir()
    def extract(archive, source, subdir):
        (source / 'getpwd.c').write_text('/* upstream host source */')
    def build(argv, **kwargs):
        source = kwargs['cwd']
        assert '__eprintf' in (source / 'getpwd.c').read_text()
        assert 'cpp' in argv and 'cc1' in argv and 'CC=/usr/bin/clang' in argv
        assert kwargs['timeout'] == 900
        for name in ['cpp', 'cc1']:
            (source / name).write_text('native')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(toolchains, '_extract', extract)
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.limits, 'capped', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', build)
    toolchains._build_sn64_gcc(archive, dest, 'pinned/source', lambda *_: None)
    assert (dest / 'cc1').read_text() == 'native'
    assert (dest / 'cpp').read_text() == 'native'


def test_native_sn_driver_keeps_preprocessor_defines_and_small_data_consistent(tmp_path, monkeypatch):
    from decompnet.consoles.n64.sn64 import compile_native_source
    tools = {key: tmp_path / key for key in ['cc', 'cpp', 'assembler', 'wrapper']}
    for p in tools.values():
        p.touch()
    source, output = tmp_path / 'probe.c', tmp_path / 'probe.o'
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        out = Path(argv[argv.index('-o')+1])
        out.write_bytes(b'\x7fELF\x01\x02' if len(calls) == 3 else b'.text\n')
        assert kwargs['check'] and kwargs['timeout'] == 30
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    compile_native_source(**tools, source=source, output=output, include=tmp_path, flags=['-O2', '-G8', '-DTEST=1'])
    assert '-DTEST=1' in calls[0] and '-DTEST=1' not in calls[1]
    assert '-G8' in calls[1] and '-G8' in calls[2]
    assert '-EB' in calls[2] and str(tools['wrapper']) in calls[2]


def test_gcc_sn_pair_preserves_generated_instructions_and_uses_explicit_tools(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import sn64

    tools = [tmp_path / name for name in ['gcc', 'asn64.exe', 'converter', 'wibo']]
    for tool in tools:
        tool.touch()
    calls = []
    original = '.type probe,@function\nprobe:\n\tjr $ra\n\taddu $v0,$a0,$a1\n'
    output = tmp_path / 'probe.o'
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['check'] and kwargs['timeout'] == 30
        if len(calls) == 1:
            assert '-S' in argv and '-O2' in argv
            (tmp_path / 'probe.sn.s').write_text(original)
        elif len(calls) == 2:
            assert (tmp_path / 'probe.sn.s').read_bytes() == sn64.assembler_input(original).encode()
        else:
            output.write_bytes(b'\x7fELF\x01\x02')
    monkeypatch.setattr(sn64.subprocess, 'run', run)
    sn64.compile_gcc_source(*tools, tmp_path / 'probe.c', output, tmp_path, ['-O2'])
    assert calls[1][:3] == [str(tools[3]), str(tools[1]), '-mips3']
    assert calls[2][0] == str(tools[2])


def test_installed_pair_supports_export_style_relative_builds(tmp_path):
    from decompnet.compiler import run_in

    tid = 'gcc-2.8.1-pm-sn-as'
    if any(toolchains.installed(t) is None for t in [tid, *toolchains.get(tid).requires]):
        pytest.skip('requires accepted local compiler/assembler pairing')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    (tmp_path / 'include').mkdir()
    (tmp_path / 'source.c').write_text('int probe(int a,int b){return a+b;}\n')
    subst = {'{src}': 'source.c', '{obj}': 'source.o', '{include}': 'include'}
    argv = [subst.get(arg, arg) for arg in cfg.cmd]
    result = run_in(cfg, tmp_path, argv, inputs=['source.c'], outputs=['source.o'])
    assert result.returncode == 0, result.stderr
    fn = extract_function((tmp_path / 'source.o').read_bytes(), 'probe')
    assert fn.code == bytes.fromhex('03e0000800851021')
