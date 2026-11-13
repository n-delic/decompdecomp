"""Local VM compiler staging never reaches remote Docker contexts or host game folders."""
import base64
import hashlib
import io
import json
import shlex
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from decompnet import hostenv, linuxvm, linuxvm_worker, toolchains
from decompnet.compiler import CompilerConfig, compile_source, run_in
from decompnet.project import compiler_config


@pytest.fixture
def vm(monkeypatch, tmp_path):
    monkeypatch.setattr(linuxvm, 'wanted', lambda: True)
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: False)
    monkeypatch.setattr(linuxvm, 'executable', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(linuxvm, '_status', None)
    monkeypatch.setattr(linuxvm, '_worked', False)
    monkeypatch.setattr(linuxvm, '_prepared', set())
    monkeypatch.setenv('DECOMP_LINUX_VM_PROFILE', 'test-compilers')
    monkeypatch.setenv('DECOMPNET_TOOLCHAINS', str(tmp_path / 'toolchains'))
    return tmp_path


def test_only_opted_in_linux_packs_use_vm_and_native_profiles_keep_their_paths(vm, monkeypatch):
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    assert toolchains.get('gcc-4.4.0-n64').via_vm
    assert not toolchains.get('gcc-2.7.2-kmc').via_vm
    assert toolchains.path('gcc-4.4.0-n64') == vm / 'toolchains/linux-vm/gcc-4.4.0-n64'
    assert toolchains.path('gcc-2.7.2-kmc') == vm / 'toolchains/gcc-2.7.2-kmc'
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'linux-x86_64')
    assert not toolchains.get('gcc-4.4.0-n64').via_vm
    assert toolchains.path('gcc-4.4.0-n64') == vm / 'toolchains/gcc-4.4.0-n64'


def test_readiness_never_starts_vm_and_ignores_remote_docker_environment(vm, monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote.example:2375')
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote-cloud')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert 'DOCKER_HOST' not in kwargs['env'] and 'DOCKER_CONTEXT' not in kwargs['env']
        assert argv[:3] == ['/usr/bin/docker', '--host', 'unix://' + str(Path.home() / '.colima/test-compilers/docker.sock')]
        assert argv[3:5] == ['image', 'inspect']
        return subprocess.CompletedProcess(argv, 1, b'', b'not running')
    monkeypatch.setattr(linuxvm.subprocess, 'run', run)
    assert not linuxvm.status()[0]
    assert not linuxvm.status()[0] and len(calls) == 1


def test_setup_downloads_only_the_digest_pinned_image_and_keeps_global_context(vm, monkeypatch):
    checks = iter([(False, 'not ready'), (True, '')])
    monkeypatch.setattr(linuxvm, 'status', lambda **_: next(checks))
    calls = []
    def run(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b'', b'')
    monkeypatch.setattr(linuxvm, '_plain', run)
    assert linuxvm.setup(log=lambda *_: None)['ready']
    assert '--activate=false' in calls[0] and 'test-compilers' in calls[0]
    assert calls[1][-1] == linuxvm.IMAGE and '@sha256:' in linuxvm.IMAGE
    assert all('tcp://' not in arg for args in calls for arg in args)


def test_missing_host_runtime_has_actionable_setup_steps(vm, monkeypatch):
    monkeypatch.setattr(linuxvm, 'executable', lambda _: None)
    assert 'brew install colima docker' in linuxvm.status()[1]
    with pytest.raises(linuxvm.VMError, match='brew install colima docker'):
        linuxvm.setup()


def test_proprietary_vm_install_stops_before_any_runtime_setup(vm, monkeypatch):
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(linuxvm, 'setup', lambda **_: pytest.fail('VM started before license acceptance'))
    with pytest.raises(toolchains.ToolchainError, match='accept-license'):
        toolchains.install('ido-5.2', log=lambda *_: None)


def test_guest_tools_are_atomic_shared_and_invalidated_by_manifest_or_driver(vm, monkeypatch):
    compiler, driver = vm / 'compiler', vm / 'driver'
    compiler.mkdir()
    driver.mkdir()
    (compiler / toolchains.MANIFEST).write_text('{"version":1}')
    (compiler / 'cc').write_bytes(b'compiler')
    (driver / 'run.py').write_text('version = 1')
    monkeypatch.setattr(linuxvm, 'status', lambda: (True, ''))
    calls = []
    def invoke(argv, payload, timeout, name):
        calls.append(argv)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
            assert {'0/cc', '1/run.py'} <= set(archive.getnames())
        assert '--network' in argv and 'none' in argv and '--read-only' in argv
        return subprocess.CompletedProcess(argv, 0, b'', b'')
    monkeypatch.setattr(linuxvm, '_invoke', invoke)
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda _: linuxvm.prepare_tools((compiler, driver)), range(8)))
    assert len(calls) == 1 and all(p == paths[0] for p in paths)
    (driver / 'run.py').write_text('version = 2')
    second = linuxvm.prepare_tools((compiler, driver))
    assert second != paths[0] and len(calls) == 2
    (compiler / toolchains.MANIFEST).write_text('{"version":2}')
    assert linuxvm.prepare_tools((compiler, driver)) != second and len(calls) == 3


