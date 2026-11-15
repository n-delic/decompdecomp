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


def _sn64_source(source):
    """A stand-in for the pinned SN64 source: each file holds the exact text the 64-bit fixes replace."""
    for name, old, _ in toolchains._SN64_EDITS:
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text((file.read_text(encoding='latin-1') if file.is_file() else '/* upstream */\n') + old + '\n',
                        encoding='latin-1')
    (source / 'getpwd.c').write_text('/* upstream host source */')


def test_reconstructed_sn_native_build_fixes_the_64_bit_host_and_checks_itself(tmp_path, monkeypatch):
    dest = tmp_path / 'out'
    dest.mkdir()
    seen = {}
    def build(argv, **kwargs):
        source = kwargs['cwd']
        seen['argv'], seen['env'] = argv, kwargs['env']
        seen['files'] = {name: (source / name).read_text(encoding='latin-1')
                         for name in {name for name, _, _ in toolchains._SN64_EDITS} | {'getpwd.c'}}
        assert kwargs['timeout'] == 900
        for name in ['cpp', 'cc1']:
            (source / name).write_text('native')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    checked = []
    monkeypatch.setattr(toolchains, '_extract', lambda archive, source, subdir: _sn64_source(source))
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.limits, 'capped', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', build)
    monkeypatch.setattr(toolchains, '_check_sn64_gcc', lambda build, tmp: checked.append((build / 'cc1').read_text()))
    toolchains._build_sn64_gcc(tmp_path / 'source.tar.gz', dest, 'pinned/source', lambda *_: None)
    files = seen['files']
    assert '__eprintf' in files['getpwd.c']
    for name, old, new in toolchains._SN64_EDITS:
        assert new in files[name]
    assert 'bcopy ((char *) &CONST_DOUBLE_LOW' not in files['varasm.c'] + files['real.h'] + files['emit-rtl.c']
    assert 'extern void   error ();' not in files['config/mips/mips.c']
    assert 'static void decomp_cd_put' in files['rtl.h']
    argv = seen['argv']
    assert argv[0] == '/usr/bin/make' and 'cpp' in argv and 'cc1' in argv and 'CC=/usr/bin/clang' in argv
    cflags = next(a for a in argv if a.startswith('CFLAGS='))
    host = next(a for a in argv if a.startswith('HOST_CFLAGS='))
    assert '-include ' in cflags and 'decomp-arm64-varargs.h' in cflags
    assert '-include' not in host  # the generators define their own fatal()
    assert '-fno-strict-aliasing' in cflags and '-fno-strict-aliasing' in host  # clang -O2 broke real.c otherwise
    assert seen['env']['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin' and 'CFLAGS' not in seen['env']
    assert checked == ['native']
    assert {p.name: p.read_text() for p in dest.iterdir()} == {'cpp': 'native', 'cc1': 'native'}


def test_reconstructed_sn_native_build_fails_closed_when_the_source_moves(tmp_path, monkeypatch):
    def extract(archive, source, subdir):
        _sn64_source(source)
        (source / 'varasm.c').write_text('/* a newer varasm.c */')
    monkeypatch.setattr(toolchains, '_extract', extract)
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', lambda *a, **k: pytest.fail('built an unpatched GCC'))
    with pytest.raises(toolchains.ToolchainError, match='varasm.c no longer matches'):
        toolchains._build_sn64_gcc(tmp_path / 'source.tar.gz', tmp_path, 'pinned', lambda *_: None)


def test_reconstructed_sn_installs_built_before_the_fixes_are_rebuilt():
    """An install's fingerprint covers its parts, so the recipe number makes the broken builds out of date."""
    tc = toolchains.get('sn-n64-272-970404')
    for plat in ['macos-arm64', 'macos-x86_64']:
        assert [(p.get('build'), p.get('recipe')) for p in tc.parts_for(plat)] == [('sn64-gcc', 2)]


# What the Linux release prints for the check's file (under qemu-i386), and what the broken builds printed.
_SN64_GOOD = ('\tli.s\t$f0,1.50000000000000000000e0\n\tli.s\t$f0,1.00000001490116119385e-1\n'
              '\tli.d\t$f0,4.29496729600000000000e9\n\t.word\t0xc47a0000\t\t# -1000 (float)\n'
              '\t.word\t0x3f000000\t\t# 0.5 (float)\n')


@pytest.mark.parametrize('listings, broken_exit, why', [
    ([_SN64_GOOD.replace('\tli.s\t$f0,1.5', '\tl.s\t$f0,%lo(.LC0)($2)  # 1.5')] * 3, 33,
     'prints float constants wrongly'),                                    # CONST_DOUBLE_HIGH never written
    ([_SN64_GOOD.replace('li.d\t$f0,4.29', 'l.d\t$f0,.LC0  # 4.29')] * 3, 33,
     'prints float constants wrongly'),                                    # clang -O2 with strict aliasing
    ([_SN64_GOOD, _SN64_GOOD + '\t.word\t0x00000000\t\t# 2.48e-312 (float)\n', _SN64_GOOD], 33,
     'from run to run'),
    ([_SN64_GOOD] * 3, -11, "doesn't report compile errors"),             # no variadic prototypes (arm64)
])
def test_reconstructed_sn_check_refuses_the_broken_builds_seen(tmp_path, monkeypatch, listings, broken_exit, why):
    floats = iter(listings)
    def run(argv, **kwargs):
        src = Path(argv[argv.index('-o') - 1])
        if src.name == 'floats.c':
            Path(argv[argv.index('-o') + 1]).write_text(next(floats))
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=broken_exit, stdout='', stderr="`undeclared' undeclared")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    with pytest.raises(toolchains.ToolchainError, match=why):
        toolchains._check_sn64_gcc(tmp_path, tmp_path)


