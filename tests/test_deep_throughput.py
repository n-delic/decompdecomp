"""Throughput leases share hard limits, keep exact publication and restore normal scheduling."""
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import compile_timing, compiler
from decompnet.app import deep_compilation, deep_throughput, trial_bookkeeping
from decompnet.compile_admission import Admission
from decompnet.network import NetworkError


def test_cpu_defaults_and_validation(monkeypatch):
    monkeypatch.setattr(deep_throughput.os, 'cpu_count', lambda:10)
    assert deep_throughput.width(True,0)==20  # two compiles in flight per core: the slots were 15% busy at one
    assert deep_throughput.lanes(True)==15  # one and a half functions per core
    assert deep_throughput.width(True,7)==7
    monkeypatch.setattr(deep_throughput.compiler, 'concurrency_limit', lambda:17)
    assert deep_throughput.width(False,0)==17  # balanced uses the machine, not a fixed 4
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
            self.metrics=deep_throughput.Metrics()
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


def test_deep_search_workers_avoid_macos_utility_qos(monkeypatch):
    """A user-started deep search must not drop to utility QoS on macOS (efficiency cores only)."""
    from decompnet.app import workers
    calls = []
    monkeypatch.setattr(workers, 'lower_priority', lambda: calls.append('lower'))
    monkeypatch.setattr(workers.sys, 'platform', 'linux')
    workers.started_priority()
    assert calls == ['lower']
    for path in ('deep_sweep.py', 'deep_parallel.py', 'deep_compilation.py', 'deep_proposal_worker.py'):
        source = (Path(workers.__file__).parent / path).read_text()
        assert 'lower_priority' not in source, path


def test_cached_compiler_identity_follows_toolchain_files(tmp_path, monkeypatch):
    """The identity is cached for speed, but a changed compiler binary or manifest must change it."""
    import os

    from decompnet import match_bundle, toolchains
    monkeypatch.setattr(toolchains, 'path', lambda tool: tmp_path)
    monkeypatch.setattr(toolchains, 'get', lambda tool: {'name': tool})
    (tmp_path/'cc1').write_bytes(b'one')
    first = match_bundle.compiler_identity({'toolchain': 'gcc-test'})
    assert match_bundle.compiler_identity({'toolchain': 'gcc-test'}) == first
    (tmp_path/'cc1').write_bytes(b'two!')
    os.utime(tmp_path/'cc1', ns=(1, 2))
    assert match_bundle.compiler_identity({'toolchain': 'gcc-test'})['executables'] != first['executables']


def test_cached_environment_follows_changes(monkeypatch):
    before = __import__("decompnet.stamped").stamped.environment()
    monkeypatch.setenv('DECOMP_TEST_ENVIRONMENT_CACHE', '1')
    assert ('DECOMP_TEST_ENVIRONMENT_CACHE', '1') in __import__("decompnet.stamped").stamped.environment()
    monkeypatch.delenv('DECOMP_TEST_ENVIRONMENT_CACHE')
    assert __import__("decompnet.stamped").stamped.environment() == before


def test_recent_key_fast_path_notices_a_changed_function_row():
    """The fast path compares the rows: a function changed in memory never keeps the old key."""
    from types import SimpleNamespace

    from decompnet.app import deep_permuter
    search = deep_permuter.Search.__new__(deep_permuter.Search)
    store = SimpleNamespace(read_version=lambda kind, project: 3)
    search.net = SimpleNamespace(project_store=lambda pid: store)
    search.measuring_baseline = False
    search._recent_keys = {}
    search._recent_base = b'base'
    proj, fn = {'id': 1}, {'id': 2, 'best_source': 'a'}
    import time
    search._recent_fast = (3, dict(proj), dict(fn), deep_permuter.stamped.environment(), '', time.monotonic())
    assert search._recent_key(proj, fn, 'int f(void){return 1;}', None) is not None
    calls = []
    search.net.cfg = lambda proj: calls.append(True) or {}
    assert search._recent_key(proj, dict(fn, best_source='b'), 'int f(void){return 1;}', None) is None  # slow path ran
    assert calls


