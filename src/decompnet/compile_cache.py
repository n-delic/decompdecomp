"""A persistent, content-addressed store of objects the single-function compiler built successfully.

The per-worker session cache (compile_sessions.py) forgets everything when a search ends, so a restarted app, the
next search, a render worker or the permuter's worker compiled the same C with the same compiler again. This store
keeps those objects on disk under the data home, shared by every process that uses it.

It is reached only from `compiler.compile_source`, at the same places and under the same eligibility rules as the
session cache, and is keyed by that cache's exact key plus what a key that outlives the process also needs: the
compiler's pinned manifest and the content of every program on its command line (the session key has only their
size and modification time), the code that writes the compiler's input, the sandbox mode and this store's format.
Anything this process cannot fingerprint (a folder no manifest pins, a program found on PATH, WSL and the Linux
VM's copies) compiles as before.

It stores objects, never a verdict: a cached object is linked and compared byte for byte exactly like a fresh one.
Failures, timeouts and cancelled compiles are never written. Each entry carries a SHA-256 of its contents; a
corrupt or truncated one is a miss and is removed. Size is bounded (DECOMP_OBJECT_CACHE_MB, default 1024, 0 turns
the store off) by removing the least recently used entries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import sys
import threading
import time
from pathlib import Path

from . import compile_timing

FORMAT = 1
MAGIC = b'DDOBJ\x00\x01\n'
DIGEST_BYTES = 32
META_LENGTH = struct.Struct('>I')
DEFAULT_LIMIT_MB = 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024  # the session cache's per-entry limit
# A process sweeps on its first store, then after writing this fraction of the limit: entries from earlier runs are
# counted without scanning the folder on every compile.
SWEEP_FRACTION = 16
KEEP_FRACTION = 0.9  # a sweep leaves room, so the next stores don't start another at once
STALE_SECONDS = 3600  # temporary files a killed writer left, and a sweep lock a killed sweeper left
SWEEP_LOCK = '.sweep.lock'
_PACKAGE = Path(__file__).resolve().parent
_MEMO_LIMIT = 256

_lock = threading.Lock()
_digests = {}
_written = {}
_sweepers = {}
_home = []


def limit_bytes() -> int:
    """The store's size limit; 0 when DECOMP_OBJECT_CACHE_MB turns it off. A value that isn't a number keeps the
    default rather than turning the store off by accident."""
    try:
        megabytes = int(os.environ.get('DECOMP_OBJECT_CACHE_MB', DEFAULT_LIMIT_MB))
    except ValueError:
        megabytes = DEFAULT_LIMIT_MB
    return max(0, megabytes) * 1024 * 1024


def directory() -> Path:
    """Beside the app's other private state in the data home, one folder per format."""
    home = os.environ.get('DECOMP_HOME')
    if not home:
        # The remembered home is a file read; once per process is enough. An entry in another home's cache after
        # the user picks a new one is still a correct entry, only in the old place.
        if not _home:
            from .app import edition
            _home.append(edition.home_dir())
        home = _home[0]
    return Path(home).expanduser() / '.decomp' / 'object-cache' / f'v{FORMAT}'


def key(cfg, session_key: bytes, mode: str, pipeline: bytes) -> bytes | None:
    """The persistent key for a compile the session cache may reuse (`session_key`), or None when the store is off
    or the compiler can't be fingerprinted beyond this process. `mode` is the sandbox the compile runs in (it decides
    the compiler's view of its folder), `pipeline` the identity of the code that writes the compiler's input."""
    if limit_bytes() <= 0:
        return None
    toolchain = _compiler_identity(cfg, mode)
    if toolchain is None:
        return None
    digest = hashlib.sha256(b'decompdecomp object cache\0')
    for part in (repr((FORMAT, mode, sys.platform, sys.version)).encode(), pipeline, session_key, toolchain):
        digest.update(len(part).to_bytes(8, 'little'))
        digest.update(part)
    return digest.digest()