def test_reconstructed_sn_check_accepts_the_linux_release_output(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        src = Path(argv[argv.index('-o') - 1])
        if src.name == 'floats.c':
            Path(argv[argv.index('-o') + 1]).write_text(_SN64_GOOD)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=33, stdout='', stderr="`undeclared' undeclared")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    toolchains._check_sn64_gcc(tmp_path, tmp_path)


def test_installed_reconstructed_sn_passes_the_native_build_check(tmp_path):
    """The same check holds for the Linux release, so it runs wherever the compiler is installed."""
    if toolchains.installed('sn-n64-272-970404') is None:
        pytest.skip('requires the installed compiler')
    toolchains._check_sn64_gcc(toolchains.path('sn-n64-272-970404'), tmp_path)


PM_GCC = ['gcc-2.8.1-pm', 'gcc-2.8.1-pm-sn-as']


@pytest.mark.parametrize('tid', PM_GCC)
def test_pm_gcc_is_native_on_apple_silicon_and_unchanged_on_intel_macs(tid):
    """Under Rosetta each launch of the Intel build cost 30-40 ms and a `gcc -S` 230 ms (13 ms native)."""
    tc = toolchains.get(tid)
    arm = [p for p in tc.parts_for('macos-arm64') if 'gcc-papermario' in p['url']]
    assert [p.get('build') for p in arm] == ['gcc-papermario'] and not arm[0].get('rosetta')
    assert arm[0]['url'].endswith('/' + arm[0]['subdir'].removeprefix('gcc-papermario-'))
    intel = [p for p in tc.parts_for('macos-x86_64') if 'gcc-papermario' in p['url']]
    assert [p['file'] for p in intel] == ['pm-gcc-mac.tar.gz'] and intel[0]['rosetta']
    if tid == 'gcc-2.8.1-pm-sn-as':  # its assembler runs under wibo: nothing left for Rosetta
        assert not any(p.get('rosetta') for p in tc.parts_for('macos-arm64'))


def _pm_source(source):
    for name, lines in toolchains._GCC_PM_DECLARATIONS.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text('/* upstream */\n' + ''.join(lines) + 'int keep;\n', encoding='latin-1')
    for name, edits in toolchains._CONST_DOUBLE_EDITS.items():
        (source / name).write_text(''.join(old for old, _ in edits), encoding='latin-1')
    for name in ['c-parse.c', 'c-parse.h', 'cexp.c', 'cp/parse.c', 'cp/parse.h']:
        (source / name).write_text('/* bison output */')


