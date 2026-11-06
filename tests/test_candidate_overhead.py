"""Candidate optimizations must retain input invalidation, durable winners and final progress."""
import threading
from contextlib import contextmanager

import pytest

from decompnet import candidate_scope, candidate_work, elfobj
from decompnet.app import adaptive_repairs, deep_permuter, experiment_evidence, trial_bookkeeping
from tests.test_candidate_diagnostics import object_file
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT, FakeNet


def test_scopes_reuse_only_within_trial_and_invalidate_each_input_change(monkeypatch):
    net = FakeNet();proj = net.project_row('game');fn = net.function()
    original = candidate_scope.state;calls = []
    def state(*args, **kwargs):
        calls.append(1);return original(*args, **kwargs)
    monkeypatch.setattr(candidate_scope, 'state', state)
    with candidate_work.scope():
        before = candidate_scope.key(net, proj, fn)
        assert candidate_scope.key(net, proj, fn) == before and len(calls) == 1
        with net.store.tx() as db:
            db.execute("INSERT INTO context_versions(project_id,version,unit,header) VALUES(1,1,'','changed')")
        candidate_scope.key(net, proj, fn)
        assert len(calls) == 2
        candidate_scope.key(net, proj, {**dict(fn), 'best_source':'changed C'})
        assert len(calls) == 3
    candidate_scope.key(net, proj, fn)
    assert len(calls) == 4


def test_imported_files_are_never_scope_memoized(monkeypatch):
    net = FakeNet();net.fork_slug = lambda _: 'imported'
    proj = net.project_row('game');fn = net.function();calls = []
    monkeypatch.setattr(candidate_scope, 'state', lambda *a: calls.append(1) or [len(calls)])
    with candidate_work.scope():
        assert candidate_scope.key(net, proj, fn) != candidate_scope.key(net, proj, fn)
    assert len(calls) == 2


def test_object_and_function_symbol_parsing_is_shared_but_public_maps_are_independent(monkeypatch):
    from elftools.elf.elffile import ELFFile
    calls = []
    def factory(stream):
        calls.append(1);return ELFFile(stream)
    obj = object_file()
    with candidate_work.scope():
        first = candidate_work.elf(obj, factory)
        assert candidate_work.elf(obj, factory) is first and len(calls) == 1
        symbols = elfobj._function_symbols(first)
        assert 'f' in symbols
        symbols.clear()
        assert 'f' in elfobj._function_symbols(first)
        assert candidate_work.elf(obj + b'changed', factory) is not first
    assert candidate_work.elf(obj, factory) is not first and len(calls) == 3


def test_candidate_scope_cannot_leak_to_another_thread():
    from contextvars import copy_context
    with candidate_work.scope():
        captured = copy_context();seen = []
        thread = threading.Thread(target=lambda: captured.run(lambda: seen.append(candidate_work.current())))
        thread.start();thread.join(3)
        assert seen == [None]
    assert candidate_work.current() is None


def counted_transactions(store):
    original = store.tx;calls = []
    @contextmanager
    def tx(*args, **kwargs):
        calls.append(1)
        with original(*args, **kwargs) as db:yield db
    store.tx = tx
    return calls


def test_bookkeeping_groups_misses_and_flushes_before_a_policy_read(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net);experiment_evidence.prepare(net)
    clock = [0.];monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: clock[0])
    calls = counted_transactions(net.store)
    with trial_bookkeeping.scope():
        for _ in range(10):adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 40)
        assert not calls
        adaptive_repairs.factors(net, 'profile', 'focus')
        assert len(calls) == 1
        assert net.store.one("SELECT attempted FROM repair_policy")['attempted'] == 10
    assert len(calls) == 1


def test_wins_and_matches_are_immediate_and_cancellation_flushes_misses(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net)
    monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: 0.)
    with pytest.raises(RuntimeError, match='cancelled'), trial_bookkeeping.scope():
        adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 40)
        adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 0)
        row = net.store.one('SELECT attempted,matched FROM repair_policy')
        assert (row['attempted'], row['matched']) == (2, 1)
        adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 50)
        raise RuntimeError('cancelled')
    assert net.store.one('SELECT attempted FROM repair_policy')['attempted'] == 3
    assert trial_bookkeeping.current() is None


def test_bookkeeping_flushes_at_size_and_time_boundaries(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net)
    clock = [0.];monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: clock[0])
    calls = counted_transactions(net.store)
    with trial_bookkeeping.scope():
        for _ in range(32):adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, None)
        assert len(calls) == 1  # 64 queued statements
        clock[0] = .6
        adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, None)
        assert len(calls) == 2
    assert net.store.one('SELECT attempted FROM repair_policy')['attempted'] == 33


def test_progress_is_throttled_but_winners_phase_changes_and_final_are_immediate(monkeypatch):
    net = FakeNet();log = Log();search = deep_permuter.Search(net, 1, 100, log, net.function())
    clock = [10.];monkeypatch.setattr(deep_permuter.time, 'monotonic', lambda: clock[0])
    search.report()
    for _ in range(20):search.tested += 1;search.report()
    assert len(log.updates) == 1
    clock[0] += .21;search.report()
    assert len(log.updates) == 2
    search.best = 30;search.report()
    search.phase = 'next phase';search.report()
    search.report(force=True)
    assert len(log.updates) == 5 and log.updates[-1]['tested'] == 20