def _compiler_identity(cfg, mode: str) -> bytes | None:
    """What the compiler is, by content: the session key knows its programs by size and modification time, which
    is enough within one process but not for an object kept across restarts and reinstalls."""
    if mode in ('wsl', 'linux-vm'):
        # The programs run from copies inside another system, by paths this process can't read, with that system's
        # runtime. shortcut: these compile fresh in every new session; fingerprint the VM's and WSL's copies to
        # include them (ROADMAP.md, "Persistent compiled-object cache").
        return None
    from . import toolchains
    roots, parts = [], []
    for folder in dict.fromkeys(filter(None, (*cfg.ro_paths, cfg.runtime32))):
        where = Path(folder).resolve()
        manifest = where / toolchains.MANIFEST
        try:
            if manifest.is_file():
                # A pinned install: its manifest records the archives' SHA-256 and the registry entry's fingerprint.
                parts.append(('toolchain', str(where), manifest.read_bytes()))
            elif where == _PACKAGE or _PACKAGE in where.parents:
                # A driver shipped with the app (an N64 SDK's wrapper script): its folder's code, by content.
                parts.append(('app', str(where), _folder_digest(where)))
            else:
                return None  # a folder nothing pins: its programs can change without anything here noticing
        except OSError:
            return None
        roots.append(where)
    if not cfg.cmd or not os.path.isabs(cfg.cmd[0]):
        return None  # a program found on PATH: which one runs, and what it is, can change between runs
    for arg in cfg.cmd:
        if not os.path.isabs(arg) or not Path(arg).is_file():
            continue
        program = Path(arg).resolve()
        if not any(root in program.parents for root in roots) and program != Path(sys.executable).resolve():
            return None
        try:
            parts.append(('program', arg, _file_digest(program)))
        except OSError:
            return None
    if not any(kind == 'program' for kind, *_ in parts):
        return None
    return repr(parts).encode()


def _memo(signature, compute):
    with _lock:
        if signature in _digests:
            return _digests[signature]
    value = compute()
    with _lock:
        if len(_digests) >= _MEMO_LIMIT:
            _digests.clear()
        _digests[signature] = value
    return value


def _file_digest(path: Path) -> bytes:
    """A program's SHA-256, read once per process per version of the file (compilers can be tens of MB)."""
    st = path.stat()
    def compute():
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        return digest.digest()
    return _memo(('file', str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns), compute)