def test_pm_gcc_native_build_declares_varargs_and_does_not_optimise_gcc(tmp_path, monkeypatch):
    dest = tmp_path / 'out'
    dest.mkdir()
    runs = []
    def build(argv, **kwargs):
        source = kwargs['cwd']
        runs.append((argv, kwargs['env']))
        assert kwargs['timeout'] == 900 and kwargs['env']['CC'] == '/usr/bin/clang'
        assert kwargs['env']['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'  # not a computer's newer bison
        if argv[0] == '/usr/bin/make':
            for name in ['cpp', 'cc1', 'xgcc', 'cc1plus', 'g++']:
                (source / name).write_text('native ' + name)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    checked = []
    monkeypatch.setattr(toolchains, '_extract', lambda archive, source, subdir: _pm_source(source))
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.limits, 'capped', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', build)
    def check(source, tmp):  # (the build's folder is gone once it returns)
        checked.append({name: (source / name).read_text(encoding='latin-1') for name in toolchains._GCC_PM_DECLARATIONS})
        checked.append((source / 'decomp-arm64-varargs.h', (source / 'decomp-arm64-varargs.h').read_text()))
        checked.append((source / 'varasm.c').read_text(encoding='latin-1') + (source / 'rtl.h').read_text())
        assert (source / 'cp/parse.c').stat().st_mtime > (source / 'cp/errfn.c').stat().st_mtime
    monkeypatch.setattr(toolchains, '_check_gcc_papermario', check)
    toolchains._build_gcc_papermario(tmp_path / 'src.tar.gz', dest, 'pinned', lambda *_: None)
    edited, (header, prototypes), copies = checked
    assert set(edited.values()) == {'/* upstream */\nint keep;\n'}
    assert 'CONST_DOUBLE_LOW' not in copies and 'decomp_cd_get (x, (char *) &u, sizeof u);' in copies
    assert 'extern void error (char *, ...);' in prototypes
    configure, make = runs
    assert '--target=mips-nintendo-nu64' in configure[0]
    flags = ' '.join(a for a in make[0] if a.startswith(('CFLAGS=', 'HOST_CFLAGS='))) + ' ' + configure[1]['CFLAGS']
    assert f'-include {header}' in flags and not any(f.startswith('-O') for f in flags.split())
    assert (dest / 'gcc').read_text() == 'native xgcc'
    assert {p.name for p in dest.iterdir()} == {'cpp', 'cc1', 'gcc', 'cc1plus', 'g++'}


def test_pm_gcc_native_build_fails_closed_when_the_source_moves(tmp_path, monkeypatch):
    def extract(archive, source, subdir):
        _pm_source(source)
        (source / 'cp/errfn.c').write_text('/* a newer errfn.c */')
    monkeypatch.setattr(toolchains, '_extract', extract)
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', lambda *a, **k: pytest.fail('built an unpatched GCC'))
    with pytest.raises(toolchains.ToolchainError, match='cp/errfn.c no longer matches'):
        toolchains._build_gcc_papermario(tmp_path / 'src.tar.gz', tmp_path, 'pinned', lambda *_: None)


_POOLED_1E300 = '\t.word\t0x7e37e43c\t\t# 1.0000000000000000525e+300\n\t.word\t0x8800759c\n'


@pytest.mark.parametrize('asm, broken_exit, why', [
    ('\tl.d\t$f0,.LC0\n' + _POOLED_1E300, 33, 'folds float constants wrongly'),          # clang -O2 built
    ('\tli.d\t$f0,4.29496729600000000000e9\n' + _POOLED_1E300, -11, "doesn't report compile errors"),  # no varargs
    # A 64-bit host's CONST_DOUBLE copy: 1e300's high word from the slot's unwritten half (the Intel Mac release too)
    ('\tli.d\t$f0,4.29496729600000000000e9\n\t.word\t0x00000000\n\t.word\t0x8800759c\n', 33,
     'folds float constants wrongly'),
])
def test_pm_gcc_check_refuses_the_broken_builds_seen(tmp_path, monkeypatch, asm, broken_exit, why):
    def run(argv, **kwargs):
        src = Path(argv[argv.index('-o') - 1])
        if src.name == 'floats.c':
            Path(argv[argv.index('-o') + 1]).write_text(asm)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=broken_exit, stdout='', stderr="`undeclared' undeclared")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    with pytest.raises(toolchains.ToolchainError, match=why):
        toolchains._check_gcc_papermario(tmp_path, tmp_path)


