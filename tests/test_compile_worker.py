"""Deep-search compiles run in a child process (compile_worker.py) with the same result, counters and slots."""
import sys
import threading
import time

import pytest

from decompnet import compile_sessions, compile_timing, compile_worker, compiler

COMPILER = '''import sys, time
source, obj = sys.argv[1], sys.argv[2]
text = open(source).read()
if 'slow' in text:time.sleep(1)
open(obj, 'wb').write(b'object:' + text.encode())
'''


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMP_HOME', str(tmp_path / 'home'))
    script = tmp_path / 'cc.py'
    script.write_text(COMPILER)
    # Every child needs the same import path as this process to find decompnet.
    monkeypatch.setenv('PYTHONPATH', ':'.join(path for path in sys.path if path))
    return compiler.CompilerConfig([sys.executable, str(script), '{src}', '{obj}'], sandbox='none', timeout=30)


def test_a_child_compiles_what_this_process_would(cfg):
    local = compiler.compile_source(cfg, 'int x;', 'int f(void){return 1;}')
    counts = compile_timing.Counts()
    with compile_sessions.session(), compile_worker.remote(), compile_timing.collect(counts):
        remote = compiler.compile_source(cfg, 'int x;', 'int f(void){return 1;}')
        again = compiler.compile_source(cfg, 'int x;', 'int f(void){return 2;}')
    assert (remote.ok, remote.obj, remote.log) == (local.ok, local.obj, local.log)
    assert again.ok and again.obj != remote.obj
    # The child's counters reach this thread's collector: the deep search's throughput reads them.
    assert counts.values.get('invocations') == 2 and counts.values.get('requests') == 2


def test_a_compile_holds_this_process_compile_slot(cfg, monkeypatch):
    held = []
    real = compiler._slot
    def slot():
        held.append(True)
        return real()
    monkeypatch.setattr(compiler, '_slot', slot)
    with compile_worker.remote():
        assert compiler.compile_source(cfg, '', 'int f(void){return 1;}').ok
    assert held == [True]


def test_without_a_child_compiles_run_here(cfg, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('no processes left')
    monkeypatch.setattr(compile_worker, 'Worker', broken)
    with compile_worker.remote():
        result = compiler.compile_source(cfg, '', 'int f(void){return 1;}')
    assert result.ok and result.obj.startswith(b'object:')


def test_a_cancelled_compile_leaves_the_child_usable(cfg):
    stop = threading.Event()
    def check():
        if stop.is_set():
            raise RuntimeError('cancelled')
    with compile_worker.remote(), compiler.cancellable(check):
        threading.Timer(.2, stop.set).start()
        started = time.monotonic()
        with pytest.raises(RuntimeError, match='cancelled'):
            compiler.compile_source(cfg, '', 'int slow(void){return 1;}')
        assert time.monotonic() - started < 1
        stop.clear()
        # The cancelled compile's late reply is dropped: this one gets its own object.
        result = compiler.compile_source(cfg, '', 'int f(void){return 3;}')
    assert result.ok and b'return 3' in result.obj
