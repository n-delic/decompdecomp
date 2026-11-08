"""Compile-ahead is bounded, cancellable and never owns judging or publication."""
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from types import MethodType

import pytest

from decompnet import compile_sessions, compile_sharing, compiler
from decompnet.app import deep_compilation, deep_permuter, experiment_evidence, permute, repair_ranking
from decompnet.network import Network
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT, FakeNet


def setup(monkeypatch, limit=20):
    monkeypatch.setattr(compiler, 'concurrency_limit', lambda:4)
    net = FakeNet();net._compile_ahead_local = threading.local()
    net.compiled_ahead = MethodType(Network.compiled_ahead, net)
    net._attempt_compilation = MethodType(Network._attempt_compilation, net)
    net._compile_snapshot = Network._compile_snapshot
    net.keep_diff = lambda *args:None
    search = deep_permuter.Search(net, 1, limit, Log(), net.function())
    search.compile_pool = deep_compilation.Pool()
    return net, search


def test_independent_compiles_overlap_while_judging_stays_ordered(monkeypatch):
    net, search = setup(monkeypatch, limit=8)
    barrier = threading.Barrier(4);calls = [];judged = []
    def compile_(proj, header, source, fn):
        calls.append((source, threading.current_thread().name))
        barrier.wait(5)
        return compiler.CompileResult(True, source.encode(), '')
    net._compile = compile_
    def score(self, agent, proj, fn, source, best, fork):
        result = net._attempt_compilation(proj, net._context(1,'')['header'], source, fn)
        assert result.obj == source.encode()
        judged.append(source)
        return 40
    monkeypatch.setattr(permute.Permuter, '_score_work', score)
    sources = ['int f(){return '+str(i)+';}' for i in range(8)]
    def chunks():yield sources
    try:
        for source in search._stream_variants(chunks(), AGENT, net.project_row('game'), net.function(), None):
            assert search._score(AGENT, net.project_row('game'), net.function(), source, 40, None) == 40
        assert judged == sources
        assert len(calls) == 8 and all(name.startswith('deep-compile') for _,name in calls)
        assert search.tested == 8
    finally:search.compile_pool.close()
    assert all(not t.is_alive() for t in search.compile_pool.workers)


def test_discarded_running_compiles_consume_allowance_and_cancel(monkeypatch):
    net, search = setup(monkeypatch, limit=3)
    started = threading.Barrier(4);ended = []
    def compile_(*args):
        started.wait(5)
        try:
            while True:
                compiler._CANCEL_CHECK.get()()
                time.sleep(.01)
        finally:ended.append(1)
    net._compile = compile_
    sources = ['int f(){return '+str(i)+';}' for i in range(8)]
    try:
        with deep_compilation.chunk(search, AGENT, net.project_row('game'), net.function(), sources, None):
            started.wait(5)
            assert len(search._ahead) == 3
            deep_compilation.discard(search)
            assert search.tested == 3 and not search._ahead
        assert search.tested == 3
    finally:search.compile_pool.close()
    assert len(ended) == 3


def test_changed_inputs_never_use_queued_object(monkeypatch):
    net, search = setup(monkeypatch, limit=1)
    net._compile = lambda *a:compiler.CompileResult(True,b'old object','')
    source = 'int f(){return 7;}'
    try:
        with deep_compilation.chunk(search, AGENT, net.project_row('game'), net.function(), [source], None):
            search._ahead_current = search._ahead[source]
            with net.store.tx() as db:db.execute("UPDATE projects SET config=json_set(config,'$.compiler.flags','-O1')")
            with pytest.raises(deep_permuter.SearchLimit, match='Inputs changed'):
                search._score_work(AGENT, net.project_row('game'), net.function(), source, 40, None)
    finally:search.compile_pool.close()


def test_imported_and_multi_profile_searches_do_not_speculate(monkeypatch):
    net, search = setup(monkeypatch)
    net._compile = lambda *a:pytest.fail('unexpected speculative build')
    try:
        with deep_compilation.chunk(search, AGENT, net.project_row('game'), net.function(), ['x'], 'imported'):pass
        search.compiler_profiles = [1,2]
        with deep_compilation.chunk(search, AGENT, net.project_row('game'), net.function(), ['x'], None):pass
        assert not search.compile_pool.workers
    finally:search.compile_pool.close()


def test_shared_completed_objects_are_bounded_independent_and_not_failures():
    group = compile_sharing.Group();calls = []
    def build():
        calls.append(1);return compiler.CompileResult(True,b'object','')
    first = group.run('key', build, None);first.obj = b'caller changed'
    assert group.run('key', build, None).obj == b'object' and len(calls) == 1
    for i in range(200):group.run(i, build, None)
    assert len(group.completed) <= 128 and group.bytes <= 32*1024*1024
    failures = []
    def failed():failures.append(1);return compiler.CompileResult(False,None,'retryable')
    group.run('failed', failed, None);group.run('failed', failed, None)
    assert len(failures) == 2


