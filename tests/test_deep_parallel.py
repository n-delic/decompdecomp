"""Per-function workers overlap without multiplying the configured allowance."""
import threading
from types import SimpleNamespace

import pytest

from decompnet.app import deep_parallel, deep_permuter
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup


@pytest.mark.parametrize('imported', [False, True])
def test_paths_overlap_share_budget_deadline_and_aggregate(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    barrier = threading.Barrier(3)
    seen = []
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        seen.append((lane.index, allowance, lane.deadline))
        log.progress({'tested': allowance, 'best_score': 40-lane.index, 'best_percent': 50+lane.index})
        return {'tested': allowance, 'best_score': 40-lane.index, 'skipped': lane.index}
    monkeypatch.setattr(deep_permuter, '_run', path)
    log = Log()
    result = deep_permuter.run(net, 'game', 'f', 1, 11, log, threads_per_function=3)
    assert sorted((i, n) for i,n,_ in seen) == [(0,4), (1,4), (2,3)]
    assert len({d for _,_,d in seen}) == 1
    assert result['tested'] == 11 and result['skipped'] == 3
    assert result['threads_per_function'] == 3
    assert result['reason'] == 'Variant limit reached'
    assert max(u.get('tested', 0) for u in log.updates) == 11
    assert all(u['variant_limit'] == 11 for u in log.updates)


def test_preparation_precedes_other_paths_and_credentials_are_distinct(monkeypatch):
    net = setup(monkeypatch)
    agents = []
    net.local_agent = lambda name: (agents.append(name) or SimpleNamespace(id=len(agents), name=name), None)
    preparation = []
    preflight = []
    net._comparison_digest = lambda *a: ''
    monkeypatch.setattr('decompnet.app.source_preparation.run', lambda *a, **k: preparation.append('done') or {'tested':0, 'improved':0})
    monkeypatch.setattr('decompnet.app.matching_preflight.run', lambda *a, **k: preflight.append('done') or {'tested':0})
    ready = threading.Barrier(2)
    def baseline(self, *args):
        assert preparation == ['done'] and preflight == ['done']
        ready.wait(timeout=5)
        return 40, 0
    monkeypatch.setattr(deep_permuter.Search, '_baseline', baseline)
    monkeypatch.setattr(deep_permuter.Search, 'local_repairs', lambda *a, **k: (40, 'x'))
    def permute(self, agent, proj, fn, state):
        # The random phase must not mutate compiler settings underneath another path.
        self._compiler_preflight(proj, fn, state, self.deadline)
        assert preflight == ['done']
        state.tried = 1
        self.tested = self.limit
    monkeypatch.setattr(deep_permuter.Search, '_permute', permute)
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert len(set(agents)) == 2 and all('-lane-' in a for a in agents)
    assert result['tested'] == 10


def test_exact_match_supersedes_inflight_guard_failure(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    matched = threading.Event()
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        if lane.index:
            assert matched.wait(5)
            raise NetworkError('Search inputs changed while compiling; the function is no longer open.')
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0")
        matched.set()
        return {'tested':1}
    monkeypatch.setattr(deep_permuter, '_run', path)
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert result['matched'] and result['reason'] == 'Matched'
    # Outer duplicate protection is released even when another worker is superseded.
    assert not net._deep_run_locks[net.function()['id']].locked()


def test_failure_cancels_other_paths_and_does_not_record_completion(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    ended = threading.Event()
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        if lane.index == 0:
            raise NetworkError('Compiler unavailable')
        try:
            while True:
                log.check_cancel()
        finally:
            ended.set()
    monkeypatch.setattr(deep_permuter, '_run', path)
    with pytest.raises(NetworkError, match='Compiler unavailable'):
        deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert ended.is_set()


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '2', 257])
def test_invalid_thread_counts(value):
    with pytest.raises(NetworkError, match='threads per function'):
        deep_parallel.validate_threads(value)


def test_cancellation_reaches_all_function_workers(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    log = Log()
    ended = []
    def path(net, slug, name, minutes, allowance, output, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        log.cancelled = True
        try:
            output.check_cancel()
        finally:
            ended.append(lane.index)
    monkeypatch.setattr(deep_permuter, '_run', path)
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 10, log, threads_per_function=2)
    assert sorted(ended) == [0,1]


def test_one_completed_record_uses_whole_function_budget(monkeypatch):
    net = setup(monkeypatch)
    records = []
    net.deep_sweeps = SimpleNamespace(completed=lambda *a: records.append(a), improvement=lambda *a: None)
    monkeypatch.setattr('decompnet.candidate_scope.key', lambda *a: 'scope')
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        return {'tested':allowance}
    monkeypatch.setattr(deep_permuter, '_run', path)
    deep_permuter.run(net, 'game', 'f', 5, 11, Log(), threads_per_function=3)
    assert len(records) == 1
    assert records[0][2:4] == (5,11)
    assert records[0][4]['tested'] == 11


def test_wrapped_deadline_is_not_multiplied_by_worker_count(monkeypatch):
    net = setup(monkeypatch)
    log = Log()
    log.maximum_seconds = 0
    monkeypatch.setattr(deep_permuter, '_run', lambda *a, **k: pytest.fail('expired budget'))
    result = deep_permuter.run(net, 'game', 'f', 1, 10, log, threads_per_function=3)
    assert result['reason'] == 'Time limit reached' and result['tested'] == 0


def test_maximum_mode_uses_detected_cores_without_multiplying_budget(monkeypatch):
    from decompnet import compiler
    from decompnet.app import deep_throughput
    net = setup(monkeypatch)
    monkeypatch.setattr(deep_throughput.os,'cpu_count',lambda:4)
    paths=[]
    def path(net,slug,name,minutes,allowance,log,profiles,*,lane):
        paths.append((lane.index,allowance,log.compile_pool))
        lane.prepared()
        return {'tested':allowance}
    monkeypatch.setattr(deep_permuter,'_run',path)
    initial=compiler._SLOTS.limit
    log=Log()
    result=deep_permuter.run(net,'game','f',1,11,log,maximum_throughput=True)
    assert len(paths)==4 and result['tested']==11
    assert sum(n for _,n,_ in paths)==11
    assert len({id(pool) for _,_,pool in paths})==1
    assert paths[0][2].count==4 and paths[0][2].maximum
    assert compiler._SLOTS.limit==initial and log.compile_pool is None