def test_pm_gcc_check_accepts_the_linux_release_output(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        if Path(argv[argv.index('-o') - 1]).name == 'floats.c':
            Path(argv[argv.index('-o') + 1]).write_text('\tli.d\t$f0,4.29496729600000000000e9\n' + _POOLED_1E300)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=33, stdout='', stderr="`undeclared' undeclared")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    toolchains._check_gcc_papermario(tmp_path, tmp_path)


@pytest.mark.parametrize('tid', [*PM_GCC, 'gcc-2.7.2-kmc'])
def test_native_gcc_installs_built_before_the_pooled_constant_fix_are_rebuilt(tid):
    """An install's fingerprint covers its parts, so the recipe number makes the earlier native builds out of date."""
    arm = [p for p in toolchains.get(tid).parts_for('macos-arm64') if p.get('build', '').startswith('gcc-')]
    assert [p.get('recipe') for p in arm] == [2]


@pytest.mark.parametrize('tid', PM_GCC)
def test_installed_pm_gcc_passes_the_native_build_check(tmp_path, tid):
    if toolchains.installed(tid) is None:
        pytest.skip('requires the installed compiler')
    toolchains._check_gcc_papermario(toolchains.path(tid), tmp_path)


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


KMC = 'gcc-2.7.2-kmc'


def test_kmc_gcc_is_native_on_apple_silicon_and_unchanged_on_intel_macs(monkeypatch):
    """Under Rosetta every KMC compile cost about 150-190 ms of CPU, against 18-60 ms native."""
    tc = toolchains.get(KMC)
    arm = tc.parts_for('macos-arm64')
    assert [p.get('build') for p in arm] == ['gcc-2.7.2-kmc', 'binutils-2.6-kmc']
    assert not any(p.get('rosetta') for p in arm)
    for part in arm:  # the commits upstream's v0.1 and v0.3 Mac releases were built from
        assert part['url'].endswith('/' + part['subdir'].split('-')[-1]) and len(part['sha256']) == 64
    intel = tc.parts_for('macos-x86_64')
    assert [Path(p['url']).name for p in intel] == ['gcc-2.7.2-mac.tar.gz', 'binutils-2.6-mac.tar.gz']
    assert all(p['rosetta'] for p in intel)
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-x86_64')
    assert toolchains.fingerprint(tc) == '0a47cc5b7413a94e'  # Intel Macs' installs don't reinstall


def _kmc_source(source):
    for name, lines in toolchains._GCC_KMC_DECLARATIONS.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text('/* upstream */\n' + ''.join(lines), encoding='latin-1')
    for name, edits in toolchains._GCC_KMC_EDITS.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        with open(source / name, 'a', encoding='latin-1') as f:
            f.write(''.join(old for old, _ in edits))
    for name in ['c-parse.c', 'c-parse.h', 'cexp.c', 'cp/parse.c', 'cp/parse.h', 'bi-parser.c', 'bi-parser.h']:
        (source / name).write_text('/* bison output */')


