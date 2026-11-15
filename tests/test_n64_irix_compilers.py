"""Additional N64 comparison profiles preserve host and byte-order boundaries."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import consoles, linuxvm, toolchains
from decompnet.app.engine.compilers import CompilerMixin
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import N64
from decompnet.consoles.n64.irix import compile_irix, convert_ecoff
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config
from decompnet.rom.match import RomTarget, link
from decompnet.rom.symbols import Symbol, SymbolTable

SGI = ['ido-4.1', 'ido-5.2', 'ido-5.3-cxx', 'ido-6.0', 'mipspro-7.4.4']
GNU = ['gcc-egcs-1.1.2-4', 'gcc-4.4.0-n64']


@pytest.mark.parametrize('tid', SGI + GNU)
def test_comparison_profiles_have_pins_host_limits_and_matching_guides(tid, monkeypatch):
    tc = toolchains.get(tid)
    assert tid in [c.id for c in N64.compilers]
    assert tc.parts_for('linux-x86_64') and not tc.parts_for('macos-arm64')
    assert all(len(p['sha256']) == 64 for p in tc.parts)
    assert tc.family == ('ido' if tid in SGI else 'gcc')
    assert N64.m2c_target(tid) == ('mips-ido' if tid in SGI else 'mips-gcc')
    assert N64.up_front_guide(tid)
    # Host simulation must isolate WSL too: on Windows the real wanted() is otherwise true.
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: False)
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(linuxvm, "wanted", lambda: True)
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: False)
    assert tc.supported and tc.via_vm
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'windows-x86_64')
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: True)
    assert tc.via_wsl


@pytest.mark.parametrize('tid', SGI)
def test_sgi_packs_require_explicit_license_acceptance(tid, tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMPNET_TOOLCHAINS', str(tmp_path))
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'linux-x86_64')
    with pytest.raises(toolchains.ToolchainError, match='accept-license'):
        toolchains.install(tid, log=lambda *_: None)
    assert not (tmp_path / tid).exists()


def test_experimental_profiles_are_not_added_to_routine_comparisons(monkeypatch):
    cfg = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    net = SimpleNamespace(cfg=lambda _: cfg, debug_flag_variants=lambda _, flags: [flags],
                          flag_variants=lambda _, flags: [flags])
    monkeypatch.setattr(toolchains, 'installed', lambda _: True)
    monkeypatch.setattr(toolchains, 'runtime_error', lambda _: None)
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'linux-x86_64')
    ids = {tc for tc, _ in CompilerMixin._comparison_candidates(net, None)}
    assert 'gcc-egcs-1.1.2-4' in ids
    assert not ids.intersection(SGI + ['gcc-4.4.0-n64'])
    # Explicit project selection still participates; this is not an unavailable compiler.
    cfg['compiler']['toolchain'] = 'ido-5.2'
    assert 'ido-5.2' in {tc for tc, _ in CompilerMixin._comparison_candidates(net, None)}
    assert consoles.default_flags(cfg, 'ido-5.2') == toolchains.get('ido-5.2').flags


@pytest.mark.parametrize('profile,program,suffix', [('ido-4.1','usr/bin/cc','.c'),
    ('ido-5.3-cxx','usr/lib/driver','.c'), ('ido-5.3-cxx','usr/lib/CC','.cpp'), ('ido-6.0','usr/lib/driver','.c')])
def test_irix_driver_uses_pinned_root_and_checks_elf_endianness(tmp_path, monkeypatch, profile, program, suffix):
    from decompnet.consoles.n64 import irix

    emulator = 'usr/bin/qemu-irix-4.0' if profile == 'ido-4.1' else 'usr/bin/qemu-irix'
    for name in [program, emulator]:
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True, parents=True)
        p.touch()
    obj = tmp_path / 'probe.o'
    source = Path('probe' + suffix)
    (tmp_path / source).write_text('original source bytes')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['check'] and kwargs['timeout'] == 60
        Path(argv[argv.index('-o')+1]).write_bytes(b'\x7fELF\x01\x02')
    monkeypatch.setattr(irix.subprocess, 'run', run)
    monkeypatch.setattr(irix, 'convert_ecoff', lambda _, src, dest: dest.write_bytes(src.read_bytes()))
    monkeypatch.chdir(tmp_path)
    compile_irix(tmp_path, profile, source, obj, Path('include'), ['-O2'])
    assert calls[0][:5] == [str(tmp_path / emulator), '-silent', '-L', str(tmp_path), str(tmp_path / program)]
    assert ('-EB' in calls[0]) == (program != 'usr/lib/CC')
    if suffix == '.cpp':
        assert (tmp_path / 'probe.C').read_bytes() == (tmp_path / source).read_bytes()
        assert 'probe.C' in calls[0]
    assert '-I' + str(tmp_path / 'include') in calls[0]
    def wrong(argv, **kwargs):
        Path(argv[argv.index('-o')+1]).write_bytes(b'\x7fELF\x01\x01')
    monkeypatch.setattr(irix.subprocess, 'run', wrong)
    with pytest.raises(ValueError, match='big-endian ELF32'):
        compile_irix(tmp_path, profile, source, obj, Path('include'), [])


def test_early_ecoff_adapter_serializes_elf_metadata_big_endian(tmp_path):
    converter = tmp_path / 'converter.py'
    converter.write_text('''import struct
class Endian:
    BIG = 2
class EcoffFile:
    code_endian = 2
    @classmethod
    def from_path(cls, path): return cls()
class ElfBuilder:
    ELFDATA2LSB = 1
def save_as_elf(ecoff, path):
    if ecoff.code_endian == Endian.BIG:
        print("Only MIPS-EL files are supported")
        return
    path.write_bytes(bytes([ElfBuilder.ELFDATA2LSB]) + struct.pack('<II', 0x12345678, 0x01020304))
''')
    output = tmp_path / 'probe.o'
    convert_ecoff(converter, tmp_path / 'probe.ecoff', output)
    assert output.read_bytes() == bytes.fromhex('021234567801020304')
    converter.write_text(converter.read_text().replace('code_endian = 2', 'code_endian = 1 # little endian'))
    with pytest.raises(ValueError, match='little-endian code'):
        convert_ecoff(converter, tmp_path / 'probe.ecoff', output)


@pytest.mark.parametrize('tid', SGI + GNU)
def test_installed_comparison_compilers_reach_the_normal_link_judge(tmp_path, tid):
    tc = toolchains.get(tid)
    if not tc.supported or any(not toolchains.installed(t) for t in [tid, *tc.requires]):
        pytest.skip('needs supported host and locally installed packs; never installs proprietary tools in tests')
    if tc.via_vm and not toolchains.linuxvm.status()[0]:
        pytest.skip('needs a prepared Linux compiler runner')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid, 'sandbox': 'none'}})
    for source in ['int probe(int a,int b){return a+b;}',
                   'extern int target(int); extern int value; int probe(int x){return target(x)+value;}',
                   'float probe(float x){return x*1.25f;}',
                   'int probe(int x){if(x<0)return -x;return x+3;}']:
        result = compile_source(cfg, '', source)
        assert result.ok, result.log
        fn = extract_function(result.obj, 'probe')
        assert fn.big_endian and result.obj[:6] == b'\x7fELF\x01\x02'
        if source.startswith('int probe(int a'):
            # Independent hand-encoded leaf function; compiler register choice may differ.
            assert fn.code in [bytes.fromhex(h) for h in ['03e0000800851021','03e0000800a41021','03e0000800851020']]
            verdict = link(result.obj, 'probe', RomTarget('probe', 0x80001000, fn.code, []), SymbolTable(), lambda *_: None)
            assert verdict.exact, verdict.problems
        if 'target(' in source:
            assert {'target', 'value'} <= {r.symbol for r in fn.relocs}
            # Place relocations independently, checking the high-half carry for a negative low half.
            linked = bytearray(fn.code)
            references = []
            for r in fn.relocs:
                address = {'target': 0x80010000, 'value': 0x80028000}[r.symbol]
                word = int.from_bytes(linked[r.offset:r.offset+4], 'big')
                if r.type == 4:
                    word = (word & 0xfc000000) | ((address >> 2) & 0x03ffffff)
                elif r.type in (5, 6):
                    word = (word & 0xffff0000) | (((address + 0x8000) >> 16 if r.type == 5 else address) & 0xffff)
                else:
                    pytest.fail(f'unexpected relocation {r}')
                linked[r.offset:r.offset+4] = word.to_bytes(4, 'big')
                references.append((r.offset, r.type, address))
            symbols = SymbolTable([Symbol('target', 0x80010000, kind='func'), Symbol('value', 0x80028000)])
            verdict = link(result.obj, 'probe', RomTarget('probe', 0x80001000, bytes(linked), references), symbols, lambda *_: None)
            assert verdict.exact, verdict.problems


def test_installed_sgi_cxx_frontend_accepts_a_class(tmp_path):
    tid = 'ido-5.3-cxx'
    if not toolchains.get(tid).supported or not toolchains.installed(tid):
        pytest.skip('needs accepted SGI C++ pack on Linux/WSL')
    if toolchains.get(tid).via_vm and not toolchains.linuxvm.status()[0]:
        pytest.skip('needs a prepared Linux compiler runner')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': tid, 'sandbox': 'none'}})
    assert cfg.cplusplus
    result = compile_source(cfg, '', 'class Value { public: int x; }; extern "C" int probe(Value *v) { return v->x; }')
    assert result.ok, result.log
    assert extract_function(result.obj, 'probe').big_endian
