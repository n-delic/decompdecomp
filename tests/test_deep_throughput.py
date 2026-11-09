"""Throughput leases share hard limits, keep exact publication and restore normal scheduling."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet import compile_timing, compiler
from decompnet.app import deep_compilation, deep_throughput, trial_bookkeeping
from decompnet.compile_admission import Admission
from decompnet.network import NetworkError


def test_cpu_defaults_and_validation(monkeypatch):
    monkeypatch.setattr(deep_throughput.os, 'cpu_count', lambda:10)
    assert deep_throughput.width(True,0)==10
    assert deep_throughput.width(True,7)==7
    assert deep_throughput.width(False,0)<=4
    for value in [-1,257,True,1.5,None]:
        with pytest.raises(NetworkError):deep_throughput.options(True,value)
    with pytest.raises(NetworkError):deep_throughput.options('yes',0)


def test_admission_leases_never_replace_occupied_gate():
    gate = Admission(1)
    assert gate.acquire(timeout=0)
    with gate.capacity(3):
        assert gate.acquire(timeout=0) and gate.acquire(timeout=0)
        assert not gate.acquire(timeout=0)
    assert gate.limit==1 and not gate.acquire(timeout=0)
    gate.release();gate.release()
    assert not gate.acquire(timeout=0)
    gate.release()
    assert gate.acquire(timeout=0)
    gate.release()


def test_overlapping_leases_and_failure_restore_capacity():
    gate = Admission(2)
    with gate.capacity(8):
        with pytest.raises(RuntimeError), gate.capacity(12):
            assert gate.limit==12
            raise RuntimeError('cancelled')
        assert gate.limit==8
    assert gate.limit==2


def test_private_compile_slots_bound_all_compiles_and_preserve_background_policy(monkeypatch):
    monkeypatch.setattr(compiler, '_SLOTS', Admission(8))
    gate = threading.BoundedSemaphore(2)
    lock = threading.Lock();counts=[0,0]
    def work():
        compiler.background_thread()
        with compiler.throughput_slots(True,gate), compiler._slot():
            with lock:
                counts[0]+=1;counts[1]=max(counts[1],counts[0])
            threading.Event().wait(.01)
            with lock:counts[0]-=1
        assert compiler._SCHEDULING.background
    with ThreadPoolExecutor(8) as pool:list(pool.map(lambda _:work(),range(16)))
    assert counts[1]==2
    assert compiler._SLOTS.active==0


def test_per_pool_metrics_are_scoped_and_numeric():
    a,b=deep_throughput.Metrics(),deep_throughput.Metrics()
    with compile_timing.collect(a):
        compile_timing.add('generated',20)
        compile_timing.add('invocations',4)
        compile_timing.add('queue',.25)
        with compile_timing.collect(b):compile_timing.add('invocations',2)
    assert a.values['invocations']==4 and b.values['invocations']==2
    stats=a.snapshot()
    assert stats['compiles_per_second']>0 and stats['generated_per_second']>0
    assert stats['compiler_queue_seconds']==.25
    assert stats.pop('pool_compiler_invocations_at_first_exact') is None
    assert all(isinstance(n,(int,float)) for n in stats.values())


def test_high_throughput_miss_buffer_is_bounded_and_flushes_on_cancel():
    from contextlib import contextmanager
    written=[]
    @contextmanager
    def tx():yield SimpleNamespace(execute=lambda *args:written.append(args))
    store=SimpleNamespace(tx=tx)
    with pytest.raises(RuntimeError), trial_bookkeeping.scope(maximum=True):
        for n in range(100):trial_bookkeeping.submit(store,[('miss ?', (n,))])
        assert not written
        trial_bookkeeping.submit(store,[('winner ?', (0,))],immediate=True)
        assert len(written)==101
        trial_bookkeeping.submit(store,[('last miss ?', (0,))])
        raise RuntimeError('cancel')
    assert len(written)==102


def test_shared_sweep_pool_lives_across_functions_and_closes_after_cancel(monkeypatch):
    pools=[];flags=[]
    class Pool:
        def __init__(self,count,maximum=False):
            self.count=count;self.maximum=maximum;self.closed=False;pools.append(self)
        def close(self):self.closed=True
    monkeypatch.setattr(deep_compilation,'Pool',Pool)
    @deep_throughput.sweep
    def run(self,slug,minutes,variants,log):
        flags.append(log.compile_pool)
        assert compiler._SLOTS.limit>=log.compile_pool.count
        raise RuntimeError('stop')
    owner=SimpleNamespace(config=lambda _: {'maximum_throughput':True,'compile_slots':12})
    log=SimpleNamespace()
    with pytest.raises(RuntimeError):run(owner,'game',1,20,log)
    assert flags==pools and pools[0].closed and log.compile_pool is None


def test_configurable_pool_runs_real_compiler_and_reports_invocations(monkeypatch):
    import shutil
    cc=shutil.which('clang')
    if cc is None:pytest.skip('needs a native compiler for the pool integration test')
    cfg=compiler.CompilerConfig([cc,'-O2','-c','{src}','-o','{obj}','-I{include}'],sandbox='none',identity=('throughput-test',))
    sources=[f'int function_{i}(int x) {{ return x * {i+1} + {i}; }}' for i in range(12)]
    results=[]
    for width in (1,6):
        pool=deep_compilation.Pool(width,maximum=True)
        tickets=[deep_compilation.Ticket(str(i),(),lambda source=source:compiler.compile_source(cfg,'',source),lambda:None)
                 for i,source in enumerate(sources)]
        try:
            with compiler._SLOTS.capacity(width):
                for ticket in tickets:pool.submit(ticket)
                values=[ticket.future.result(timeout=30) for ticket in tickets]
            assert all(value.ok for value in values),[v.log for v in values]
            assert pool.metrics.values['invocations']==12
            assert len(pool.workers)==width
            results.append([v.obj for v in values])
        finally:pool.close()
        assert all(not worker.is_alive() for worker in pool.workers)
    assert results[0]==results[1]


def test_live_rates_do_not_include_long_preparation_and_decay_when_idle(monkeypatch):
    clock=[0.]
    monkeypatch.setattr(deep_throughput.time,'monotonic',lambda:clock[0])
    metrics=deep_throughput.Metrics()
    clock[0]=100
    metrics.add('considered',50);metrics.add('invocations',25)
    snapshot=metrics.snapshot()
    assert snapshot['candidates_per_second']==10
    assert snapshot['compiles_per_second']==5
    clock[0]=106
    assert metrics.snapshot()['compiles_per_second']==0
    assert metrics.snapshot()['candidates_per_second']==0


def test_average_compiler_slot_utilization_uses_elapsed_worker_time(monkeypatch):
    clock=[100.]
    monkeypatch.setattr(deep_throughput.time,'monotonic',lambda:clock[0])
    metrics=deep_throughput.Metrics();metrics.gate=SimpleNamespace(limit=8,active=1)
    metrics.add('execute',8);metrics.add('history_transactions',2);metrics.add('executing',1)
    clock[0]=102.
    snapshot=metrics.snapshot()
    assert snapshot['active_compiles']==1
    assert snapshot['compiler_slot_utilization_percent']==50
    assert snapshot['history_transactions']==2


@pytest.mark.parametrize('blocked', ['search','background','shared'])
def test_waiting_compiles_identify_the_gate_without_reporting_execution(monkeypatch, blocked):
    gates = {name:Admission(1) for name in ('search','background','shared')}
    assert gates[blocked].acquire(timeout=0)
    monkeypatch.setattr(compiler, '_SLOTS', gates['shared'])
    monkeypatch.setattr(compiler, '_BACKGROUND_SLOTS', gates['background'])
    metrics = deep_throughput.Metrics();metrics.gate = gates['search']
    release = threading.Event();entered = threading.Event()
    def run():
        compiler.background_thread()
        with compile_timing.collect(metrics), compiler.throughput_slots(False,gates['search']), compiler._slot():
            entered.set();assert release.wait(3)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run)
        try:
            import time
            deadline = time.monotonic()+3
            while metrics.snapshot()['compiler_waiting_'+blocked] != 1 and time.monotonic()<deadline:
                threading.Event().wait(.01)
            stats = metrics.snapshot()
            assert stats['compiler_waiting_'+blocked] == 1 and stats['active_compiles'] == 0
            gates[blocked].release()
            assert entered.wait(3)
            stats = metrics.snapshot()
            assert stats['compiler_waiting_'+blocked] == 0 and stats['active_compiles'] == 1
            assert stats['compiler_queue_'+blocked+'_seconds'] > 0
        finally:release.set()
        future.result(3)
    assert metrics.snapshot()['active_compiles'] == 0


def test_cancelled_gate_wait_clears_metrics_and_releases_partial_admission(monkeypatch):
    shared = Admission(1);assert shared.acquire(timeout=0)
    local = Admission(2)
    monkeypatch.setattr(compiler, '_SLOTS', shared)
    metrics = deep_throughput.Metrics();metrics.gate = local
    count = [0]
    def cancel():
        count[0] += 1
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError), compile_timing.collect(metrics), compiler.throughput_slots(True,local), compiler.cancellable(cancel), compiler._slot():
        pytest.fail('blocked compile entered')
    assert count == [1] and local.active == 0
    assert metrics.snapshot()['compiler_waiting_shared'] == 0
    assert metrics.snapshot()['active_compiles'] == 0
    shared.release()
