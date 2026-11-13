"""The persistent compiled-object cache: reused across sessions and processes only for the exact same inputs and
compiler, bounded, self-repairing, and never a verdict."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from decompnet import compile_cache, compile_sessions, compile_timing, compiler, toolchains


@pytest.fixture
def builds(tmp_path, monkeypatch):
    """A stand-in compiler in a folder pinned like an installed toolchain, and a project header folder."""
    monkeypatch.setenv('DECOMP_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('DECOMP_OBJECT_CACHE_MB', '64')
    tool = tmp_path / 'toolchain';tool.mkdir()
    (tool / toolchains.MANIFEST).write_text('{"parts": [{"sha256": "one"}]}')
    program = tool / 'cc';program.write_bytes(b'compiler one')
    include = tmp_path / 'include';include.mkdir()
    (include / 'types.h').write_bytes(b'typedef int T;\n')
    cfg = compiler.CompilerConfig([str(program), '-O2', '-I{include}', '{src}', '-o', '{obj}'], sandbox='none',
                                  identity=('test',), ro_paths=(str(tool),), include_dirs=(str(include),))
    calls = []
    def compile_as(cfg, work, root, source, ext):
        calls.append(source)
        return compiler.CompileResult(True, b'object:' + source.encode(), 'warning: ok', language='c')
    monkeypatch.setattr(compiler, '_compile_as', compile_as)
    yield cfg, calls, tool, include
    compile_cache.wait_for_sweeps()


def entries():
    root = compile_cache.directory()
    return sorted(path for path in root.glob('*/*') if not path.name.startswith('.')) if root.exists() else []


def compile_in_session(cfg, header='', source='int entry(){return 1;}'):
    with compile_sessions.session(), compiler.cancellable(lambda: None):
        return compiler.compile_source(cfg, header, source)


def test_a_new_session_reuses_an_object_a_previous_session_stored(builds):
    cfg, calls, _, _ = builds
    before = compile_timing.snapshot()
    first = compile_in_session(cfg)
    second = compile_in_session(cfg)
    after = compile_timing.snapshot()
    assert len(calls) == 1 and len(entries()) == 1
    assert (second.ok, second.obj, second.log, second.language) == (first.ok, first.obj, first.log, first.language)
    assert after.get('object_cache_hits', 0) - before.get('object_cache_hits', 0) == 1
    assert after.get('object_cache_misses', 0) - before.get('object_cache_misses', 0) == 1
    assert after.get('object_cache_stores', 0) - before.get('object_cache_stores', 0) == 1


def test_requests_outside_a_search_reuse_stored_objects_too(builds):
    cfg, calls, _, _ = builds
    first = compiler.compile_source(cfg, '', 'int entry(){return 1;}')
    first.obj = b'caller changed'
    second = compiler.compile_source(cfg, '', 'int entry(){return 1;}')
    assert len(calls) == 1 and second.obj == b'object:int entry(){return 1;}'


def test_every_input_of_the_key_misses_when_it_changes(builds, monkeypatch):
    cfg, calls, tool, include = builds
    compile_in_session(cfg);compile_in_session(cfg)
    assert len(calls) == 1
    compile_in_session(cfg, source='int entry(){return 2;}');assert len(calls) == 2
    compile_in_session(cfg, header='int x;');assert len(calls) == 3
    header = include / 'types.h';stamp = header.stat()
    header.write_bytes(b'typedef long T;\n');os.utime(header, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    compile_in_session(cfg);assert len(calls) == 4
    compile_in_session(replace(cfg, cmd=[*cfg.cmd, '-g']));assert len(calls) == 5
    monkeypatch.setenv('SYNTHETIC_COMPILER_ENV', 'changed');compile_in_session(cfg);assert len(calls) == 6
    monkeypatch.delenv('SYNTHETIC_COMPILER_ENV')
    # Another compiler with the same size and time stamp: only its content says it differs.
    program = Path(cfg.cmd[0]);stamp = program.stat()
    program.write_bytes(b'compiler two');os.utime(program, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    compile_in_session(cfg);assert len(calls) == 7
    (tool / toolchains.MANIFEST).write_text('{"parts": [{"sha256": "two"}]}')
    compile_in_session(cfg);assert len(calls) == 8
    compile_in_session(cfg);assert len(calls) == 8


def test_a_corrupt_or_truncated_entry_is_a_miss_and_is_replaced(builds):
    cfg, calls, _, _ = builds
    compile_in_session(cfg)
    [entry] = entries()
    for damage in (lambda data: data[:-3], lambda data: data[:-1] + bytes([data[-1] ^ 1]), lambda data: b''):
        entry.write_bytes(damage(entry.read_bytes()))
        before = compile_timing.snapshot().get('object_cache_corrupt', 0)
        result = compile_in_session(cfg)
        assert result.obj == b'object:int entry(){return 1;}'
        assert compile_timing.snapshot().get('object_cache_corrupt', 0) == before + 1
        assert compile_cache.load(bytes.fromhex(entry.name)).obj == result.obj
    assert len(calls) == 4


def test_the_store_stays_within_its_limit_keeping_the_most_recently_used(builds):
    keys = [bytes([index]) * 32 for index in range(24)]
    for index, key in enumerate(keys):
        compile_cache.store(key, compiler.CompileResult(True, bytes(100_000), ''))
        os.utime(compile_cache._path(key), ns=(index * 10**9, index * 10**9))
    compile_cache.wait_for_sweeps()
    compile_cache.load(keys[0])  # used again: the newest now
    removed = compile_cache.sweep(limit=1024 * 1024)
    sizes = sum(path.stat().st_size for path in entries())
    assert removed > 0 and sizes <= 0.9 * 1024 * 1024
    assert compile_cache.load(keys[0]) is not None and compile_cache.load(keys[-1]) is not None
    assert compile_cache.load(keys[1]) is None


def test_storing_past_a_fraction_of_the_limit_sweeps_in_the_background(builds, monkeypatch):
    monkeypatch.setenv('DECOMP_OBJECT_CACHE_MB', '1')
    for index in range(40):
        compile_cache.store(index.to_bytes(32, 'big'), compiler.CompileResult(True, bytes(50_000), ''))
    compile_cache.wait_for_sweeps()
    compile_cache.sweep()  # (the last background sweep may have started before the last stores)
    assert sum(path.stat().st_size for path in entries()) <= 1024 * 1024


def test_zero_turns_the_store_off(builds, monkeypatch):
    cfg, calls, _, _ = builds
    monkeypatch.setenv('DECOMP_OBJECT_CACHE_MB', '0')
    compile_in_session(cfg);compile_in_session(cfg)
    assert len(calls) == 2 and not compile_cache.directory().exists()


@pytest.mark.parametrize('change', ['program on PATH', 'unpinned folder', 'linux-vm'])
def test_compilers_it_cannot_fingerprint_compile_every_session(builds, tmp_path, change):
    cfg, calls, tool, _ = builds
    if change == 'program on PATH':
        cfg = replace(cfg, cmd=['cc', *cfg.cmd[1:]])
    elif change == 'unpinned folder':
        (tool / toolchains.MANIFEST).unlink()
    else:
        cfg = replace(cfg, linux_vm=True)
        assert compile_cache.key(cfg, b'key', 'linux-vm', b'') is None
        return
    compile_in_session(cfg);compile_in_session(cfg)
    assert len(calls) == 2 and not entries()


def test_failures_cancelled_compiles_and_artifact_captures_store_nothing(builds, monkeypatch):
    cfg, calls, _, _ = builds
    with compiler.capture_artifacts(lambda work: None):
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
    assert not entries()
    def cancel():
        if calls:raise RuntimeError('cancelled')
    with compile_sessions.session(), pytest.raises(RuntimeError, match='cancelled'), compiler.cancellable(cancel):
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
    assert not entries()
    monkeypatch.setattr(compiler, '_compile_as', lambda *a: compiler.CompileResult(False, None, 'timeout'))
    compile_in_session(cfg)
    assert not entries()


def test_concurrent_writers_leave_only_whole_entries(builds):
    results = {index: compiler.CompileResult(True, bytes([index]) * 5000, f'log {index}') for index in range(8)}
    def write(index):
        compile_cache.store(bytes([index % 8]) * 32, results[index % 8])
        return compile_cache.load(bytes([index % 8]) * 32)
    with ThreadPoolExecutor(8) as pool:
        loaded = list(pool.map(write, range(64)))
    assert all(result is not None and result.obj == results[index % 8].obj for index, result in enumerate(loaded))
    script = ('import sys\nfrom decompnet import compile_cache, compiler\n'
              'for index in range(40):\n'
              '    compile_cache.store(bytes([index % 4 + 100]) * 32, compiler.CompileResult(True, bytes([index % 4]) * 9000, ""))\n'
              '    assert compile_cache.load(bytes([index % 4 + 100]) * 32).obj == bytes([index % 4]) * 9000\n')
    processes = [subprocess.Popen([sys.executable, '-c', script], env=dict(os.environ)) for _ in range(4)]
    assert all(process.wait(60) == 0 for process in processes)
    assert len(entries()) == 12
    assert not [path for path in compile_cache.directory().glob('*/.*.tmp')]
