"""The original SN pipeline feeds ELF objects to the ordinary MIPS judge."""
import shutil
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


@pytest.mark.parametrize('tid', ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0004', 'sn-n64-272-0006'])
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
    src, out, inc = tmp_path / 'src/code_80132004/func_801382A0.c', tmp_path / 'build/src/code_80132004/func_801382A0.o', tmp_path / 'include'
    src.parent.mkdir(parents=True)
    out.parent.mkdir(parents=True)
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
    sn_compile(**tools, source=src.relative_to(tmp_path), output=out.relative_to(tmp_path),
               include=Path('include'), flags=['-O2', '-DTEST=1'])
    assert seen[0][-3:] == [str(src), '-o', str(out.with_suffix('.sn.i'))]
    assert seen[2][-5:] == ['-I', str(inc), '-o', str(out.with_suffix('.sn.obj')), str(out.with_suffix('.sn.s'))]
    assert '-I' + str(inc) in seen[0]
    assert [Path(a[1]).name if a[0] == str(tools['runner']) else Path(a[0]).name for a in seen] == ['cpp', 'cc', 'assembler', 'converter']
    assert '-DTEST=1' in seen[0] and '-DTEST=1' not in seen[1]
    assert seen[-1][-2:] == ['-b', '-n']
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        sn_compile(**tools, source=src, output=out, include=inc, flags=[])


@pytest.mark.parametrize('tid', ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0004', 'sn-n64-272-0006'])
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


@pytest.mark.parametrize('tid', ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0004', 'sn-n64-272-0006'])
def test_original_sn64_supports_export_style_relative_builds(tmp_path, tid):
    from decompnet.compiler import run_in

    if any(toolchains.installed(t) is None for t in [tid, 'wibo', 'psyq-obj-parser']):
        pytest.skip('needs accepted local SN64 compilers, wibo and the pinned converter')
    if not all((toolchains.require(tid) / name).is_file() for name in ['cc1n64.exe', 'cppn64.exe', 'asn64.exe']):
        pytest.skip('older compiler-only pack needs reinstalling with its preprocessor and assembler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    (tmp_path / 'include').mkdir()
    (tmp_path / 'include/probe.h').write_text('#define VALUE 3\n')
    (tmp_path / 'src/code_80132004').mkdir(parents=True)
    (tmp_path / 'build/src/code_80132004').mkdir(parents=True)
    (tmp_path / 'src/code_80132004/func_801382A0.c').write_text('#include "probe.h"\nint probe(int x){return x+VALUE;}\n')
    subst = {'{src}': 'src/code_80132004/func_801382A0.c', '{obj}': 'build/src/code_80132004/func_801382A0.o', '{include}': 'include'}
    result = run_in(cfg, tmp_path, [subst.get(arg, arg) for arg in cfg.cmd],
                    inputs=['src/code_80132004/func_801382A0.c', 'include/probe.h'], outputs=['build/src/code_80132004/func_801382A0.o'])
    assert result.returncode == 0, result.stderr
    fn = extract_function((tmp_path / 'build/src/code_80132004/func_801382A0.o').read_bytes(), 'probe')
    assert fn.code == bytes.fromhex('03e0000824820003')


def test_sn64_uses_gcc_flag_search_and_permuter_weights():
    from decompnet.app.engine.compilers import CompilerMixin
    from decompnet.app.permute import weight_family
    for tid in ['sn-n64-281', 'sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0004', 'sn-n64-272-0006']:
        assert weight_family({'compiler': {'toolchain': tid}}, None, False) == 'gcc'
        assert ['-O2', '-g3'] in CompilerMixin.debug_flag_variants(tid, ['-O2'])


def test_pinned_converter_build_uses_bounded_source_only_command(tmp_path, monkeypatch):
    calls = []
    source = tmp_path / 'source/pcsx/src/supportpsx/psyq-obj.cc'
    source.parent.mkdir(parents=True)
    source.write_text('        {".data", ELFIO::SHF_ALLOC | ELFIO::SHF_WRITE},')
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
    assert '{".ctors", ELFIO::SHF_ALLOC | ELFIO::SHF_WRITE}' in source.read_text()
    assert '{".dtors", ELFIO::SHF_ALLOC | ELFIO::SHF_WRITE}' in source.read_text()


def test_converter_recipe_update_marks_old_install_for_reinstall():
    from dataclasses import replace

    converter = toolchains.get('psyq-obj-parser')
    old = replace(converter, build_revision='')
    manifest = {'fingerprint': toolchains.fingerprint(old)}
    assert toolchains.changed(converter.id, manifest)


def test_converter_build_rejects_unexpected_source(tmp_path):
    source = tmp_path / 'source/pcsx/src/supportpsx/psyq-obj.cc'
    source.parent.mkdir(parents=True)
    source.write_text('unexpected source')
    with pytest.raises(toolchains.ToolchainError, match='section table changed'):
        toolchains._build_psyq_parser(tmp_path, False, lambda *_: None)


def test_converter_preserves_cpp_constructor_and_destructor_tables(tmp_path):
    from elftools.elf.elffile import ELFFile

    source = toolchains.path('psyq-obj-parser') / 'source'
    compiler = toolchains.path('sn-n64-272-0006')
    runner = toolchains.path('wibo') / 'wibo'
    if not source.is_dir() or not (compiler / 'cc1pln64.exe').is_file() or not runner.is_file():
        pytest.skip('needs the pinned converter source and accepted SN64 C++ compiler')
    shutil.copytree(source, tmp_path / 'source')
    toolchains._build_psyq_parser(tmp_path, False, lambda *_: None)
    src, obj = tmp_path / 'global.cpp', tmp_path / 'global.o'
    src.write_text('class Count { public: int n; Count(); ~Count(); }; '
                   'Count::Count() { n=1; } Count::~Count() { n=0; } Count global;')
    sn_compile(compiler / 'cc1pln64.exe', compiler / 'cppn64.exe', compiler / 'asn64.exe',
               tmp_path / 'psyq-obj-parser', runner, src, obj, tmp_path,
               ['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mhard-float'])
    with obj.open('rb') as file:
        elf = ELFFile(file)
        for name in ('.ctors', '.dtors'):
            section = elf.get_section_by_name(name)
            relocation = elf.get_section_by_name('.rel' + name)
            assert section is not None and section['sh_type'] == 'SHT_PROGBITS'
            assert section['sh_flags'] & 3 == 3 and len(section.data()) == 4
            assert relocation is not None and relocation.num_relocations() == 1
            assert next(relocation.iter_relocations())['r_info_type'] == 2  # R_MIPS_32


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


def test_sn_0001_with_0004_assembler_reuses_the_pinned_downloads():
    """The pairing downloads nothing new: 0001's archive and 0004's assembler, each under its pinned checksum."""
    pair = toolchains.get('sn-n64-272-0001-as0004')
    assert pair.proprietary and pair.requires == toolchains.get('sn-n64-272-0001').requires
    pins = {(p['url'], p['sha256']) for p in pair.parts}
    assert pins <= {(p['url'], p['sha256']) for t in ('sn-n64-272-0001', 'sn-n64-272-0004') for p in toolchains.get(t).parts}
    # the 0004 assembler is saved under its own name, so 0001's own asn64.exe stays beside it
    assert {p.get('binary') for p in pair.parts} == {None, 'asn64-0004.exe'}
    assert '{tc}/asn64-0004.exe' in pair.cmd and '{tc}/cc1n64.exe' in pair.cmd
    assert pair.flags == toolchains.get('sn-n64-272-0001').flags
    assert pair.own_copy is False


def test_sn_0001_with_0004_assembler_loads_doubles_with_one_ldc1(tmp_path):
    ids = ['sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0004']
    if any(toolchains.installed(t) is None for t in [*ids, 'wibo', 'psyq-obj-parser']):
        pytest.skip('needs the installed SN 2.7.2 packs (proprietary: accepted in Setup)')
    code = {}
    for tid in ids:
        result = compile_source(compiler_config(tmp_path, {'compiler': {'toolchain': tid}}), '',
                                'double g; double probe(double x){return x*g+2.5;}')
        assert result.ok, result.log
        code[tid] = extract_function(result.obj, 'probe').code
    ldc1 = lambda c: [c[i:i + 4] for i in range(0, len(c), 4) if c[i] >> 2 == 0x35]
    assert not ldc1(code[ids[0]]) and len(ldc1(code[ids[1]])) == 2  # 0001 splits l.d into lwc1 pairs
    assert code[ids[1]] == code[ids[2]]  # same code here as 0004, whose compiler differs elsewhere


def test_native_cpp_only_for_a_compared_cppn64_on_a_mac(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import sn64
    compared, other, native = tmp_path / 'cppn64.exe', tmp_path / 'other.exe', tmp_path / 'cpp'
    compared.write_bytes(b'compared build');other.write_bytes(b'another build');native.touch()
    monkeypatch.setattr(sn64, 'NATIVE_CPP', {sn64.hashlib.sha256(b'compared build').hexdigest()})
    monkeypatch.setattr(sn64.sys, 'platform', 'darwin')
    assert sn64.native_cpp(compared, native) == native
    assert sn64.native_cpp(other, native) is None  # (0004, 0006 and 2.8.1 ship their own preprocessors)
    assert sn64.native_cpp(compared, None) is None and sn64.native_cpp(compared, tmp_path / 'missing') is None
    monkeypatch.setattr(sn64.sys, 'platform', 'linux')  # (wibo is native there: starting it costs little)
    assert sn64.native_cpp(compared, native) is None


def test_the_native_cpp_hands_cc1_the_same_crlf_text(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import sn64
    tools = {key: tmp_path / key for key in ['cc', 'cpp', 'assembler', 'converter', 'runner']}
    for tool in tools.values():
        tool.touch()
    native = tmp_path / 'native-cpp';native.touch()
    monkeypatch.setattr(sn64, 'native_cpp', lambda cpp, given: given)
    src, out = tmp_path / 'probe.c', tmp_path / 'probe.o'
    src.write_text('int probe(void){return 0;}')
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        output = Path(argv[argv.index('-o') + 1])
        if argv[0] == str(native):
            output.write_bytes(b'int probe(void)\n{return 0;}\n')
        elif Path(argv[1] if argv[0] == str(tools['runner']) else argv[0]).name == 'cc':
            assert Path(argv[-3]).read_bytes() == b'int probe(void)\r\n{return 0;}\r\n'
            output.write_bytes(b'.text\n')
        else:
            output.write_bytes(b'\x7fELF\x01\x02' if argv[0] == str(tools['converter']) else b'.text\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    sn_compile(**tools, source=src, output=out, include=tmp_path, flags=['-O2', '-DTEST=1'], native=native)
    assert seen[0][0] == str(native) and '-DTEST=1' in seen[0] and seen[0][-3:] == [str(src), '-o', str(out.with_suffix('.sn.i'))]
    assert seen[1][:2] == [str(tools['runner']), str(tools['cc'])]


@pytest.mark.parametrize('present', [True, False])
def test_an_optional_tool_is_mounted_when_installed_and_its_argument_empty_when_not(tmp_path, monkeypatch, present):
    tc = toolchains.get('sn-n64-272-0001')
    assert tc.optional == ['sn-n64-272-970404'] and 'sn-n64-272-970404' not in tc.requires
    folders = {tid: tmp_path / tid for tid in ['sn-n64-272-0001', *tc.requires, 'sn-n64-272-970404']}
    monkeypatch.setattr(toolchains, 'runtime_dirs', lambda tid: tuple(folders[t] for t in [tid, *toolchains.get(tid).requires]))
    monkeypatch.setattr(toolchains, 'installed', lambda tid, *a, **k: {} if present or tid != 'sn-n64-272-970404' else None)
    monkeypatch.setattr(toolchains, 'path', lambda tid, *a, **k: folders[tid])
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'sn-n64-272-0001'}})
    given = cfg.cmd[cfg.cmd.index('--native-cpp') + 1]
    assert not any('{tc?:' in arg for arg in cfg.cmd)
    if present:
        assert given == str(folders['sn-n64-272-970404'] / 'cpp') and str(folders['sn-n64-272-970404']) in cfg.ro_paths
    else:
        assert given == '' and str(folders['sn-n64-272-970404']) not in cfg.ro_paths


def built(obj):
    """What an object holds, without the source-file symbol the converter names after its temporary file (two runs
    of the same pipeline differ there)."""
    import io

    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection
    from elftools.elf.sections import SymbolTableSection
    elf = ELFFile(io.BytesIO(obj))
    value = []
    for section in elf.iter_sections():
        if isinstance(section, SymbolTableSection):
            value.append(sorted((s.name, s['st_value'], s['st_size'], s['st_info']['type'], s['st_shndx'])
                                for s in section.iter_symbols() if s['st_info']['type'] != 'STT_FILE'))
        elif isinstance(section, RelocationSection):
            value.append((section.name, [(r['r_offset'], r['r_info_type']) for r in section.iter_relocations()]))
        elif section['sh_type'] in ('SHT_PROGBITS', 'SHT_NOBITS'):
            value.append((section.name, section['sh_size'], section.data() if section['sh_type'] == 'SHT_PROGBITS' else b''))
    return value


def test_native_cpp_builds_the_same_objects_as_cppn64(tmp_path):
    """Real compilers: the same code, data, relocations and symbols whether build 0001's preprocessor runs under
    wibo or natively."""
    import sys
    tid = 'sn-n64-272-0001'
    if sys.platform != 'darwin' or any(toolchains.installed(t) is None for t in [tid, 'wibo', 'psyq-obj-parser', 'sn-n64-272-970404']):
        pytest.skip('needs a Mac with SN64 0001, wibo, the converter and the reconstructed SN64 GCC installed')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    assert cfg.cmd[cfg.cmd.index('--native-cpp') + 1].endswith('/cpp')
    without = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    without.cmd = [arg if not arg.endswith('sn-n64-272-970404/cpp') else '' for arg in without.cmd]
    header = '#define SQ(x) ((x)*(x))\ntypedef struct { int a, b; } Pair;\nextern int gValue;\n'
    for source in ['int probe(int a,int b){return a+b;}',
                   'int probe(Pair *p, int n) { int i, t = 0; for (i = 0; i < n; i++) t += SQ(p[i].a) - p[i].b; return t + gValue; }',
                   'float probe(float x){ /* comment */ return x*1.25f; }\n#line 40 "other.c"\nint second(void){return __LINE__;}']:
        native, original = compile_source(cfg, header, source), compile_source(without, header, source)
        assert native.ok and original.ok, (native.log, original.log)
        assert built(native.obj) == built(original.obj)


@pytest.mark.parametrize('tid', ['sn-n64-272-0001', 'sn-n64-272-0001-as0004', 'sn-n64-272-0006'])
def test_original_sn64_uses_its_cpp_frontend_for_methods(tmp_path, tid):
    if any(toolchains.installed(t) is None for t in (tid, 'wibo', 'psyq-obj-parser')):
        pytest.skip('needs the accepted SN64 compiler and pinned tools')
    if not (toolchains.require(tid) / 'cc1pln64.exe').is_file():
        pytest.skip('the C++ frontend is absent; reinstall the pinned compiler pack')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid}})
    assert cfg.cplusplus
    c = compile_source(cfg, '', 'int probe(int a,int b){return a+b;}')
    assert c.ok and c.language == 'c', c.log
    assert extract_function(c.obj, 'probe').code == bytes.fromhex('03e0000800851021')
    cpp = compile_source(cfg, '', '#ifndef __cplusplus\n#error expected C++\n#endif\n'
                         'class Counter { public: int value; int next(); }; '
                         'int Counter::next(){return ++value;}')
    assert cpp.ok and cpp.language == 'c++', cpp.log
    code = bytes.fromhex('8c8200002442000103e00008ac820000')
    assert extract_function(cpp.obj, 'next__7Counter').code == code
    from decompnet.rom.match import RomTarget, link
    from decompnet.rom.symbols import SymbolTable
    verdict = link(cpp.obj, 'next__7Counter', RomTarget('next__7Counter', 0x80001000, code, []),
                   SymbolTable([]), lambda *_: None)
    assert verdict.exact, verdict.problems


def test_sn64_cpp_preprocessor_defines_the_cpp_language(tmp_path, monkeypatch):
    tools = {name: tmp_path / name for name in ('cc1pln64.exe', 'cpp', 'assembler', 'converter', 'runner')}
    for path in tools.values():
        path.touch()
    source = tmp_path / 'unit.cpp'
    source.write_text('class Counter {};')
    output = tmp_path / 'unit.o'
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        path = Path(argv[argv.index('-o') + 1])
        path.write_bytes(b'\x7fELF\x01\x02' if argv[0] == str(tools['converter']) else b'.text\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    from decompnet.consoles.n64 import sn64
    native_checks = []
    monkeypatch.setattr(sn64, 'native_cpp', lambda cpp, native: native_checks.append(native))
    sn_compile(tools['cc1pln64.exe'], tools['cpp'], tools['assembler'], tools['converter'], tools['runner'],
               source, output, tmp_path, [], native=tmp_path / 'native-cpp')
    assert native_checks == [None]  # the native preprocessor was validated only on C
    assert '-D__cplusplus=1' in calls[0] and '-D_LANGUAGE_C_PLUS_PLUS' in calls[0]
    assert '-D_LANGUAGE_C' not in calls[0]


def test_sn64_driver_selects_frontend_from_source_extension(tmp_path, monkeypatch):
    import sys

    from decompnet.consoles.n64 import sn64

    selected = []
    monkeypatch.setattr(sn64, 'compile_source', lambda cc, *args: selected.append(cc))
    for suffix in ('c', 'cpp'):
        monkeypatch.setattr(sys, 'argv', ['sn64.py', '--cc', '/tools/cc1n64.exe', '--cxx', '/tools/cc1pln64.exe',
                                       '--cpp', '/tools/cppn64.exe', '--assembler', '/tools/asn64.exe',
                                       '--converter', '/tools/converter', '--runner', '/tools/wibo',
                                       str(tmp_path / f'unit.{suffix}'), str(tmp_path / 'unit.o'), str(tmp_path)])
        sn64.main()
    assert selected == [Path('/tools/cc1n64.exe'), Path('/tools/cc1pln64.exe')]