def test_failed_guest_cache_is_not_marked_prepared(vm, monkeypatch):
    (vm / toolchains.MANIFEST).write_text('{}')
    monkeypatch.setattr(linuxvm, 'status', lambda: (True, ''))
    monkeypatch.setattr(linuxvm, '_invoke', lambda *a: subprocess.CompletedProcess([], 1, b'', b'no space'))
    for _ in range(2):
        with pytest.raises(linuxvm.VMError, match='no space'):
            linuxvm.prepare_tools((vm,))
    assert not linuxvm._prepared


def test_compile_and_export_use_the_same_vm_runner(vm, monkeypatch):
    cfg = CompilerConfig(cmd=['/tools/cache/cc', '-I{include}', '{src}', '-o', '{obj}'], linux_vm=True, sandbox='none')
    calls = []
    def run(argv, cwd, timeout, **kwargs):
        calls.append((argv, kwargs))
        assert '/work/func.c' in argv or 'source.c' in argv
        assert (cwd / 'include/context.h').exists() or (cwd / 'source.c').exists()
        out = kwargs['outputs'][0]
        (cwd / out).write_bytes(b'object')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(linuxvm, 'run', run)
    assert compile_source(cfg, '', 'int probe(void){return 1;}').obj == b'object'
    (vm / 'source.c').write_text('source')
    assert run_in(cfg, vm, ['cc', 'source.c'], inputs=['source.c'], outputs=['source.o']).returncode == 0
    assert calls[0][1]['outputs'] == ['func.o'] and calls[0][1]['stage'] == '/work'
    assert calls[1][1]['outputs'] == ['source.o']


def test_vm_config_uses_guest_python_and_dependency_paths(vm, monkeypatch):
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(toolchains, 'runtime_dirs', lambda _: (vm / 'compiler', vm / 'dependency'))
    monkeypatch.setattr(linuxvm, 'prepare_tools', lambda dirs: tuple(f'/tools/cache/{i}' for i in range(len(dirs))))
    cfg = compiler_config(vm, {'compiler': {'toolchain': 'ido-5.2'}})
    assert cfg.linux_vm and not cfg.wsl and cfg.cmd[:3] == ['/usr/bin/env', 'LD_LIBRARY_PATH=/tools/cache/1', 'python3']
    assert '/tools/cache/2/irix.py' in cfg.cmd and '/tools/cache/0' in cfg.cmd
    cfg = compiler_config(vm, {'compiler': {'toolchain': 'gcc-4.4.0-n64'}})
    assert 'LD_LIBRARY_PATH=/tools/cache/1' in cfg.cmd