def test_shared_identical_inflight_build_runs_once_and_follower_can_cancel():
    group = compile_sharing.Group();started = threading.Event();release = threading.Event();calls = []
    def build():
        calls.append(1);started.set();assert release.wait(5)
        return compiler.CompileResult(True,b'object','')
    def canceled():raise CancelledError()
    with ThreadPoolExecutor(3) as pool:
        owner = pool.submit(group.run, 'key', build, None)
        assert started.wait(5)
        follower = pool.submit(group.run, 'key', build, None)
        with pytest.raises(CancelledError):group.run('key', build, canceled)
        release.set()
        assert owner.result().obj == follower.result().obj == b'object'
    assert calls == [1]


def test_canceled_owner_does_not_poison_other_lane():
    group = compile_sharing.Group();started = threading.Event();release = threading.Event()
    def canceled_owner():
        started.set();assert release.wait(5);raise CancelledError()
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(group.run, 'key', canceled_owner, None)
        assert started.wait(5)
        follower = pool.submit(group.run, 'key', lambda:compiler.CompileResult(True,b'retry',''), None)
        release.set()
        with pytest.raises(CancelledError):owner.result()
        assert follower.result().obj == b'retry'


def test_baseline_evidence_shared_once_and_invalidated(monkeypatch):
    net, first = setup(monkeypatch)
    second = deep_permuter.Search(net,1,20,Log(),net.function());second.compile_pool = first.compile_pool
    calls = []
    def build(search):
        calls.append(1);search.starting_score=search.best=40;search.baseline_measured=True
        search.winner_source=net.function()['best_source'];search.winner_diff=([('r','target','candidate')],'text')
        return 40,1
    args = (AGENT,net.project_row('game'),net.function(),None,None)
    try:
        assert deep_compilation.baseline(first,*args,lambda:build(first)) == (40,1)
        assert deep_compilation.baseline(second,*args,lambda:build(second)) == (40,0)
        assert len(calls)==1 and second.baseline_measured and second.best==40
        second.winner_diff[0].clear()
        assert first.winner_diff[0]
        with net.store.tx() as db:db.execute("UPDATE projects SET config=json_set(config,'$.compiler.flags','-O1')")
        deep_compilation.baseline(second,AGENT,net.project_row('game'),net.function(),None,None,lambda:build(second))
        assert len(calls)==2
    finally:first.compile_pool.close()


def test_rank_diagnosis_is_only_computed_once(monkeypatch):
    from decompnet.app import allocation_evidence
    original = allocation_evidence.diagnose;calls = []
    def diagnose(rows):calls.append(1);return original(rows)
    monkeypatch.setattr(allocation_evidence,'diagnose',diagnose)
    repair_ranking.order(['signedness','local_storage'],[('r','move $v0, $a0','move $v0, $a1')])
    assert len(calls)==1


def test_compact_random_effects_skip_line_diff_but_retain_counts(monkeypatch):
    net = FakeNet()
    monkeypatch.setattr(experiment_evidence,'edits',lambda *a:pytest.fail('expensive source diff'))
    experiment_evidence.record(net,net.function(),'scope','family','before','after',40,50,'same','same',1,compact=True)
    row = net.store.one('SELECT * FROM repair_input_effects')
    assert row['attempted']==1 and row['same_output']==1 and row['worse']==1 and row['no_edit']==0


def test_group_keys_still_come_from_full_compiler_input_capture(monkeypatch, tmp_path):
    calls = []
    def build(cfg,work,root,source,ext):
        calls.append(source);return compiler.CompileResult(True,source.encode(),'')
    monkeypatch.setattr(compiler,'_compile_as',build)
    cfg = compiler.CompilerConfig(['synthetic-cc'],sandbox='none',identity=('search',))
    group = compile_sharing.Group()
    for header in ('typedef int T;','typedef int T;','typedef long T;'):
        with compile_sessions.session(), compile_sharing.group(group), compiler.cancellable(lambda:None):
            assert compiler.compile_source(cfg,header,'T f(){return 1;}').ok
    assert len(calls)==2


@pytest.mark.parametrize('score,exact,baseline,compact', [(80,False,False,True),(10,False,False,False),(80,True,False,False),(80,False,True,False)])
def test_only_ordinary_random_misses_defer_detailed_diagnostics(monkeypatch, score, exact, baseline, compact):
    from decompnet import scoring
    from decompnet.elfobj import FunctionObj
    from decompnet.repair_feedback import trial
    net = FakeNet();fn=net.function();proj=net.project_row('game')
    target=FunctionObj('f','mips',True,bytes.fromhex('03e0000800000000'))
    net._compile=lambda *a:compiler.CompileResult(True,b'object','')
    evaluated=[];locations=[];physical=[]
    net._evaluate=lambda *a:evaluated.append(1) or (target,exact,[])
    net._target_obj=lambda *a:target
    net.keep_diff=lambda *a:None
    net._record=lambda *a,**kw:None
    net._search_rows=lambda *a:physical.append(1) or a[-1]
    monkeypatch.setattr(scoring,'diff',lambda *a:scoring.DiffResult(score,100,[],False))
    monkeypatch.setattr('decompnet.source_lines.mismatches',lambda *a,**kw:locations.append(1) or {'available':False})
    search=permute.Permuter(net)
    source=fn['best_source'] if baseline else 'int f(){return 123;}'
    with trial({'kind':'random rewrite','family':'random'}):search._score_work(AGENT,proj,fn,source,40,None)
    assert search._compact_trial is compact
    assert bool(locations) is not compact and bool(physical) is not compact
    assert len(evaluated) == (1 if compact else 2)


