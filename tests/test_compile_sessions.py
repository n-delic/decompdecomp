"""Search-local caches preserve input identity, cancellation, artifacts and private staging."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from decompnet import compile_sessions, compiler


@pytest.fixture
def builds(tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMP_HOME', str(tmp_path / 'scratch'))
    include = tmp_path / 'include';include.mkdir()
    (include / 'types.h').write_bytes(b'typedef int T;\r\n')
    cfg = compiler.CompilerConfig(['fake-cc'], sandbox='none', identity=('test',), include_dirs=(str(include),))
    calls = []
    def compile_as(cfg, work, root, source, ext):
        calls.append((work, source, cfg.cmd))
        assert (work / 'include' / 'types.h').read_bytes().endswith(b'\n')
        return compiler.CompileResult(True, source.encode(), 'ok')
    monkeypatch.setattr(compiler, '_compile_as', compile_as)
    return cfg, calls, include


def test_prepared_session_reuses_headers_and_successful_objects_but_not_results_across_runs(builds):
    cfg, calls, _ = builds
    with compile_sessions.session(), compiler.cancellable(lambda: None):
        first = compiler.compile_source(cfg, 'int x;', 'int entry(){return 1;}')
        first.log = 'caller changed'
        second = compiler.compile_source(cfg, 'int x;', 'int entry(){return 1;}')
        assert second.log == 'ok' and len(calls) == 1
        work = calls[0][0]
        marker = (work / 'include' / 'types.h').stat().st_mtime_ns
        compiler.compile_source(cfg, 'int x;', 'int entry(){return 2;}')
        assert calls[-1][0] == work and (work / 'include' / 'types.h').stat().st_mtime_ns == marker
    assert not work.exists()
    with compile_sessions.session():compiler.compile_source(cfg, 'int x;', 'int entry(){return 1;}')
    assert len(calls) == 3


def test_cache_invalidates_exact_header_bytes_flags_environment_and_tool(builds, monkeypatch, tmp_path):
    cfg, calls, include = builds
    executable = tmp_path / 'compiler';executable.write_bytes(b'one')
    cfg = replace(cfg, cmd=[str(executable)])
    with compile_sessions.session():
        def run(config=cfg, header='int x;'):
            compiler.compile_source(config, header, 'int entry(){return 1;}')
        run();run();assert len(calls) == 1
        path = include / 'types.h';stamp = path.stat()
        path.write_bytes(b'typedef long T;\n')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        run();assert len(calls) == 2
        run(header='long x;');assert len(calls) == 3
        run(replace(cfg, cmd=[str(executable), '-O2']));assert len(calls) == 4
        monkeypatch.setenv('SYNTHETIC_COMPILER_ENV', 'changed');run();assert len(calls) == 5
        executable.write_bytes(b'different executable');run();assert len(calls) == 6


def test_session_resets_outputs_restores_headers_and_handles_removed_includes(builds):
    cfg, calls, include = builds
    with compile_sessions.session():
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        work = calls[0][0]
        (work / 'include' / 'types.h').write_text('compiler changed this')
        (work / 'include' / 'unexpected.h').write_text('stale')
        (work / 'stale.o').write_bytes(b'old')
        compiler.compile_source(cfg, '', 'int entry(){return 2;}')
        assert (work / 'include' / 'types.h').read_bytes() == b'typedef int T;\n'
        assert not (work / 'stale.o').exists() and not (work / 'include' / 'unexpected.h').exists()
        (include / 'types.h').unlink()
        # Observe staging directly since the test compiler expects types.h.
        compile_sessions.current().prepare(cfg, '')
        assert not (work / 'include' / 'types.h').exists()


def test_cancelled_cache_hit_raises_without_running_or_poisoning_cache(builds):
    cfg, calls, _ = builds
    def cancel():raise RuntimeError('cancelled')
    with compile_sessions.session():
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        with pytest.raises(RuntimeError, match='cancelled'), compiler.cancellable(cancel):
            compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        assert len(calls) == 1


@pytest.mark.parametrize('macro', ['__TIME__', '__DATE__', '__TIMESTAMP__', '__FILE__', '__BASE_FILE__'])
def test_volatile_or_path_dependent_inputs_do_not_reuse_objects(builds, macro):
    cfg, calls, _ = builds
    with compile_sessions.session():
        for _ in range(2):compiler.compile_source(cfg, '', f'int entry(){{return {macro}[0];}}')
    assert len(calls) == 2


def test_failures_artifact_capture_and_untrusted_code_never_short_circuit(builds, monkeypatch):
    cfg, calls, _ = builds
    with compile_sessions.session():
        for _ in range(2):
            with compiler.capture_artifacts(lambda work: calls.append(('artifact', work))):
                compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        assert len(calls) == 4 and calls[0][0] != calls[2][0]
        for _ in range(2):assert not compiler.compile_source(cfg, '', 'asm("bad");').ok
        monkeypatch.setattr(compiler, '_compile_as', lambda *a: compiler.CompileResult(False, None, 'timeout'))
        for _ in range(2):compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        assert not compile_sessions.current().results


def test_cache_bound_and_thread_ownership(builds):
    cfg, calls, _ = builds
    with compile_sessions.session() as session:
        with compile_sessions.session() as nested:assert nested is session
        for i in range(140):compiler.compile_source(cfg, '', f'int entry(){{return {i};}}')
        assert len(session.results) == 128 and session.bytes <= 32 * 1024 * 1024
        with ThreadPoolExecutor(2) as pool:
            def work(_):
                assert compile_sessions.current() is None
                with compile_sessions.session() as child:
                    assert child.owner == threading.get_ident() and child is not session
                    compiler.compile_source(cfg, '', 'int entry(){return 1;}')
            list(pool.map(work, range(2)))
        assert calls[-1][0] != calls[-2][0]


@pytest.mark.parametrize('value,expected', [('1',1), ('4',4), ('1000',256), ('bad',None), ('0',None), ('-1',None)])
def test_concurrency_override_is_bounded(monkeypatch,value,expected):
    monkeypatch.setenv('DECOMP_COMPILER_THREADS', value)
    assert compiler.concurrency_limit() == (expected or max(2, (os.cpu_count() or 4)-1))


@pytest.mark.parametrize('source', ['#define JOIN(a,b) a##b\nint entry(){return JOIN(__TI,ME__)[0];}',
                                    'int entry(){return __TI\\\nME__[0];}'])
def test_indirect_volatile_macros_cannot_enter_cache(builds, source):
    cfg, calls, _ = builds
    with compile_sessions.session():
        for _ in range(2):compiler.compile_source(cfg, '', source)
    assert len(calls) == 2


def test_session_cleanup_on_failure_and_byte_budget(builds, monkeypatch):
    cfg, calls, _ = builds
    work = None
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with compile_sessions.session() as session:
            compiler.compile_source(cfg, '', 'int entry(){return 1;}')
            work = calls[0][0]
            payload = compiler.CompileResult(True, bytes(2 * 1024 * 1024), '')
            for i in range(20):session.put(str(i), payload)
            assert len(session.results) == 16 and session.bytes == 32 * 1024 * 1024
            monkeypatch.setattr(compiler, '_compile_as', lambda *a: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
            compiler.compile_source(cfg, '', 'int entry(){return 2;}')
    assert work is not None and not work.exists() and compile_sessions.current() is None


def test_cpp_result_keeps_language_and_caller_settings_private(builds, monkeypatch):
    cfg, calls, _ = builds
    cfg = replace(cfg, cplusplus=True)
    def compile_as(cfg, work, root, source, ext):
        calls.append(ext)
        return compiler.CompileResult(ext == 'cpp', b'object' if ext == 'cpp' else None, '', language='c++' if ext == 'cpp' else 'c')
    monkeypatch.setattr(compiler, '_compile_as', compile_as)
    with compile_sessions.session():
        first = compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        first.effective_settings = {'caller':'changed'}
        second = compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        assert first.language == second.language == 'c++'
        assert second.effective_settings is None and calls == ['c', 'cpp']


def test_directory_symlinks_use_ordinary_staging_and_missing_roots_still_fail(builds, tmp_path):
    cfg, calls, include = builds
    other = tmp_path / 'other';other.mkdir();(other / 'nested.h').write_text('int nested;')
    try:(include / 'link').symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):pytest.skip('directory symlinks unavailable')
    with compile_sessions.session():
        compiler.compile_source(cfg, '', 'int entry(){return 1;}')
        assert not compile_sessions.current().results and len(calls) == 1
        assert 'session' not in calls[0][0].name
        with pytest.raises(FileNotFoundError):
            compiler.compile_source(replace(cfg, include_dirs=(str(tmp_path / 'absent'),)), '', 'int entry(){return 1;}')


@pytest.mark.parametrize('header', ['#include "/external/header.h"', '#include <external.h>',
                                   '#define H "external.h"\n#include H', '%:include "/external/header.h"',
                                   '#include_next "types.h"', '#import "/external/header.h"',
                                   '#/**/include "/external/header.h"', '#/**/include MISSING_HEADER'])
def test_uncaptured_includes_cannot_reuse_objects(builds, header):
    cfg, calls, _ = builds
    with compile_sessions.session():
        for _ in range(2):compiler.compile_source(cfg, header, 'int entry(){return 1;}')
    assert len(calls) == 2


def test_captured_literal_include_remains_cacheable(builds):
    cfg, calls, _ = builds
    with compile_sessions.session():
        for _ in range(2):compiler.compile_source(cfg, '#include "types.h"', 'int entry(){return 1;}')
    assert len(calls) == 1


@pytest.mark.parametrize('args', [['-I/external'], ['-I', '/external'], ['-include', '/external.h'],
                                 ['-Wp,-include,/external.h'], ['@/external.rsp']])
def test_external_command_include_inputs_bypass_object_cache(builds, args):
    cfg, calls, _ = builds
    cfg = replace(cfg, cmd=[*cfg.cmd, *args])
    with compile_sessions.session():
        for _ in range(2):compiler.compile_source(cfg, '', 'int entry(){return 1;}')
    assert len(calls) == 2