def test_vm_transfers_headers_and_only_requested_outputs(vm, monkeypatch):
    (vm / 'header ü.h').write_text('#define VALUE 7')
    def invoke(argv, payload, timeout, name, check_cancel):
        data = json.loads(payload)
        assert base64.b64decode(data['files']['header ü.h']) == b'#define VALUE 7'
        assert '--user' in argv and '65534:65534' in argv
        mount = argv[argv.index('--mount') + 1]
        assert mount.endswith(',readonly') and 'type=volume' in mount
        assert not any('type=bind' in a for a in argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({'returncode': 0, 'stdout': '', 'stderr': '',
            'files': {'out.o': base64.b64encode(b'object').decode()}}).encode(), b'')
    monkeypatch.setattr(linuxvm, '_invoke', invoke)
    assert linuxvm.run(['cc'], vm, 15, outputs=['out.o']).returncode == 0
    assert (vm / 'out.o').read_bytes() == b'object'


@pytest.mark.parametrize('bad', ['../outside.o', '/tmp/outside.o'])
def test_vm_refuses_output_path_escape(vm, monkeypatch, bad):
    monkeypatch.setattr(linuxvm, '_invoke', lambda *a: subprocess.CompletedProcess([], 0,
        json.dumps({'files': {bad: 'eA=='}}).encode(), b''))
    with pytest.raises(ValueError, match='escapes'):
        linuxvm.run(['cc'], vm, 1)


def test_vm_refuses_symlinked_input(vm):
    outside = vm.parent / 'secret.txt'
    outside.write_text('never transfer')
    try:
        (vm / 'link').symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(linuxvm.VMError, match='symbolic'):
        linuxvm.run(['cc'], vm, 1)


def test_large_input_is_read_from_a_file_while_cancellation_is_polled(vm, monkeypatch):
    # Repeated communicate(input=...) timeouts can stop writing a large pipe on macOS.
    # A file descriptor keeps the complete payload available without blocking cancellation.
    original = linuxvm.subprocess.Popen
    monkeypatch.setattr(linuxvm.subprocess, 'Popen', original)
    payload = b'x' * (2 << 20)
    result = linuxvm._invoke([sys.executable, '-c', 'import sys,time; time.sleep(.2); print(len(sys.stdin.buffer.read()))'],
                            payload, 5, 'unused-test-container')
    assert result.returncode == 0 and result.stdout.strip() == str(len(payload)).encode()


def test_cancellation_removes_the_container_and_process_tree(vm, monkeypatch):
    calls = []
    class Cancelled(Exception):
        pass
    n = 0
    def check():
        nonlocal n
        n += 1
        if n == 2:
            raise Cancelled()
    proc = object()
    monkeypatch.setattr(linuxvm.subprocess, 'Popen', lambda *a, **kw: proc)
    monkeypatch.setattr(linuxvm, '_plain', lambda argv, timeout: calls.append(argv))
    monkeypatch.setattr(hostenv, 'stop_process_tree', lambda p: calls.append(p))
    with pytest.raises(Cancelled):
        linuxvm._invoke(['docker'], b'data', 1, 'decomp-compile-only-this-job', check)
    assert calls[0][-3:] == ['rm', '--force', 'decomp-compile-only-this-job'] and calls[1] is proc


def test_worker_limits_paths_before_reading_input(vm):
    with pytest.raises(ValueError, match='escapes'):
        linuxvm_worker.compile_job({'files': {'../leak': 'eA=='}}, root=vm)


