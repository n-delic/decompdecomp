"""Mac host routing and opt-in byte judging with the original PS2 compiler packs."""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from decompnet import consoles, linuxvm, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.compiler import compile_source, run_in
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config

SONY = ('ee-gcc-2.96', 'ee-gcc-2.9-991111', 'ee-gcc-3.2')
SN = ('ee-gcc-2.95.2', 'ee-gcc-2.95.3')
MW = ('mwcps2-2.3.3', 'mwcps2-3.0.1')


def test_wibo_dependency_reports_missing_rosetta(monkeypatch):
    from functools import cache

    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(toolchains, '_intel_mac_runtime_ready', cache(lambda: False))
    assert 'Rosetta 2' in toolchains.runtime_error('mwcps2-3.0.1')


def test_installing_vm_compiler_requires_explicit_prepare(tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMPNET_TOOLCHAINS', str(tmp_path))
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(linuxvm, 'status', lambda refresh=False: (False, 'Choose Prepare Linux runner'))
    monkeypatch.setattr(linuxvm, 'setup', lambda **kwargs: pytest.fail('install must not start VM'))
    with pytest.raises(toolchains.ToolchainError, match='Prepare Linux runner'):
        toolchains.install('ee-gcc-2.96', log=lambda *_: None, accept_license=True)


def test_own_compiler_is_not_copied_into_vm(tmp_path, monkeypatch):
    from decompnet import kits

    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(kits, 'own_compiler', lambda _: SimpleNamespace(like='ee-gcc-2.96'))
    monkeypatch.setattr(linuxvm, 'prepare_tools', lambda _: pytest.fail('kit must stay in place'))
    with pytest.raises(toolchains.ToolchainError, match='cannot read your own compiler kit in place'):
        compiler_config(tmp_path, {'compiler': {'toolchain': 'own-ee-gcc'}})


@pytest.mark.parametrize('host', ['macos-arm64', 'macos-x86_64'])
def test_mac_ps2_compilers_select_the_runner_for_their_binary_format(monkeypatch, host):
    monkeypatch.setattr(toolchains, 'current_platform', lambda: host)
    monkeypatch.setattr(linuxvm, 'wanted', lambda: True)
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: False)
    for tid in SONY:
        tc = toolchains.get(tid)
        assert tc.supported and tc.via_vm and not tc.parts_for()
        assert all(toolchains.get(dep).via_vm for dep in tc.requires)
    for tid in SN + MW:
        tc = toolchains.get(tid)
        assert tc.supported and not tc.via_vm and tc.requires == ['wibo']
        assert toolchains.get('wibo').parts_for(host)
        assert tc.parts_for(host) == tc.parts_for('linux-x86_64')
    assert {c.id for c in consoles.get('ps2').compilers} == set(SONY + SN + MW)
    assert all(toolchains.get(tid).proprietary for tid in MW)


@pytest.mark.parametrize('tid', SONY + SN + MW)
def test_real_mac_ps2_engine_and_export_compile(tmp_path, tid):
    """No downloads in tests: opt in after installing packs in a scratch toolchain root/VM."""
    if (os.environ.get('DECOMP_PS2_MAC_TESTS') != '1'
            or not toolchains.current_platform().startswith('macos-') or not toolchains.installed(tid)):
        pytest.skip('needs opt-in and an isolated installed PS2 compiler')
    # Hand-encoded return a+b: GCC 3.2 moves the result through a0; CodeWarrior
    # adds before returning, leaving a nop in the delay slot.
    code = ('211085000800e00300000000' if tid in MW else
            '212085000800e0032d108000' if tid == 'ee-gcc-3.2' else '0800e00321108500')
    target = bytes.fromhex(code)
    (tmp_path / 'homemade.bin').write_bytes(target)
    flags = toolchains.get(tid).flags
    cfg = {'kind': 'rom', 'platform': 'ps2', 'big_endian': False,
           'rom': {'file': 'homemade.bin', 'sha1': hashlib.sha1(target).hexdigest()},
           'segments': [{'name': 'main', 'rom_start': 0, 'rom_end': len(target), 'vram': 0x100000}],
           'compiler': {'toolchain': tid, 'flags': flags}}
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config,arch,big_endian) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Homemade test', str(tmp_path), json.dumps(cfg), 'mips', 0)).lastrowid
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)', (pid, 1, '', ''))
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) '
                   'VALUES(?,?,?,?,?,?,?,?,?)', (pid, 'probe', 0x100000, 0, len(target), len(target)//4, target, '[]', 'open'))
    proj, fn = net._project('demo'), net._function('probe', 'demo')
    for source, expected in [('int probe(int a,int b){return a+b;}', True),
                             ('int probe(int a,int b){return a-b;}', False)]:
        matched, diff, log = net._judge_with(proj, fn, tid, flags, source)
        assert matched == expected and diff.bytes_match == expected, log
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'test.h').write_text('#define EXTRA 0\n')
    (tmp_path / 'source.c').write_text('#include "test.h"\nint probe(int a,int b){return a+b+EXTRA;}')
    cc = compiler_config(tmp_path, cfg)
    # Native runners see host-created directories; VM runners must recreate them.
    (tmp_path / 'build/src/unit').mkdir(parents=True)
    argv = [a.replace('{src}', 'source.c').replace('{obj}', 'build/src/unit/probe.o').replace('{include}', 'include') for a in cc.cmd]
    result = run_in(cc, tmp_path, argv, inputs=['source.c', 'include'], outputs=['build/src/unit/probe.o'])
    assert result.returncode == 0, result.stdout + result.stderr
    assert extract_function((tmp_path / 'build/src/unit/probe.o').read_bytes(), 'probe').code == target

    cpp = compile_source(cc, '', 'template<class T> T sum(T a,T b){return a+b;}\n'
                         'extern "C" int probe(int a,int b){return sum(a,b);}')
    assert cpp.ok and cpp.language == 'c++', cpp.log
    method = extract_function(cpp.obj, 'probe')
    assert method.arch == 'mips' and not method.big_endian and method.code


@pytest.mark.parametrize('tid', MW)
def test_mac_codewarrior_requires_license_acceptance_before_download(tmp_path, monkeypatch, tid):
    monkeypatch.setenv('DECOMPNET_TOOLCHAINS', str(tmp_path))
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(toolchains, 'runtime_error', lambda _: None)
    def unexpected(*args, **kwargs):
        pytest.fail('a proprietary pack must not download without acceptance')
    monkeypatch.setattr(toolchains, '_download', unexpected)
    with pytest.raises(toolchains.ToolchainError, match='accept-license'):
        toolchains.install(tid, log=lambda *_: None)