def test_admission_is_bounded_across_lanes_without_partial_window_deadlock(monkeypatch):
    monkeypatch.setattr(compiler,'concurrency_limit',lambda:2)
    pool=deep_compilation.Pool();lock=threading.Lock();active=[0];maximum=[0]
    def lane():
        with pool.window(4,lambda:None):
            with lock:
                active[0]+=4;maximum[0]=max(maximum[0],active[0])
            time.sleep(.01)
            with lock:active[0]-=4
    try:
        with ThreadPoolExecutor(8) as lanes:
            futures=[lanes.submit(lane) for _ in range(8)]
            for future in futures:future.result(timeout=5)
        assert maximum[0]==4 and active[0]==0
    finally:pool.close()


def test_queued_deadline_reports_normal_search_limit(monkeypatch):
    net, search=setup(monkeypatch,limit=1)
    search.deadline=time.monotonic()-1
    net._compile=lambda *a:pytest.fail('expired job')
    try:
        with pytest.raises(deep_permuter.SearchLimit,match='Time limit'):
            with deep_compilation.chunk(search,AGENT,net.project_row('game'),net.function(),['int f(){return 3;}'],None):
                search._ahead['int f(){return 3;}'].future.result(timeout=5)
    finally:search.compile_pool.close()


def test_unrelated_input_revision_does_not_discard_valid_compilation(monkeypatch):
    net, search=setup(monkeypatch,limit=1)
    net._compile=lambda *a:compiler.CompileResult(True,b'queued object','')
    # Use the ordinary handoff, but isolate it from link diagnostics for this guard test.
    def score(self,agent,proj,fn,source,best,fork):
        return net._attempt_compilation(proj,net._context(1,'')['header'],source,fn).obj
    monkeypatch.setattr(permute.Permuter,'_score_work',score)
    source='int f(){return 3;}'
    try:
        with deep_compilation.chunk(search,AGENT,net.project_row('game'),net.function(),[source],None):
            search._ahead_current=search._ahead[source]
            with net.store.tx() as db:
                db.execute("INSERT INTO functions(project_id,name,best_source,best_score) VALUES(1,'other','int other(){return 2;}',10)")
            assert search._score_work(AGENT,net.project_row('game'),net.function(),source,40,None)==b'queued object'
    finally:search.compile_pool.close()


def test_ticket_invocations_preserve_pool_totals_and_are_not_candidate_counts(monkeypatch):
    from decompnet import compile_timing
    monkeypatch.setattr(compiler,'concurrency_limit',lambda:2)
    pool=deep_compilation.Pool()
    def run():
        compile_timing.add('invocations',2)
        return 'compiled'
    ticket=deep_compilation.Ticket('key',None,run,lambda:None)
    try:
        pool.submit(ticket)
        assert ticket.future.result(timeout=5)=='compiled'
        assert ticket.compile_counts.values['invocations']==2
        assert pool.metrics.snapshot()['pool_compiler_invocations']==2
    finally:pool.close()


def test_consumed_ticket_plus_lane_retry_invocations_are_both_measured(monkeypatch):
    from decompnet import compile_timing
    net,search=setup(monkeypatch,limit=2)
    def compile_(proj,header,source,fn):
        compile_timing.add('invocations',3)
        return compiler.CompileResult(True,source.encode(),'')
    net._compile=compile_
    def score(self,agent,proj,fn,source,best,fork):
        net._attempt_compilation(proj,net._context(1,'')['header'],source,fn)
        compile_timing.add('invocations',2)  # A synchronous retry after consuming compile-ahead.
        return 0
    monkeypatch.setattr(permute.Permuter,'_score_work',score)
    try:
        for source in search._stream_variants((chunk for chunk in [['int f(){return 7;}']]),AGENT,net.project_row('game'),net.function(),None):
            search._score(AGENT,net.project_row('game'),net.function(),source,40,None)
        counts=search.efficiency.snapshot()['families']
        assert sum(c.get('compiler_invocations',0) for c in counts.values())==5
        assert search.compile_pool.metrics.snapshot()['pool_compiler_invocations']==3
    finally:search.compile_pool.close()