@pytest.mark.parametrize('tid,code', [('gcc-egcs-1.1.2-4','03e0000800851021'), ('gcc-4.4.0-n64','03e0000800a41021')])
def test_real_mac_vm_engine_comparison_and_export_compile(tmp_path, tid, code):
    import os

    from decompnet.app.engine import RomNetwork
    from decompnet.elfobj import extract_function

    if os.environ.get('DECOMP_LINUX_VM_TESTS') != '1' or not toolchains.get(tid).via_vm or not toolchains.installed(tid):
        pytest.skip('explicit opt-in with an isolated VM and installed free compiler packs')
    target = bytes.fromhex(code)
    (tmp_path / 'homemade.bin').write_bytes(target)
    flags = toolchains.get(tid).flags
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'rom': {'file': 'homemade.bin', 'sha1': hashlib.sha1(target).hexdigest()},
           'segments': [{'name': 'main', 'rom_start': 0, 'rom_end': 8, 'vram': 0x80001000}],
           'compiler': {'toolchain': tid, 'flags': flags}}
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config,arch,big_endian) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Homemade test', str(tmp_path), json.dumps(cfg), 'mips', 1)).lastrowid
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)', (pid, 1, '', ''))
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) '
                   'VALUES(?,?,?,?,?,?,?,?,?)', (pid, 'probe', 0x80001000, 0, 8, 2, target, '[]', 'open'))
    source = 'int probe(int a,int b){return a+b;}'
    proj, fn = net._project('demo'), net._function('probe', 'demo')
    matched, diff, log = net._judge_with(proj, fn, tid, flags, source)
    assert matched and diff.bytes_match, log
    # The exported project's build helper uses relative paths and selected input/output files.
    inc = tmp_path / 'include ü'
    inc.mkdir()
    (inc / 'test.h').write_text('#define EXTRA 0\n')
    (tmp_path / 'source.c').write_text('#include "test.h"\nint probe(int a,int b){return a+b+EXTRA;}')
    compile_cfg = compiler_config(tmp_path, cfg)
    argv = [a.replace('{src}', 'source.c').replace('{obj}', 'out.o').replace('{include}', 'include ü') for a in compile_cfg.cmd]
    result = run_in(compile_cfg, tmp_path, argv, inputs=['source.c', 'include ü'], outputs=['out.o'])
    assert result.returncode == 0, result.stdout + result.stderr
    assert extract_function((tmp_path / 'out.o').read_bytes(), 'probe').code == target


def test_real_vm_timeout_and_cancellation_remove_jobs(tmp_path, monkeypatch):
    import os
    import time

    if os.environ.get('DECOMP_LINUX_VM_TESTS') != '1' or not linuxvm.wanted() or not linuxvm.status()[0]:
        pytest.skip('explicit opt-in with an isolated ready Linux VM')
    names = []
    original = linuxvm._container
    def container(name, **kwargs):
        names.append(name)
        return original(name, **kwargs)
    monkeypatch.setattr(linuxvm, '_container', container)
    with pytest.raises(subprocess.TimeoutExpired):
        linuxvm.run(['/bin/sh', '-c', 'sleep 120'], tmp_path, 0.2, outputs=[])
    class Cancelled(Exception):
        pass
    started = time.monotonic()
    def check():
        if time.monotonic() - started > 2:
            raise Cancelled()
    with pytest.raises(Cancelled):
        linuxvm.run(['/bin/sh', '-c', 'sleep 120'], tmp_path, 60, outputs=[], check_cancel=check)
    for name in names:
        assert linuxvm._plain([*linuxvm._docker(), 'container', 'inspect', name]).returncode != 0


