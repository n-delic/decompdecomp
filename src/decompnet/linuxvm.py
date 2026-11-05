"""Optional local Linux compiler runner for macOS, shared by toolchain profiles.

Colima/Docker are user-managed host infrastructure, like WSL on Windows. The
compiler image is digest pinned; jobs have no network or host-folder mounts.
Only explicit Setup may start the dedicated VM or download its image.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .hostenv import NO_WINDOW

IMAGE = 'python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f'
INSTALL = 'Install Colima and the Docker CLI (`brew install colima docker`), then choose Prepare Linux runner in Compilers & tools.'
_tools_lock = threading.Lock()
_setup_lock = threading.Lock()
_prepared: set[tuple[str, str]] = set()
_status: tuple[bool, str, float] | None = None


class VMError(RuntimeError):
    pass


def profile() -> str:
    name = os.environ.get('DECOMP_LINUX_VM_PROFILE', 'decompdecomp')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', name):
        raise VMError('The Linux compiler VM profile must contain only lowercase letters, digits and hyphens.')
    return name


def wanted() -> bool:
    return sys.platform == 'darwin'


def executable(name: str) -> str | None:
    return shutil.which(name) or next((str(p) for p in [Path('/opt/homebrew/bin') / name,
        Path('/usr/local/bin') / name, Path.home() / '.docker/bin' / name] if p.is_file()), None)


def _env() -> dict:
    # A desktop app must never send compiler files to an ambient remote Docker endpoint.
    return {k: v for k, v in os.environ.items() if k not in {'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY',
            'DOCKER_CERT_PATH', 'COLIMA_HOME', 'LIMA_HOME'}}


def _docker() -> list[str]:
    exe = executable('docker')
    if not exe:
        raise VMError(INSTALL)
    socket = Path.home() / '.colima' / profile() / 'docker.sock'
    return [exe, '--host', 'unix://' + str(socket)]


def _worker() -> str:
    return Path(__file__).with_name('linuxvm_worker.py').read_text(encoding='utf-8')


def _plain(args: list[str], timeout: float = 30, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, env=_env(), creationflags=NO_WINDOW, **kwargs)


def status(refresh: bool = False) -> tuple[bool, str]:
    """Read-only readiness check: never starts a VM or pulls an image."""
    global _status
    if not wanted():
        return False, 'The Linux VM runner is for macOS; Linux runs packs directly and Windows uses WSL.'
    if not executable('colima') or not executable('docker'):
        return False, INSTALL
    if _status and not refresh and time.monotonic() - _status[2] < 10:
        return _status[:2]
    try:
        result = _plain([*_docker(), 'image', 'inspect', IMAGE], 10)
        ok = result.returncode == 0
        why = '' if ok else 'The Linux compiler runner is not ready. Choose Prepare Linux runner in Compilers & tools.'
    except (OSError, subprocess.TimeoutExpired) as error:
        ok, why = False, f'The Linux compiler runner is unavailable ({error}). {INSTALL}'
    _status = ok, why, time.monotonic()
    return ok, why


def setup(log=print) -> dict:
    """Explicit Setup starts only this app's VM and pulls the exact runtime image."""
    global _status
    if not wanted() or not executable('colima') or not executable('docker'):
        raise VMError(INSTALL)
    with _setup_lock:
        if status(refresh=True)[0]:
            log('Linux compiler runner is ready.')
            return {'ready': True}
        name = profile()
        log('Starting the dedicated Linux compiler VM (the first start downloads its OS image).')
        command = [executable('colima'), 'start', name, '--activate=false', '--vm-type', 'vz',
                   '--cpus', '2', '--memory', '3', '--disk', '12']
        if platform.machine().lower() in ('arm64', 'aarch64'):
            command += ['--vz-rosetta']
        result = _plain(command, 300)
        if result.returncode:
            raise VMError('Could not start the dedicated Linux compiler VM.\n' + result.stderr.decode(errors='replace')[-3000:])
        log('Downloading and verifying the pinned Linux compiler runtime image.')
        result = _plain([*_docker(), 'pull', '--platform', 'linux/amd64', IMAGE], 300)
        if result.returncode:
            raise VMError('Could not download the pinned Linux runtime.\n' + result.stderr.decode(errors='replace')[-3000:])
        _status = None
        if not status(refresh=True)[0]:
            raise VMError('The Linux runtime was downloaded but its local VM is unavailable. Try Prepare Linux runner again.')
        log('Linux compiler runner is ready. Compiler jobs run locally without network access.')
        return {'ready': True}


def _volume() -> str:
    return 'decompdecomp-compiler-tools'


def _container(name: str, *, write_tools: bool = False) -> list[str]:
    mode = '' if write_tools else ',readonly'
    command = [*_docker(), 'run', '--rm', '--pull', 'never', '--name', name, '--platform', 'linux/amd64', '--network', 'none',
               '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '1g',
               '--memory-swap', '1g', '--pids-limit', '128', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
               '--tmpfs', '/work:rw,nosuid,nodev,mode=1777,size=192m',
               '--tmpfs', '/var/tmp:rw,nosuid,nodev,mode=1777,size=128m',
               '--mount', 'type=volume,source=' + _volume() + ',target=/tools' + mode,
               '--env', 'QEMU_RESERVED_VA=0x60000000', '--env', 'PYTHONDONTWRITEBYTECODE=1']
    if not write_tools:
        command += ['--user', '65534:65534']
    return [*command, '-i', IMAGE, 'python3', '-c', _worker()]