def _mock_build(monkeypatch, extract, outputs):
    runs = []
    def build(argv, **kwargs):
        runs.append((argv, kwargs['env']))
        assert kwargs['timeout'] == 900 and kwargs['env']['CC'] == '/usr/bin/clang'
        assert kwargs['env']['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'  # not a computer's newer bison
        if argv[0] == '/usr/bin/make':
            for name in outputs:
                (kwargs['cwd'] / name).parent.mkdir(parents=True, exist_ok=True)
                (kwargs['cwd'] / name).write_text('native ' + name)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(toolchains, '_extract', lambda archive, source, subdir: extract(source))
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.limits, 'capped', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', build)
    return runs


def test_kmc_gcc_native_build_declares_varargs_and_does_not_optimise_gcc(tmp_path, monkeypatch):
    dest = tmp_path / 'out'
    dest.mkdir()
    runs = _mock_build(monkeypatch, _kmc_source, ['xgcc', 'cc1', 'cc1plus', 'cpp', 'cccp', 'g++'])
    checked = []
    def check(source, tmp, name):  # (the build's folder is gone once it returns)
        files = dict.fromkeys([*toolchains._GCC_KMC_DECLARATIONS, *toolchains._GCC_KMC_EDITS])
        checked.append({f: (source / f).read_text(encoding='latin-1') for f in files})
        checked.append((source / 'decomp-arm64-varargs.h').read_text())
        assert (source / 'cp/parse.c').stat().st_mtime > (source / 'cp/errfn.c').stat().st_mtime
    monkeypatch.setattr(toolchains, '_check_native_gcc', check)
    toolchains._build_gcc_kmc(tmp_path / 'src.tar.gz', dest, 'pinned', lambda *_: None)
    edited, prototypes = checked
    assert 'extern void error (char *, ...);' in prototypes
    assert 'extern void   error ();' not in edited['config/mips/mips.c']
    assert '(*(void (*) (char *, ...)) errfn) (fmt, ARGSLIST);' in edited['cp/errfn.c']
    assert 'errfn == compiler_error' in edited['cp/errfn.c']  # (compiler_error isn't variadic)
    assert 'a transparent union", (char *) 0);' in edited['c-decl.c']
    assert 'CONST_DOUBLE_LOW' not in edited['varasm.c'] and 'static void decomp_cd_get' in edited['rtl.h']
    configure, make, gxx = runs
    assert '--target=mips-mips-gnu' in configure[0] and '--host=i386-apple-darwin' in configure[0]
    flags = ' '.join(a for a in make[0] + gxx[0] if a.startswith(('CFLAGS=', 'HOST_CFLAGS='))) + configure[1]['CFLAGS']
    assert not any(f.startswith('-O') for f in flags.split()) and '-DDARWIN' in flags
    cflags, host = (next(a for a in make[0] if a.startswith(k)) for k in ('CFLAGS=', 'HOST_CFLAGS='))
    assert '-include ' in cflags and '-include' not in host  # the generators define their own static fatal()
    assert gxx[0][1] == 'g++' and not any('-include' in a for a in gxx[0])
    assert (dest / 'gcc').read_text() == 'native xgcc'
    assert {p.name for p in dest.iterdir()} == {'gcc', 'cc1', 'cc1plus', 'cpp', 'cccp', 'g++'}


def test_kmc_gcc_native_build_fails_closed_when_the_source_moves(tmp_path, monkeypatch):
    def extract(source):
        _kmc_source(source)
        (source / 'cp/typeck2.c').write_text('/* a newer typeck2.c */')
    monkeypatch.setattr(toolchains, '_extract', lambda archive, source, subdir: extract(source))
    monkeypatch.setattr(toolchains.shutil, 'which', lambda x: x)
    monkeypatch.setattr(toolchains.subprocess, 'run', lambda *a, **k: pytest.fail('built an unpatched GCC'))
    with pytest.raises(toolchains.ToolchainError, match='cp/typeck2.c no longer matches'):
        toolchains._build_gcc_kmc(tmp_path / 'src.tar.gz', tmp_path, 'pinned', lambda *_: None)


def test_kmc_binutils_native_build_runs_configure_probes_with_the_quiet_flags(tmp_path, monkeypatch):
    dest = tmp_path / 'out'
    dest.mkdir()
    def extract(source):
        for name in ['binutils/arparse.c', 'binutils/arparse.h', 'binutils/arlex.c']:
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_text('/* generated */')
    runs = _mock_build(monkeypatch, extract, ['gas/as.new', 'binutils/ar', 'binutils/strip.new',
                                              'binutils/objcopy', 'binutils/c++filt'])
    checked = []
    monkeypatch.setattr(toolchains, '_check_kmc_as', lambda assembler, tmp: checked.append(assembler.read_text()))
    toolchains._build_binutils_kmc(tmp_path / 'src.tar.gz', dest, 'pinned', lambda *_: None)
    configure, *makes = runs
    assert '--target=mips-kmc-elf' in configure[0]
    # A newer clang's errors made configure's probes fail: gas then defined `inline` away and broke the link.
    assert '-Wno-implicit-int' in configure[1]['CFLAGS'] and '-Wno-implicit-function-declaration' in configure[1]['CFLAGS']
    assert [m[0][2] for m in makes] == ['bfd', 'opcodes', 'libiberty', 'gas', 'binutils']
    assert not any(f.startswith('-O') for m in makes for a in m[0] if a.startswith('CFLAGS=') for f in a.split())
    assert checked == ['native gas/as.new']
    assert {p.name for p in dest.iterdir()} == {'as', 'ar', 'strip', 'objcopy', 'c++filt'}


@pytest.mark.parametrize('returncode, obj, bad_exit, why', [
    (0, b'\x7fELF' + bytes(60) + bytes.fromhex('0085102d03e00008'), -11, "doesn't report errors"),
    (0, b'\x7fELF' + bytes(64), 1, "doesn't assemble MIPS correctly"),
    (-11, b'', 1, "doesn't assemble MIPS correctly"),
])
def test_kmc_as_check_refuses_broken_assemblers(tmp_path, monkeypatch, returncode, obj, bad_exit, why):
    def run(argv, **kwargs):
        if Path(argv[-1]).name == 'good.s':
            Path(argv[argv.index('-o') + 1]).write_bytes(obj)
            return SimpleNamespace(returncode=returncode, stdout='', stderr='')
        return SimpleNamespace(returncode=bad_exit, stdout='', stderr="Error: unrecognized opcode `notanopcode'")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    with pytest.raises(toolchains.ToolchainError, match=why):
        toolchains._check_kmc_as(tmp_path / 'as', tmp_path)


def _installed_kmc():
    if toolchains.installed(KMC) is None:
        pytest.skip('requires the installed compiler')
    return toolchains.path(KMC)


def test_installed_kmc_gcc_passes_the_native_build_checks(tmp_path):
    folder = _installed_kmc()
    toolchains._check_native_gcc(folder, tmp_path, 'GCC 2.7.2 (KMC)')
    toolchains._check_kmc_as(folder / 'as', tmp_path)


def test_installed_kmc_gcc_reports_diagnostics_missing_their_argument_like_the_intel_build(tmp_path):
    """The arm64 build crashed (exit 138) on a transparent union, where the Intel build warned."""
    folder = _installed_kmc()
    src = tmp_path / 'union.c'
    src.write_text('union u { char a; int x[2]; } __attribute__ ((transparent_union));\nint keep;\n')
    import subprocess
    run = subprocess.run([str(folder / 'cc1'), '-quiet', '-O2', str(src), '-o', str(tmp_path / 'union.s')],
                         capture_output=True, text=True, errors='replace', timeout=30)
    assert run.returncode == 0 and "cannot make `(null)' a transparent union" in run.stderr


def test_installed_kmc_gcc_builds_matching_objects_through_the_profile(tmp_path):
    _installed_kmc()
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': KMC, 'flags': ['-G0', '-mips3', '-mgp32', '-mfp32',
                                                                              '-O2', '-g3']}})
    result = compile_source(cfg, '', 'int probe(int a,int b){return a+b;}\n'
                                     'double widen(unsigned int x){return x;}\n')
    assert result.ok, result.log
    fn = extract_function(result.obj, 'probe')
    assert fn.code == bytes.fromhex('03e0000800851021')
    verdict = link(result.obj, 'probe', RomTarget('probe', 0x80001000, fn.code, []), SymbolTable(), lambda *_: None)
    assert verdict.exact, verdict.problems