def test_vm_installs_are_separate_and_keep_dependency_routing(vm, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(linuxvm, 'setup', lambda **_: {'ready': True})
    monkeypatch.setattr(linuxvm, 'status', lambda: (True, ''))
    original = toolchains.registry()
    profiles = {}
    for tid in ['gcc-4.4.0-n64', 'runtime-gcc-math']:
        source = vm / tid
        source.mkdir()
        (source / 'example').write_bytes(b'original pinned file')
        archive = vm / (tid + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(source / 'example', arcname='example')
        profiles[tid] = replace(original[tid], parts=[{'url': archive.as_uri(), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                                     'platforms': ['linux-x86_64']}])
    monkeypatch.setattr(toolchains, 'registry', lambda: profiles)
    original_registered = toolchains._registered
    monkeypatch.setattr(toolchains, '_registered', lambda tid: toolchains.registry().get(tid) or original_registered(tid))
    native = toolchains.path('gcc-4.4.0-n64', vm=False)
    native.mkdir(parents=True)
    (native / 'preserved').write_text('native install')
    installed = toolchains.install('gcc-4.4.0-n64', log=lambda *_: None)
    assert installed == vm / 'toolchains/linux-vm/gcc-4.4.0-n64'
    assert (native / 'preserved').read_text() == 'native install'
    metadata = toolchains.installed('runtime-gcc-math')
    assert metadata['archive_parts'][0]['sha256'] == profiles['runtime-gcc-math'].parts[0]['sha256']
    assert not (vm / 'toolchains/linux-vm/runtime-gcc-math/example').exists()
    assert (vm / 'toolchains/linux-vm/runtime-gcc-math' / metadata['archive_parts'][0]['archive']).is_file()
    assert toolchains.installed('gcc-4.4.0-n64')['linux_vm'] is True
    assert toolchains.installed('gcc-4.4.0-n64')['wsl'] is False


def test_guest_cache_wraps_compiler_but_never_the_runtime_library(vm, monkeypatch):
    import struct
    from types import SimpleNamespace

    # Homemade ELF header with one PT_INTERP; no executable compiler is needed.
    binary = bytearray(84)
    binary[:6] = b'\x7fELF\x01\x01'
    struct.pack_into('<H', binary, 18, 3)
    struct.pack_into('<I', binary, 28, 52)
    struct.pack_into('<HH', binary, 42, 32, 1)
    struct.pack_into('<I', binary, 52, 3)
    inputs = vm / 'input'
    for directory in ['0','1']:
        (inputs / directory).mkdir(parents=True)
        (inputs / directory / 'binary').write_bytes(binary)
    (inputs / '0').chmod(0o700)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        tar.add(inputs / '0', arcname='0')
        tar.add(inputs / '1', arcname='1')
    monkeypatch.setattr(linuxvm_worker.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(archive.getvalue())))
    root = vm / 'tools'
    root.mkdir()
    linuxvm_worker.prepare('testkey', '1', root)
    assert (root / 'testkey/0/binary.i386').read_bytes() == binary
    # The worker runs on Linux, but this structural test also runs on Windows. Parse the
    # quoted shell arguments before comparing paths rather than assuming one separator style.
    command = shlex.split((root / 'testkey/0/binary').read_text().splitlines()[1])
    assert command[0] == 'exec' and Path(command[1]) == root / 'testkey/1/ld-linux.so.2'
    assert command[2] == '--library-path' and Path(command[3]) == root / 'testkey/1'
    assert (root / 'testkey/1/binary').read_bytes() == binary
    assert not (root / 'testkey/1/binary.i386').exists()
    if sys.platform != 'win32':
        assert (root / 'testkey/0').stat().st_mode & 0o555 == 0o555
    # A second publisher must retain the immutable snapshot already used by jobs.
    (root / 'testkey/keep').write_text('active snapshot')
    linuxvm_worker.prepare('testkey', '1', root)
    assert (root / 'testkey/keep').read_text() == 'active snapshot'


def test_proxy_installer_preserves_case_colliding_archive_without_extracting(vm, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(linuxvm, 'setup', lambda **_: {'ready': True})
    monkeypatch.setattr(linuxvm, 'status', lambda: (True, ''))
    archive = vm / 'compiler.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, content in [('libC.so.1', b'C++ runtime'), ('libc.so.1', b'C runtime')]:
            info = tarfile.TarInfo('usr/lib/' + name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    profile = replace(toolchains.get('ido-5.2'), requires=[], parts=[{
        'url': archive.as_uri(), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'platforms': ['linux-x86_64']}])
    monkeypatch.setattr(toolchains, 'registry', lambda: {'ido-5.2': profile})
    original_registered = toolchains._registered
    monkeypatch.setattr(toolchains, '_registered', lambda tid: toolchains.registry().get(tid) or original_registered(tid))
    monkeypatch.setattr(toolchains, '_extract', lambda *_: pytest.fail('must extract on Linux'))
    directory = toolchains.install('ido-5.2', accept_license=True, log=lambda *_: None)
    metadata = toolchains.installed('ido-5.2')
    saved = directory / metadata['archive_parts'][0]['archive']
    assert saved.read_bytes() == archive.read_bytes()
    assert toolchains.require('ido-5.2') == directory
    metadata.pop('archive_parts')
    (directory / '.decompnet-toolchain.json').write_text(json.dumps(metadata))
    with pytest.raises(toolchains.ToolchainError, match='Reinstall'):
        toolchains.require('ido-5.2')


def test_guest_archive_checksum_and_escape_are_rejected(tmp_path):
    archive = tmp_path / 'pack.tar'
    with tarfile.open(archive, 'w') as tar:
        info = tarfile.TarInfo('../escape')
        info.size = 4
        tar.addfile(info, io.BytesIO(b'bad!'))
    part = {'archive': archive.name, 'sha256': '0' * 64}
    with pytest.raises(ValueError, match='checksum'):
        linuxvm_worker.unpack_part(tmp_path, part)
    part['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(tarfile.FilterError):
        linuxvm_worker.unpack_part(tmp_path, part)


@pytest.mark.skipif(sys.platform == 'win32', reason='guest worker uses POSIX subprocess limits')
def test_guest_compile_creates_export_output_parents(tmp_path, monkeypatch):
    monkeypatch.setattr(linuxvm_worker, 'child_limits', lambda: None)
    result = linuxvm_worker.compile_job({
        'files': {}, 'outputs': ['build/src/unit/probe.o'], 'timeout': 5,
        'argv': [sys.executable, '-c',
                 "from pathlib import Path; Path('build/src/unit/probe.o').write_bytes(b'object')"],
    }, tmp_path)
    assert result['returncode'] == 0, result['stderr']
    assert base64.b64decode(result['files']['build/src/unit/probe.o']) == b'object'


def test_guest_validates_output_paths_before_starting_compiler(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('compiler must not start with an escaping output path')
    monkeypatch.setattr(linuxvm_worker.subprocess, 'Popen', unexpected)
    with pytest.raises(ValueError, match='escapes'):
        linuxvm_worker.compile_job({'files': {}, 'outputs': ['../outside/probe.o'], 'argv': [], 'timeout': 5}, tmp_path)


def test_a_runner_that_worked_survives_one_missed_check(vm, monkeypatch, caplog):
    # Under many compiles Docker missed a check now and then, and every compile in the next 10 seconds failed:
    # 34 polishes of an N64 game in bursts, and one function given up on.
    answers, slept = [], []
    def plain(args, timeout=30, **kwargs):
        ok = answers.pop(0) if answers else True
        return subprocess.CompletedProcess(args, 0 if ok else 1, b'', b'' if ok else b'Error: daemon busy')
    monkeypatch.setattr(linuxvm, '_plain', plain)
    monkeypatch.setattr(linuxvm.time, 'sleep', slept.append)
    answers[:] = [False]
    assert not linuxvm.status()[0] and slept == []  # never ready in this process: said at once, for Setup
    answers[:] = [True]
    assert linuxvm.status(refresh=True) == (True, '')
    answers[:] = [False, True]
    assert linuxvm.status(refresh=True) == (True, '') and slept == [1]
    answers[:] = [False, False, False]
    ok, why = linuxvm.status(refresh=True)
    assert not ok and 'not ready' in why and slept == [1, 1, 2]
    assert 'daemon busy' in caplog.text
    # a failure is remembered briefly, a success for 10 seconds
    answers[:] = [True]
    assert not linuxvm.status()[0]
    monkeypatch.setattr(linuxvm, '_status', (False, why, linuxvm.time.monotonic() - linuxvm.FAILED_FOR - 1))
    assert linuxvm.status() == (True, '')


class _Later:
    """Holds a restart thread for the test to run once the check that started it has returned."""
    pending = []

    def __init__(self, target, **_):
        self.target = target

    def start(self):
        _Later.pending.append(self.target)


def _run_restarts():
    while _Later.pending:
        _Later.pending.pop(0)()


def _colima(monkeypatch, listed, docker=b'Cannot connect to the Docker daemon at unix:///x/docker.sock. '
                                         b'Is the docker daemon running?'):
    calls = []
    def plain(args, timeout=30, **kwargs):
        calls.append(args)
        if args[3:5] == ['image', 'inspect']:
            return subprocess.CompletedProcess(args, 1, b'', docker)
        if args[1] == 'list':
            return subprocess.CompletedProcess(args, 0, json.dumps({'name': 'test-compilers', 'status': listed}).encode(), b'')
        return subprocess.CompletedProcess(args, 0, b'', b'')
    monkeypatch.setattr(linuxvm, '_plain', plain)
    monkeypatch.setattr(linuxvm.time, 'sleep', lambda _: None)
    monkeypatch.setattr(linuxvm.threading, 'Thread', _Later)
    _Later.pending.clear()
    monkeypatch.setattr(linuxvm, '_restarted', None)
    return calls


def test_a_vm_that_died_under_colima_is_restarted_once(vm, monkeypatch, caplog):
    # Twice in one night the VM's process died while Colima listed it as running; every compiler that runs in it
    # failed until someone ran `colima restart` by hand, and that waited 7 minutes on the dead VM's shutdown.
    calls = _colima(monkeypatch, 'Running')
    ok, why = linuxvm.status()
    assert not ok and why == linuxvm.RESTARTING
    assert linuxvm.status(refresh=True)[1] == linuxvm.RESTARTING  # while it restarts
    _run_restarts()
    restarts = [c for c in calls if c[1] in ('stop', 'start')]
    assert restarts == [['/usr/bin/colima', 'stop', '--force', 'test-compilers'],
                        ['/usr/bin/colima', 'start', 'test-compilers', '--activate=false']]
    assert linuxvm._status is None and not linuxvm._restarting.locked()  # the next check looks again
    assert 'restarting it' in caplog.text and 'running again' in caplog.text
    # still dead a minute later: not restarted again until RESTART_EVERY has passed
    calls.clear()
    assert not linuxvm.status(refresh=True)[0]
    assert not [c for c in calls if c[1] in ('stop', 'start')]
    monkeypatch.setattr(linuxvm, '_restarted', linuxvm.time.monotonic() - linuxvm.RESTART_EVERY - 1)
    assert linuxvm.status(refresh=True)[1] == linuxvm.RESTARTING
    _run_restarts()


def test_a_restart_in_progress_is_reported_and_not_doubled(vm, monkeypatch):
    calls = _colima(monkeypatch, 'Running')
    linuxvm._restarting.acquire()
    try:
        assert linuxvm.status() == (False, linuxvm.RESTARTING)
        assert not [c for c in calls if c[1] in ('list', 'stop', 'start')]
    finally:
        linuxvm._restarting.release()


@pytest.mark.parametrize('listed, docker', [
    ('Stopped', b'Cannot connect to the Docker daemon at unix:///x/docker.sock.'),  # the user stopped it
    ('Running', b'Error: No such image: python:3.12-slim'),  # reachable: Setup pulls the image, not a restart
])
def test_a_stopped_vm_or_a_missing_image_is_left_to_setup(vm, monkeypatch, listed, docker):
    calls = _colima(monkeypatch, listed, docker)
    ok, why = linuxvm.status()
    assert not ok and 'Prepare Linux runner' in why
    assert not [c for c in calls if c[1] in ('stop', 'start')]
