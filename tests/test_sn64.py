"""The original SN pipeline feeds ELF objects to the ordinary MIPS judge."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import N64
from decompnet.consoles.n64.sn64 import assembler_input
from decompnet.consoles.n64.sn64 import compile_source as sn_compile
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config


@pytest.mark.parametrize('tid', ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0004', 'sn-n64-272-0006'])
def test_sn64_registered_as_full_n64_compiler(tid):
    tc = toolchains.get(tid)
    assert tid in [c.id for c in N64.compilers]
    assert tc.kind == 'compiler' and tc.arch == 'mips'
    assert tc.proprietary and tc.driver == 'consoles/n64/sn64.py'
    assert 'psyq-obj-parser' in tc.requires
    assert N64.m2c_target(tid) == 'mips-gcc'
    assert N64.up_front_guide(tid) == 'sn64'
    assert all(p['sha256'] for p in toolchains.get('psyq-obj-parser').parts)


def test_sn64_assembler_metadata_and_dos_lines():
    text = '.version "2.7"\n.type probe,@function\n.size probe,8\n.ent probe\nprobe:\n j $31\n nop\n.end probe\n'
    converted = assembler_input(text)
    assert '.version' not in converted and '.size' not in converted and '.type' not in converted
    assert '.ent probe\r\n' in converted and 'j $31\r\n' in converted
    assert '\n' not in converted.replace('\r\n', '')


def test_sn64_pipeline_order_and_failed_steps(tmp_path, monkeypatch):
    tools = {key: tmp_path / key for key in ['cc', 'cpp', 'assembler', 'converter', 'runner']}
    for tool in tools.values():
        tool.touch()
    src, out, inc = tmp_path / 'probe.c', tmp_path / 'probe.o', tmp_path / 'include'
    src.write_text('int probe(void){return 0;}')
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        assert kwargs['check'] and kwargs['timeout'] == 30
        output = Path(argv[argv.index('-o') + 1])
        output.write_bytes(b'\x7fELF\x01\x02' if argv[0] == str(tools['converter']) else b'.text\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.chdir(tmp_path)
    sn_compile(**tools, source=src, output=out, include=Path('include'), flags=['-O2', '-DTEST=1'])
    assert '-I' + str(inc) in seen[0]
    assert [Path(a[1]).name if a[0] == str(tools['runner']) else Path(a[0]).name for a in seen] == ['cpp', 'cc', 'assembler', 'converter']
    assert '-DTEST=1' in seen[0] and '-DTEST=1' not in seen[1]
    assert seen[-1][-2:] == ['-b', '-n']
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        sn_compile(**tools, source=src, output=out, include=inc, flags=[])


@pytest.mark.parametrize('tid', ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0004', 'sn-n64-272-0006'])
def test_original_sn64_compiles_and_preserves_relocations(tmp_path, tid):
    required = [tid, 'wibo', 'psyq-obj-parser']
    if any(toolchains.installed(t) is None for t in required):
        pytest.skip('needs accepted local SN64 compilers, wibo and the pinned converter')
    if not all((toolchains.require(tid) / name).is_file() for name in ['cc1n64.exe', 'cppn64.exe', 'asn64.exe']):
        pytest.skip('older compiler-only pack needs reinstalling with its preprocessor and assembler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    assert '{driver}' not in cfg.cmd and any('sn64.py' in arg for arg in cfg.cmd)
    result = compile_source(cfg, '', 'int probe(int a,int b){return a+b;}')
    assert result.ok, result.log
    fn = extract_function(result.obj, 'probe')
    assert fn.code == bytes.fromhex('03e0000800851021')
    result = compile_source(cfg, '', 'extern int target(int); extern int value; int probe(int x){return target(x)+value;}')
    assert result.ok, result.log
    fn = extract_function(result.obj, 'probe')
    assert {'target', 'value'} <= {r.symbol for r in fn.relocs}
    result = compile_source(cfg, '', 'float probe(float x){return x*1.25f;}')
    assert result.ok, result.log
    assert extract_function(result.obj, 'probe').code


def test_sn64_uses_gcc_flag_search_and_permuter_weights():
    from decompnet.app.engine.compilers import CompilerMixin
    from decompnet.app.permute import weight_family
    for tid in ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0004', 'sn-n64-272-0006']:
        assert weight_family({'compiler': {'toolchain': tid}}, None, False) == 'gcc'
        assert ['-O2', '-g3'] in CompilerMixin.debug_flag_variants(tid, ['-O2'])


def test_pinned_converter_build_uses_bounded_source_only_command(tmp_path, monkeypatch):
    calls = []
    def build(argv, **kwargs):
        calls.append((argv, kwargs))
        (tmp_path / 'psyq-obj-parser').touch()
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(subprocess, 'run', build)
    toolchains._build_psyq_parser(tmp_path, False, lambda *_: None)
    argv, options = calls[0]
    assert options['timeout'] == 300 and options['cwd'] == tmp_path
    assert '-std=c++20' in argv and '-lz' in argv
    assert all('source/pcsx/' in arg for arg in argv if arg.endswith('.cc'))


def test_n64_export_preserves_out_of_isa_pref_word():
    text = '    /* 0010 80001000 CCB0F043 */ pref 0x10,-0xFBD($a1)\n'
    assert '.word 0xCCB0F043' in N64.export_asm(text)
    assert N64.export_asm('jr $ra\n') == 'jr $ra\n'


def test_sn64_driver_and_all_tools_are_staged_for_wsl(tmp_path, monkeypatch):
    from decompnet import wsl
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'windows-x86_64')
    monkeypatch.setattr(wsl, 'wanted', lambda: True)
    monkeypatch.setattr(wsl, 'status', lambda: (True, 'ready'))
    directories = (tmp_path / 'compiler', tmp_path / 'runner', tmp_path / 'converter')
    monkeypatch.setattr(toolchains, 'runtime_dirs', lambda _: directories)
    seen = []
    def prepare(paths):
        seen.extend(paths)
        return tuple('/tools/' + p.name for p in paths)
    monkeypatch.setattr(wsl, 'prepare_tools', prepare)
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'sn-n64-281'}})
    assert cfg.wsl
    assert seen[:3] == list(directories)
    assert seen[-1].name == 'n64'
    assert '/tools/n64/sn64.py' in cfg.cmd
    assert '/tools/compiler/cc1n64.exe' in cfg.cmd
    assert '/tools/converter/psyq-obj-parser' in cfg.cmd
    assert '{driver}' not in cfg.cmd


def test_sn64_readonly_sections_keep_jump_table_data_and_symbol_names():
    asm = '.section .rodata\n.rodata\n.section ".rodata", "a"\njtbl:\n.word .L1\n.text\n.L1:\nj $31\nnop\n# .rodata is data, not an identifier replacement\n'
    converted = assembler_input(asm)
    assert '.section .rdata' in converted and '\r\n.rdata\r\n' in converted
    assert '.word .L1' in converted and 'jtbl:' in converted and '.L1:' in converted
    assert '# .rodata' in converted


def test_gcc_sn_switch_readonly_object_converts(tmp_path):
    tid = 'gcc-2.8.1-pm-sn-as'
    if any(toolchains.installed(t) is None for t in [tid, 'wibo', 'psyq-obj-parser']):
        pytest.skip('needs installed GCC/SN pipeline')
    cfg = compiler_config(tmp_path, {'compiler':{'toolchain':tid}})
    cases = ' '.join(f'case {i}: return call({i*7+3});' for i in range(10))
    result = compile_source(cfg, '', 'extern int call(int); int probe(int x){switch(x){'+cases+' default:return 0;}}')
    assert result.ok, result.log
    fn = extract_function(result.obj, 'probe')
    assert fn.code
    assert any(r.symbol == 'call' for r in fn.relocs)
