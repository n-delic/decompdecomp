"""Sharing compilation must not share cancellation, diagnostics or per-request result mutation."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from decompnet import compile_sharing, compiler


def test_shared_compile_has_independent_results_and_no_persistent_hits():
    start=threading.Event(); release=threading.Event(); joined=threading.Event(); calls=[]
    def build():
        calls.append(1);start.set();assert release.wait(3)
        return compiler.CompileResult(True,b'object','ok')
    # Observe the join instead of using a sleep to guess whether the second caller started.
    original=compile_sharing.Future
    class Future(original):
        def result(self,*args,**kwargs):
            joined.set()
            return super().result(*args,**kwargs)
    compile_sharing.Future=Future
    try:
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(compile_sharing.run,'same',build)
            assert start.wait(3)
            second=pool.submit(compile_sharing.run,'same',build)
            assert joined.wait(3);release.set()
            a,b=first.result(),second.result()
        a.log='changed'
        assert b.log=='ok' and calls==[1]
        compile_sharing.run('same',build)
        assert calls==[1,1]
    finally:
        compile_sharing.Future=original
        release.set()


def test_compile_identity_includes_headers_flags_target_and_staged_files(tmp_path,monkeypatch):
    include=tmp_path/'include';include.mkdir();header=include/'types.h';header.write_text('typedef int T;')
    cfg=compiler.CompilerConfig(['fake','-O2'],sandbox='none',identity=('project','target'),include_dirs=(str(include),))
    keys=[]
    def shared(key,build):
        keys.append(key);return build()
    monkeypatch.setattr(compile_sharing,'run',shared)
    monkeypatch.setattr(compiler,'_compile_as',lambda *args:compiler.CompileResult(True,b'object','ok'))
    def run(config=cfg,context='int x;'):
        compiler.compile_source(config,context,'int f(void){return 1;}')
    run();run();assert keys[0]==keys[1]
    run(context='long x;');assert keys[-1]!=keys[0]
    run(replace(cfg,cmd=['fake','-O3']));assert keys[-1]!=keys[0]
    run(replace(cfg,identity=('project','other-target')));assert keys[-1]!=keys[0]
    header.write_text('typedef long T;');run();assert keys[-1]!=keys[0]
    keys.clear()
    with compiler.cancellable(lambda:None):run()
    with compiler.capture_artifacts(lambda _:None):run()
    compiler.compile_source(cfg,'','int f(void){return __TIME__[0];}')
    assert keys==[]


def test_background_compiles_reserve_capacity_for_interactive_requests(monkeypatch):
    monkeypatch.setattr(compiler,'_SLOTS',threading.BoundedSemaphore(2))
    monkeypatch.setattr(compiler,'_BACKGROUND_SLOTS',threading.BoundedSemaphore(1))
    entered=threading.Event();release=threading.Event();foreground=threading.Event()
    def background():
        compiler.background_thread()
        with compiler._slot():
            entered.set();assert release.wait(3)
    def interactive():
        with compiler._slot():foreground.set()
    with ThreadPoolExecutor(3) as pool:
        first=pool.submit(background);assert entered.wait(3)
        second=pool.submit(background)
        ui=pool.submit(interactive)
        try:assert foreground.wait(3)
        finally:release.set()
        first.result();second.result();ui.result()


def test_waiting_compile_checks_cancellation_and_releases_background_slot(monkeypatch):
    import pytest
    gate=threading.BoundedSemaphore(1);gate.acquire()
    background=threading.BoundedSemaphore(1)
    monkeypatch.setattr(compiler,'_SLOTS',gate)
    monkeypatch.setattr(compiler,'_BACKGROUND_SLOTS',background)
    monkeypatch.setattr(compiler._SCHEDULING,'background',True,raising=False)
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'),compiler.cancellable(cancel),compiler._slot():
        raise AssertionError('must not acquire')
    assert background.acquire(blocking=False)


def test_large_stability_checks_cache_exact_bytes_and_staged_include_names():
    compiler._stable_part.cache_clear()
    header = b'/*'+b'x'*compiler.MIN_CACHED_INPUT_BYTES+b'*/\n#include "types.h"\n'
    assert compiler._stable_inputs([header], [b'types.h'])
    assert compiler._stable_inputs([bytes(bytearray(header))], [b'types.h'])
    assert compiler._stable_part.cache_info().hits == 1
    assert not compiler._stable_inputs([header], [])
    assert not compiler._stable_inputs([header.replace(b'types.h',b'other.h')], [b'types.h'])
    assert not compiler._stable_inputs([header+b'__TIME__'], [b'types.h'])
    assert compiler._stable_part.cache_info().currsize <= 4
    before = compiler._stable_part.cache_info()
    assert compiler._stable_inputs([b'int value;'])
    assert compiler._stable_inputs([b' '* (compiler.MAX_CACHED_INPUT_BYTES+1)])
    assert compiler._stable_part.cache_info() == before