def _invoke(argv: list[str], payload: bytes, timeout: float, name: str, check_cancel=None) -> subprocess.CompletedProcess:
    """Cancellation/timeout removes the container, including all of the compiler's children."""
    from .hostenv import stop_process_tree

    if check_cancel:
        check_cancel()
    transfer = tempfile.TemporaryFile()
    transfer.write(payload)
    transfer.seek(0)
    proc = subprocess.Popen(argv, stdin=transfer, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(), creationflags=NO_WINDOW)
    started = time.monotonic()
    try:
        while True:
            if check_cancel:
                check_cancel()
            try:
                out, err = proc.communicate(timeout=0.1)
                return subprocess.CompletedProcess(argv, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                if time.monotonic() - started > timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
    except BaseException:
        try:
            _plain([*_docker(), 'rm', '--force', name], 10)
        finally:
            stop_process_tree(proc)
        raise
    finally:
        transfer.close()


def prepare_tools(directories: tuple[Path, ...]) -> tuple[str, ...]:
    """Publish an immutable guest filesystem copy, keyed by install manifests and driver contents."""
    if not status()[0]:
        raise VMError(status()[1])
    digest = hashlib.sha256(IMAGE.encode() + _worker().encode())
    runtime = ''
    for index, directory in enumerate(directories):
        digest.update(directory.name.encode())
        manifest = directory / '.decompnet-toolchain.json'
        if manifest.is_file():
            digest.update(manifest.read_bytes())
        else:
            for file in sorted(directory.rglob('*')):
                if file.is_file() and '__pycache__' not in file.parts:
                    digest.update(str(file.relative_to(directory)).encode() + file.read_bytes())
        if directory.name == 'runtime-i386':
            runtime = str(index)
    key = digest.hexdigest()[:32]
    paths = tuple(f'/tools/{key}/{i}' for i in range(len(directories)))
    cache = (profile(), key)
    with _tools_lock:
        if cache in _prepared:
            return paths
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz', compresslevel=1) as archive:
            for index, directory in enumerate(directories):
                archive.add(directory.resolve(), arcname=str(index), filter=lambda info: None if '__pycache__' in Path(info.name).parts else info)
        name = 'decomp-tools-' + uuid.uuid4().hex
        result = _invoke([*_container(name, write_tools=True), 'prepare', key, runtime], stream.getvalue(), 120, name)
        if result.returncode:
            raise VMError('Could not prepare the guest compiler cache.\n' + result.stderr.decode(errors='replace')[-2000:])
        _prepared.add(cache)
    return paths


def run(argv: list[str], cwd: Path, timeout: float, *, outputs: list[str] | None = None,
        inputs: list[str] | None = None, stage: str | None = None, check_cancel=None) -> subprocess.CompletedProcess:
    """Copy inputs into Linux, compile without host mounts/network, copy selected outputs back."""
    from .linuxvm_worker import MAX_FILE, MAX_TOTAL, work_path

    files = {}
    total = 0
    for selected in inputs if inputs is not None else ['.']:
        path = work_path(cwd, selected)
        if path.is_symlink():
            raise VMError('Linux compiler inputs must be ordinary files, not symbolic links.')
        for file in path.rglob('*') if path.is_dir() else [path]:
            if file.is_symlink():
                raise VMError('Linux compiler inputs must be ordinary files, not symbolic links.')
            if not file.is_file():
                continue
            name = str(file.relative_to(cwd))
            if name in files:
                continue
            size = file.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise VMError('The compiler workspace exceeds the Linux runner transfer limit.')
            files[name] = base64.b64encode(file.read_bytes()).decode()
    payload = json.dumps({'argv': argv, 'files': files, 'outputs': outputs, 'timeout': timeout}).encode()
    name = 'decomp-compile-' + uuid.uuid4().hex
    result = _invoke(_container(name), payload, timeout + 20, name, check_cancel)
    if result.returncode:
        raise VMError('The local Linux compiler container failed.\n' + result.stderr.decode(errors='replace')[-2000:])
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise VMError('The Linux compiler runner returned an invalid response.') from error
    if response.get('timeout'):
        raise subprocess.TimeoutExpired(argv, timeout)
    if response.get('error'):
        raise VMError('The Linux compiler runner failed: ' + response['error'])
    total = 0
    for name, encoded in response.get('files', {}).items():
        path = work_path(cwd, name)
        if outputs is not None and name not in outputs:
            raise VMError('The Linux runner returned an unrequested output.')
        data = base64.b64decode(encoded, validate=True)
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise VMError('The Linux runner returned oversized output.')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return subprocess.CompletedProcess(argv, response['returncode'], response['stdout'], response['stderr'])