def test_equivalent_text_keeps_what_the_compiler_could_tell_apart():
    from decompnet.app.deep_permuter import equivalent_text as same
    assert same('int f(int x){ /* note */ return x+1; }') == same('int f(int x)\n{\n    return x + 1; // note\n}')
    assert same('i++ + j') != same('i+ ++j') and same('a - -b') != same('a--b')
    assert same('s = "a  b";') != same('s = "a b";') and same("c = ' ';") != same("c = '  ';")
    for exact in ('#define N 1\nint a = N;', 'int line = __LINE__;'):
        assert same(exact) == exact


def test_layout_counts_when_the_header_reads_line_numbers():
    from decompnet.app import deep_permuter
    search = deep_permuter.Search.__new__(deep_permuter.Search)
    search._recent_base = b'base'
    keys = {}
    for header in ('int x;', '#define ASSERT(c) check(c, __LINE__)'):
        search._recent_keys = {}
        one = search._recent_source('int f(void){return 1;}', 1, header)
        two = search._recent_source('int f(void)\n{\n  return 1;\n}', 1, header)
        keys[header] = one == two
    assert keys == {'int x;': True, '#define ASSERT(c) check(c, __LINE__)': False}


def test_the_compile_pipeline_counters_reach_the_snapshot_and_the_lanes_forward_them():
    from decompnet.app import deep_lanes
    assert set(deep_throughput.PIPELINE_COUNTERS) <= set(deep_lanes.COUNTERS)
    metrics = deep_throughput.Metrics()
    for name in deep_throughput.PIPELINE_COUNTERS:metrics.add(name, 3)
    snapshot = metrics.snapshot()
    assert all(snapshot[name] == 3 for name in deep_throughput.PIPELINE_COUNTERS)


def test_discarding_a_lookahead_window_counts_used_cancelled_and_wasted_tickets(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import deep_compilation
    counted = {}
    monkeypatch.setattr(deep_compilation.compile_timing, 'add', lambda name, value: counted.__setitem__(name, counted.get(name, 0) + value))
    class Ticket:
        def __init__(self, counted, cancels):self.counted, self.cancels = counted, cancels
        def cancel(self):return self.cancels
    tickets = {'a': Ticket(True, False), 'b': Ticket(True, False), 'c': Ticket(False, True),
               'd': Ticket(False, False), 'e': Ticket(False, False)}
    search = SimpleNamespace(tested=0, _ahead=dict(tickets))
    deep_compilation.discard(search)
    assert counted == {'ahead_used': 2, 'ahead_cancelled': 1, 'ahead_wasted': 2}
    assert search.tested == 2 and tickets['d'].counted and tickets['e'].counted  # (running work spends allowance)
    assert search._ahead == {}  # (a window is discarded once)


def test_foreground_compile_passes_queued_workers_and_idle_slots_go_to_the_sweep():
    gate = Admission(2)
    sweep = gate.group(2)
    first, second = sweep.request(), sweep.request()
    assert first.done() and second.done()
    waiting = sweep.request()
    foreground = gate.request()
    assert not waiting.done() and not foreground.done()
    sweep.finish(first)
    assert foreground.done() and not waiting.done()
    gate.release()
    assert waiting.done()
    sweep.finish(second)
    sweep.finish(waiting)
    assert gate.active == sweep.active == 0


def test_a_full_sweep_does_not_block_other_admission_and_cancelled_requests_are_skipped():
    gate = Admission(3)
    sweep = gate.group(1)
    first = sweep.request()
    cancelled, waiting = sweep.request(), sweep.request()
    sweep.finish(cancelled)
    foreground = gate.request()
    assert foreground.done()
    assert not waiting.done()
    sweep.finish(first)
    assert waiting.done()
    sweep.finish(waiting)
    gate.release()
    assert gate.active == sweep.active == 0