def test_recent_duplicates_skip_scope_and_database_miss_lookup(monkeypatch):
    from decompnet.app.permute import Permuter
    net = FakeNet();fn = net.function();proj = net.project_row('game');log = Log()
    search = deep_permuter.Search(net, 1, 100, log, fn)
    monkeypatch.setattr(Permuter, '_score', lambda *a: 40)
    calls = []
    original = deep_permuter.agent_tokens.target_scope
    def scope(*a):calls.append(1);return original(*a)
    monkeypatch.setattr(deep_permuter.agent_tokens, 'target_scope', scope)
    with trial_bookkeeping.scope():
        assert search._score_current(AGENT, proj, fn, 'int f(){return 9;}', 40, None) == 40
        assert search._score_current(AGENT, proj, fn, 'int f(){return 9;}', 40, None) == 40
        assert len(calls) == 1 and search.tested == 1 and search.skipped == 1
        with net.store.tx() as db:
            db.execute("UPDATE projects SET config=json_set(config,'$.gp',32) WHERE id=1")
        search._score_current(AGENT, net.project_row('game'), fn, 'int f(){return 9;}', 40, None)
        assert len(calls) == 2


def test_recent_duplicates_do_not_reuse_winners_baselines_or_external_includes(monkeypatch):
    from decompnet.app.permute import Permuter
    net = FakeNet();fn = net.function();proj = net.project_row('game');log = Log()
    search = deep_permuter.Search(net, 1, 100, log, fn)
    assert search._recent_key(proj, fn, '#include "/external.h"', None) is None
    assert search._recent_key(proj, fn, 'source', 'imported') is None
    search.measuring_baseline = True
    assert search._recent_key(proj, fn, 'source', None) is None
    search.measuring_baseline = False
    monkeypatch.setattr(Permuter, '_score', lambda *a: 10)
    for _ in range(2):search._score_current(AGENT, proj, fn, 'int f(){return 10;}', 40, None)
    assert search.tested == 2


def test_buffered_misses_still_skip_duplicate_imported_candidates(monkeypatch):
    from decompnet.app.permute import Permuter
    net = FakeNet();fn = net.function();proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    monkeypatch.setattr(Permuter, '_score', lambda *a: 40)
    monkeypatch.setattr(deep_permuter.agent_tokens, 'target_scope', lambda *a: 'external-files-current')
    with trial_bookkeeping.scope():
        for _ in range(2):search._score_current(AGENT, proj, fn, 'source', 40, 'imported')
        assert search.tested == 1 and search.skipped == 1
    assert net.store.one('SELECT COUNT(*) n FROM reuse_trials')['n'] == 1


def test_large_bookkeeping_payload_flushes_without_an_unbounded_queue(monkeypatch):
    net = FakeNet();monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: 0.)
    with trial_bookkeeping.scope():
        trial_bookkeeping.submit(net.store, [('INSERT OR REPLACE INTO settings VALUES(?,?)', ('large', 'x'*(1024*1024)))])
        assert not trial_bookkeeping.current().pending and trial_bookkeeping.current().bytes == 0
        assert len(net.store.one("SELECT value FROM settings WHERE key='large'")['value']) == 1024*1024


def test_failed_batch_rolls_back_without_masking_the_original_failure(monkeypatch):
    net = FakeNet();monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: 0.)
    with pytest.raises(RuntimeError, match='original failure'), trial_bookkeeping.scope():
        trial_bookkeeping.submit(net.store, [
            ('INSERT OR REPLACE INTO settings VALUES(?,?)', ('pending-stat', 'value')),
            ('INSERT INTO nonexistent_stat_table VALUES(?)', (1,))])
        raise RuntimeError('original failure')
    assert net.store.one("SELECT value FROM settings WHERE key='pending-stat'") is None
    assert trial_bookkeeping.current() is None


def test_buffer_owner_cannot_be_reused_by_a_context_copied_to_another_thread():
    from contextvars import copy_context
    with trial_bookkeeping.scope():
        context = copy_context();seen = []
        thread = threading.Thread(target=lambda: context.run(lambda: seen.append(trial_bookkeeping.current())))
        thread.start();thread.join(3)
        assert seen == [None]


def test_duplicate_guard_includes_repair_rules_and_compiler_identity(monkeypatch):
    from decompnet import claim_stalls, match_bundle
    net = FakeNet();fn = net.function();proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    first = search._recent_key(proj, fn, 'int f(){return 9;}', None)
    monkeypatch.setattr(claim_stalls, 'repair_rules', lambda: ['new rules'])
    assert search._recent_key(proj, fn, 'int f(){return 9;}', None) != first
    second = search._recent_key(proj, fn, 'int f(){return 9;}', None)
    monkeypatch.setattr(match_bundle, 'compiler_identity', lambda *a: 'replacement compiler')
    assert search._recent_key(proj, fn, 'int f(){return 9;}', None) != second