def _folder_digest(folder: Path) -> bytes:
    """The files directly in a driver's folder, by content: a driver may import its neighbours."""
    with os.scandir(folder) as entries:
        files = sorted((entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                       for entry in entries if entry.is_file())
    def compute():
        digest = hashlib.sha256()
        for name, *_ in files:
            digest.update(name.encode() + b'\0' + (folder / name).read_bytes())
        return digest.digest()
    return _memo(('folder', str(folder), tuple(files)), compute)


def _path(entry: bytes, root: Path | None = None) -> Path:
    name = entry.hex()
    return (root or directory()) / name[:2] / name


def load(entry: bytes):
    """A stored object as a new CompileResult, or None. A damaged entry is removed and is a miss."""
    from .compiler import CompileResult
    path = _path(entry)
    try:
        data = path.read_bytes()
    except OSError:
        compile_timing.add('object_cache_misses', 1)
        return None
    result = _decode(data)
    if result is None:
        compile_timing.add('object_cache_corrupt', 1)
        compile_timing.add('object_cache_misses', 1)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # (Windows: another process still reads it; a later miss removes it)
        return None
    try:
        os.utime(path)  # most recently used: what the sweep keeps
    except OSError:
        pass
    compile_timing.add('object_cache_hits', 1)
    obj, log, language = result
    return CompileResult(True, obj, log, language)


def _decode(data: bytes):
    header = len(MAGIC) + DIGEST_BYTES
    if len(data) < header + META_LENGTH.size or not data.startswith(MAGIC):
        return None
    payload = data[header:]
    if hashlib.sha256(payload).digest() != data[len(MAGIC):header]:
        return None
    (length,) = META_LENGTH.unpack_from(payload)
    try:
        meta = json.loads(payload[META_LENGTH.size:META_LENGTH.size + length])
        return payload[META_LENGTH.size + length:], str(meta['log']), str(meta['language'])
    except (ValueError, KeyError, TypeError):
        return None


def _encode(result) -> bytes:
    meta = json.dumps({'log': result.log, 'language': result.language}).encode()
    payload = META_LENGTH.pack(len(meta)) + meta + result.obj
    return MAGIC + hashlib.sha256(payload).digest() + payload


def store(entry: bytes, result) -> None:
    """Keep a successful compile's object. Written whole or not at all (a file beside it, then os.replace), so
    another process reads the old entry, the new one or none, never half. A failure to write never fails a compile."""
    limit = limit_bytes()
    if not limit or not result.ok or result.obj is None:
        return
    data = _encode(result)
    if len(data) > MAX_ENTRY_BYTES or len(data) > limit // 8:
        return
    path = _path(entry)
    if path.exists():
        return  # (another worker of the same search stored the same object)
    root = path.parent.parent
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as error:
        logging.getLogger('decompdecomp').debug('the compiled-object cache could not store an object: %s', error)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return
    compile_timing.add('object_cache_stores', 1)
    _schedule_sweep(root, limit, len(data))


def _schedule_sweep(root: Path, limit: int, size: int) -> None:
    with _lock:
        written = _written.get(root)
        _written[root] = (written or 0) + size
        due = written is None or _written[root] >= limit // SWEEP_FRACTION
        running = _sweepers.get(root)
        if not due or (running is not None and running.is_alive()):
            return
        _written[root] = 0
        thread = _sweepers[root] = threading.Thread(target=sweep, args=(root, limit), daemon=True,
                                                    name='object-cache-sweep')
    # In the background: a sweep scans the whole store, which no compile should wait for.
    thread.start()


def wait_for_sweeps(timeout: float = 30) -> None:
    """Let running sweeps finish (tests, and a benchmark that reports the store's size)."""
    with _lock:
        threads = list(_sweepers.values())
    for thread in threads:
        thread.join(timeout)


def sweep(root: Path | None = None, limit: int | None = None) -> int:
    """Remove the least recently used entries until the store is within its limit, and what killed writers left.
    One process sweeps at a time (a lock file); the others skip. Returns how many entries were removed."""
    root = root or directory()
    limit = limit_bytes() if limit is None else limit
    lock = root / SWEEP_LOCK
    try:
        root.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - lock.stat().st_mtime < STALE_SECONDS:
                return 0
            lock.unlink(missing_ok=True)  # a sweeper that was killed
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except OSError:
        return 0  # (another process took it between the two steps, or the folder is unavailable)
    try:
        return _remove_oldest(root, limit)
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_oldest(root: Path, limit: int) -> int:
    entries, total, now = [], 0, time.time()
    with os.scandir(root) as shards:
        for shard in shards:
            if not shard.is_dir(follow_symlinks=False):
                continue
            with os.scandir(shard.path) as files:
                for item in files:
                    try:
                        st = item.stat(follow_symlinks=False)
                    except OSError:
                        continue  # (removed meanwhile)
                    if item.name.startswith('.'):
                        if item.name.endswith('.tmp') and now - st.st_mtime > STALE_SECONDS:
                            _remove(item.path)
                        continue
                    entries.append((st.st_mtime_ns, st.st_size, item.path))
                    total += st.st_size
    removed = 0
    if total > limit:
        entries.sort()
        target = int(limit * KEEP_FRACTION)
        for _, size, path in entries:
            if total <= target:
                break
            if _remove(path):
                removed += 1
            total -= size
    if removed:
        compile_timing.add('object_cache_evictions', removed)
    return removed


def _remove(path) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False  # (gone already, or open in another process on Windows)
