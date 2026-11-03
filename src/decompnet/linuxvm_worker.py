"""Small standard-library-only worker inside the pinned Linux compiler image."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MAX_FILE = 64 << 20
MAX_TOTAL = 128 << 20


def work_path(root: Path, name: str) -> Path:
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Compiler workspace path escapes its folder.')
    return path


def wrap_i386(tree: Path, runtime: str) -> None:
    """The guest cache keeps original packs intact; only its private copies get loader wrappers."""
    for path in tree.rglob('*'):
        if path.is_symlink() or not path.is_file() or path.suffix == '.i386':
            continue
        with path.open('rb') as f:
            head = f.read(52)
            if len(head) != 52 or head[:5] != b'\x7fELF\x01' or int.from_bytes(head[18:20], 'little') != 3:
                continue
            offset, size, count = struct.unpack_from('<I', head, 28)[0], *struct.unpack_from('<HH', head, 42)
            dynamic = False
            for i in range(count):
                f.seek(offset + size*i)
                if int.from_bytes(f.read(4), 'little') == 3:
                    dynamic = True
                    break
        if dynamic:
            real = path.with_name(path.name + '.i386')
            path.replace(real)
            path.write_text('#!/bin/sh\nexec ' + shlex.quote(runtime + '/ld-linux.so.2') +
                            ' --library-path ' + shlex.quote(runtime) + ' --argv0 "$0" "$0.i386" "$@"\n')
            path.chmod(0o755)
            real.chmod(0o755)


def deb_tar(path: Path) -> bytes:
    with path.open('rb') as file:
        if file.read(8) != b'!<arch>\n':
            raise ValueError('The pinned runtime archive is not a Debian package.')
        while header := file.read(60):
            if len(header) != 60:
                break
            name = header[:16].decode().strip().rstrip('/')
            size = int(header[48:58])
            data = file.read(size)
            if size & 1:
                file.read(1)
            if name.startswith('data.tar'):
                return data
    raise ValueError('The Debian runtime has no data archive.')


def unpack_part(directory: Path, part: dict) -> None:
    archive = work_path(directory, part['archive'])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != part['sha256']:
        raise ValueError('The staged compiler archive checksum does not match; reinstall its pack.')
    destination = work_path(directory, part.get('into', '.'))
    destination.mkdir(parents=True, exist_ok=True)
    if part.get('binary'):
        target = work_path(destination, part['binary'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
        target.chmod(0o755)
        return
    with tempfile.TemporaryDirectory(prefix='unpack-', dir=directory.parent) as temporary:
        root = Path(temporary)
        if archive.name.endswith('.deb'):
            with tarfile.open(fileobj=io.BytesIO(deb_tar(archive)), mode='r:*') as tar:
                tar.extractall(root, filter='data')
        else:
            with tarfile.open(archive, mode='r:*') as tar:
                tar.extractall(root, filter='data')
        source = work_path(root, part.get('subdir') or '.')
        keep = part.get('keep') or {str(item.relative_to(source)): str(item.relative_to(source)) for item in source.iterdir()}
        for name, target in keep.items():
            item, output = work_path(source, name), work_path(destination, target)
            output.parent.mkdir(parents=True, exist_ok=True)
            if item.is_symlink():
                output.unlink(missing_ok=True)
                output.symlink_to(os.readlink(item))
            elif item.is_dir():
                shutil.copytree(item, output, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(item, output)
            if part.get('executable'):
                for file in output.rglob('*') if output.is_dir() else [output]:
                    if file.is_file() and not file.is_symlink():
                        file.chmod(file.stat().st_mode | 0o111)


def hydrate(stage: Path, final: Path, runtime_index: str) -> None:
    """Extract preserved packs on Linux, then make the shared snapshot usable by unprivileged jobs."""
    for directory in stage.iterdir():
        manifest = directory / '.decompnet-toolchain.json'
        if directory.is_dir() and manifest.is_file():
            for part in json.loads(manifest.read_text()).get('archive_parts', []):
                unpack_part(directory, part)
            shutil.rmtree(directory / '.archives', ignore_errors=True)
    if runtime_index:
        runtime = str(final / runtime_index)
        for directory in stage.iterdir():
            if directory.is_dir() and directory.name != runtime_index:
                wrap_i386(directory, runtime)
    for path in stage.rglob('*'):
        if not path.is_symlink():
            mode = path.stat().st_mode
            path.chmod(mode | (0o555 if path.is_dir() or mode & 0o111 else 0o444))
    stage.chmod(0o755)


def prepare(key: str, runtime_index: str, root: Path = Path('/tools')) -> None:
    final = root / key
    if (final / '.ready').is_file():
        return
    stage = Path(tempfile.mkdtemp(prefix='stage-', dir=root))
    try:
        with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode='r:*') as archive:
            archive.extractall(stage, filter='data')
        hydrate(stage, final, runtime_index)
        (stage / '.ready').touch()
        try:
            stage.rename(final)
        except OSError:
            # Another app process may have published the same immutable snapshot meanwhile.
            # Never remove its cache: an in-flight compile can already be reading it.
            if not (final / '.ready').is_file():
                raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def child_limits():
    import resource

    # Physical memory is capped by Docker; the virtual cap allows i386 translation's reserved address range.
    for kind, value in [(resource.RLIMIT_AS, 2 << 30), (resource.RLIMIT_CPU, 30),
                        (resource.RLIMIT_FSIZE, MAX_FILE), (resource.RLIMIT_CORE, 0)]:
        resource.setrlimit(kind, (value, value))


def compile_job(payload: dict, root: Path = Path('/work')) -> dict:
    total = 0
    for name, encoded in payload['files'].items():
        data = base64.b64decode(encoded, validate=True)
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise ValueError('Compiler workspace exceeds the transfer limit.')
        path = work_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # Export builds create build/src/<unit> on the host; only selected sources cross
    # into the fresh guest workspace. Recreate output parents before the assembler runs.
    outputs = payload.get('outputs')
    for name in outputs or []:
        work_path(root, name).parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(payload['argv'], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, preexec_fn=child_limits)
    try:
        stdout, stderr = proc.communicate(timeout=payload['timeout'])
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return {'timeout': True}
    paths = list(root.rglob('*')) if outputs is None else [work_path(root, name) for name in outputs]
    files = {}
    total = 0
    for path in paths:
        if path.is_file() and not path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError('Compiler output escapes its workspace.')
            size = path.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise ValueError('Compiler output exceeds the transfer limit.')
            files[str(path.relative_to(root))] = base64.b64encode(path.read_bytes()).decode()
    return {'returncode': proc.returncode, 'stdout': stdout.decode(errors='replace')[-16000:],
            'stderr': stderr.decode(errors='replace')[-16000:], 'files': files}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'hydrate':
        hydrate(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
        return
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
        prepare(sys.argv[2], sys.argv[3])
        return
    try:
        result = compile_job(json.load(sys.stdin))
    except Exception as error:
        result = {'error': f'{type(error).__name__}: {error}'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
