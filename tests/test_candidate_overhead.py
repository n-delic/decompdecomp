"""Candidate optimizations must retain input invalidation, durable winners and final progress."""
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

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


def test_bookkeeping_groups_misses_and_a_policy_read_sees_them_without_a_flush(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net);experiment_evidence.prepare(net)
    clock = [0.];monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: clock[0])
    calls = counted_transactions(net.store)
    with trial_bookkeeping.scope():
        for _ in range(10):adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 40)
        assert not calls
        adaptive_repairs.factors(net, 'profile', 'focus')
        assert not calls  # the read adds the queued counts to the stored rows: no transaction
        assert adaptive_repairs.policy_rows(net, 'profile', 'focus')[0]['attempted'] == 10
        assert not net.store.one("SELECT attempted FROM repair_policy")  # (still queued)
    assert len(calls) == 1
    assert net.store.one("SELECT attempted FROM repair_policy")['attempted'] == 10


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
        # Each family and origin has a distinct counter. Repeating a key would merge its statements.
        limit = trial_bookkeeping.Batch().max_statements
        for index in range(limit // 2):
            adaptive_repairs.record(net, 'profile', 'focus', f'family-{index}', 40, None, origin=f'origin-{index}')
        assert len(calls) == 1  # as many distinct counter updates as a batch holds
        clock[0] = trial_bookkeeping.Batch().seconds + .1
        adaptive_repairs.record(net, 'profile', 'focus', 'family-0', 40, None)
        assert len(calls) == 2
    assert net.store.one("SELECT sum(attempted) AS attempted FROM repair_policy")['attempted'] == limit // 2 + 1


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
        size = trial_bookkeeping.current().max_bytes
        trial_bookkeeping.submit(net.store, [('INSERT OR REPLACE INTO settings VALUES(?,?)', ('large', 'x'*size))])
        assert not trial_bookkeeping.current().pending and trial_bookkeeping.current().bytes == 0
        assert len(net.store.one("SELECT value FROM settings WHERE key='large'")['value']) == size


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

    clock = [10.]
    monkeypatch.setattr(deep_permuter, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    net = FakeNet();fn = net.function();proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    first = search._recent_key(proj, fn, 'int f(){return 9;}', None)
    monkeypatch.setattr(claim_stalls, 'repair_rules', lambda: ['new rules'])
    clock[0] += 1
    assert search._recent_key(proj, fn, 'int f(){return 9;}', None) != first
    second = search._recent_key(proj, fn, 'int f(){return 9;}', None)
    monkeypatch.setattr(match_bundle, 'compiler_identity', lambda *a: 'replacement compiler')
    clock[0] += 1
    assert search._recent_key(proj, fn, 'int f(){return 9;}', None) != second


def test_delayed_statistics_do_not_move_collection_windows_backwards(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net)
    clock = [20.];monkeypatch.setattr(adaptive_repairs.time, 'time', lambda: clock[0])
    with trial_bookkeeping.scope():
        adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 40)
        clock[0] = 30.
        thread = threading.Thread(target=lambda: adaptive_repairs.record(net, 'profile', 'focus', 'family', 40, 40))
        thread.start();thread.join(3)
        assert not thread.is_alive()
    row = net.store.one('SELECT first_at,last_at,attempted FROM repair_policy_coverage')
    assert (row['first_at'], row['last_at'], row['attempted']) == (20., 30., 2)


def test_priming_shares_advisory_fingerprints_but_judging_recaptures_them(monkeypatch):
    from decompnet import claim_stalls, match_bundle

    clock = [10.]
    monkeypatch.setattr(deep_permuter, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    net = FakeNet();proj = net.project_row('game');fn = net.function()
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    calls = {'rules':0,'compiler':0};version = [1]
    def rules():calls['rules'] += 1;return [('rules',str(version[0]))]
    def identity(*args):calls['compiler'] += 1;return ('compiler',version[0])
    monkeypatch.setattr(claim_stalls, 'repair_rules', rules)
    monkeypatch.setattr(match_bundle, 'compiler_identity', identity)
    sources = [f'int f(void){{return {i};}}' for i in range(16)]
    search._prime_trials(AGENT, proj, fn, sources, None)
    assert calls == {'rules':1,'compiler':1}
    clock[0] += 1
    prepared = search._recent_key(proj, fn, sources[0], None)
    assert prepared in search._primed_scopes
    version[0] = 2
    clock[0] += 1
    fresh = search._recent_key(proj, fn, sources[0], None)
    assert fresh != prepared and fresh not in search._primed_scopes
    assert calls == {'rules':3,'compiler':3}


def test_advisory_preparation_is_thread_owned_and_restores_outer_trial_on_cancel():
    with candidate_work.scope():
        outer = candidate_work.current()
        with pytest.raises(RuntimeError), candidate_work.preparation() as prepared:
            assert prepared.preparing and prepared is not outer
            copied = __import__('contextvars').copy_context();seen = []
            thread = threading.Thread(target=lambda:copied.run(lambda:seen.append(candidate_work.current())))
            thread.start();thread.join(3)
            assert seen == [None]
            raise RuntimeError('cancelled')
        assert candidate_work.current() is outer and not outer.preparing
    assert candidate_work.current() is None


def test_batched_history_scopes_equal_fresh_scopes_for_each_candidate():
    from decompnet.app.agent_tokens import target_scope
    net = FakeNet();proj = net.project_row('game');fn = net.function()
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    sources = ['int f(void){return 1;}', 'int f(void){return helper();}', 'int f(void){return other();}']
    expected = {search._recent_key(proj,fn,source,None):target_scope(net,dict(fn,best_source=source),'') for source in sources}
    search._prime_trials(AGENT,proj,fn,sources,None)
    assert search._primed_scopes == expected
    assert candidate_work.current() is None


def test_judging_scope_never_inherits_advisory_preparation_memo():
    with candidate_work.preparation() as batch:
        candidate_work.remember('compiler', lambda:'prepared')
        with candidate_work.scope():
            assert not candidate_work.current().preparing
            assert candidate_work.remember('compiler', lambda:'fresh') == 'fresh'
        assert candidate_work.current() is batch
        assert candidate_work.remember('compiler', lambda:'wrong') == 'prepared'
    assert candidate_work.current() is None


def test_background_threads_reuse_the_repair_rule_stamp_for_a_few_seconds(monkeypatch):
    """A thread with no session or snapshot used to list and stamp ~120 files on every claim."""
    from decompnet import claim_stalls
    calls = []
    clock = [100.0]
    monkeypatch.setattr(claim_stalls, '_recent', None)
    monkeypatch.setattr(claim_stalls.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(claim_stalls, '_repair_rules', lambda: calls.append(1) or [('rule', str(len(calls)))])
    first = claim_stalls.repair_rules()
    assert claim_stalls.repair_rules() == first and len(calls) == 1
    clock[0] += claim_stalls._RECENT_SECONDS + 1  # an edit shows up once the stamp is a few seconds old
    assert claim_stalls.repair_rules() != first and len(calls) == 2


def saved_history(net, fn):
    import json
    row = net.store.one("SELECT value FROM settings WHERE key=?", 'agent-output-history:' + str(fn['id']))
    return json.loads(row['value']) if row else None


def test_a_search_batches_its_output_history_and_saves_the_same_list(monkeypatch):
    from decompnet.app import search_evidence
    search_evidence._HISTORY.clear()
    net = FakeNet();fn = net.function();calls = counted_transactions(net.store)
    entry = lambda n: {'output': f'out-{n % 3}', 'family': 'fam'}
    with trial_bookkeeping.scope():
        for n in range(40):search_evidence._remember_fingerprint(net, fn, 'scope-a', f'digest-{n}', entry(n))
        assert not calls and saved_history(net, fn) is None  # nothing written one candidate at a time
    assert len(calls) == 1  # one transaction at the end of the search
    saved = saved_history(net, fn)
    assert saved['scope'] == 'scope-a' and [r[0] for r in saved['history']] == [f'digest-{n}' for n in range(16, 40)]
    # the same list the unbatched path writes
    search_evidence._HISTORY.clear()
    other = FakeNet();other_fn = other.function()
    for n in range(40):search_evidence._remember_fingerprint(other, other_fn, 'scope-a', f'digest-{n}', entry(n))
    assert saved_history(other, other_fn) == saved


def test_the_batched_history_starts_from_the_saved_one_and_a_new_scope_starts_over(monkeypatch):
    from decompnet.app import search_evidence
    search_evidence._HISTORY.clear()
    net = FakeNet();fn = net.function()
    search_evidence._remember_fingerprint(net, fn, 'scope-a', 'old', {'output': 'o', 'family': 'f'})
    search_evidence._HISTORY.clear()  # (another process, or a restart)
    with trial_bookkeeping.scope():
        search_evidence._remember_fingerprint(net, fn, 'scope-a', 'new', {'output': 'o2', 'family': 'f'})
        search_evidence._remember_fingerprint(net, fn, 'scope-a', 'old', {'output': 'o3', 'family': 'f'})
    assert [r[0] for r in saved_history(net, fn)['history']] == ['new', 'old'] and saved_history(net, fn)['history'][1][1] == 'o3'
    with trial_bookkeeping.scope():
        search_evidence._remember_fingerprint(net, fn, 'scope-b', 'other', {'output': 'o', 'family': 'f'})
    assert saved_history(net, fn) == {'scope': 'scope-b', 'history': [['other', 'o', 'f']]}


def test_policy_reads_see_the_batch_without_flushing_it(monkeypatch):
    import random
    net = FakeNet();adaptive_repairs.prepare(net);experiment_evidence.prepare(net)
    monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: 0.)
    calls = counted_transactions(net.store)
    random.seed(5)
    families = [f'family-{i}' for i in range(6)]
    with trial_bookkeeping.scope():
        for step in range(300):
            family = random.choice(families)
            score = random.choice([None, 10, 40, 40, 40, 60])
            adaptive_repairs.record(net, 'profile', 'focus', family, 40, score, random.randint(1, 9), origin='deep permuter')
            adaptive_repairs.record(net, 'other', 'focus', family, 40, 40)  # (another profile: not part of the answer)
            if step % 25 == 24:
                before = len(calls)
                seen = adaptive_repairs.factors(net, 'profile', 'focus')
                assert len(calls) == before  # reading the policy is not a transaction
                rows = adaptive_repairs.policy_rows(net, 'profile', 'focus')
                trial_bookkeeping.flush(net.store)
                assert rows == [dict(r) for r in net.store.all(
                    "SELECT * FROM repair_policy WHERE profile='profile' AND focus='focus' ORDER BY family")]
                assert adaptive_repairs.factors(net, 'profile', 'focus') == seen


def test_input_effect_reads_see_the_batch_without_flushing_it(monkeypatch):
    import random
    net = FakeNet();experiment_evidence.prepare(net);fn = net.function()
    monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: 0.)
    store = experiment_evidence.prepare(net, fn['project_id']);calls = counted_transactions(store)
    random.seed(9)
    with trial_bookkeeping.scope():
        for step in range(200):
            family = random.choice(['a', 'b', 'c', 'd'])
            experiment_evidence.record(net, fn, 'scope', family, 'int f(){return 1;}', random.choice(['int f(){return 1;}', 'int f(){return 2;}']),
                                       40, random.choice([None, 40, 40, 60]), 'out', random.choice(['out', 'other']), random.randint(1, 9), compact=True)
            if step % 20 == 19:
                before = len(calls)
                rows = experiment_evidence.effect_rows(store, fn['id'], 'scope')
                assert len(calls) == before
                trial_bookkeeping.flush(store)
                stored = [dict(r) for r in store.all("SELECT * FROM repair_input_effects WHERE function_id=? AND scope='scope' ORDER BY family", fn['id'])]
                columns = experiment_evidence.EFFECT_COLUMNS
                assert [{k: r[k] for k in ('family', *columns)} for r in rows] == [{k: r[k] for k in ('family', *columns)} for r in stored]


def test_fork_slug_is_asked_of_the_database_once_a_while_not_for_every_candidate(monkeypatch):
    from decompnet.app.engine import core
    asked = []
    def project(self, slug):
        asked.append(slug)
        if slug == 'gone':
            raise core.NetworkError('no such project')
        return {'slug': slug, 'kind': 'fork' if slug == 'forked' else 'rom'}
    monkeypatch.setattr(core.Network, '_project', project)
    clock = [100.];monkeypatch.setattr(core.time, 'monotonic', lambda: clock[0])
    net = SimpleNamespace(cfg=lambda proj: {'kind': proj['kind']})
    fork_slug = lambda name: core.Core.fork_slug(net, name)
    assert [fork_slug('forked') for _ in range(50)] == ['forked'] * 50 and [fork_slug('game') for _ in range(50)] == [None] * 50
    assert asked == ['forked', 'game']
    assert fork_slug('gone') is None and fork_slug('gone') is None and asked.count('gone') == 2  # (a missing project is not remembered)
    clock[0] += core.FORK_SLUG_SECONDS + 1
    assert fork_slug('forked') == 'forked' and asked.count('forked') == 2


def test_stored_policy_rows_are_read_once_a_second_and_again_after_a_flush(monkeypatch):
    net = FakeNet();adaptive_repairs.prepare(net)
    clock = [0.];monkeypatch.setattr(trial_bookkeeping.time, 'monotonic', lambda: clock[0])
    reads = []
    real = net.store.all
    net.store.all = lambda sql, *args: reads.append(sql) or real(sql, *args)
    with trial_bookkeeping.scope():
        for _ in range(5):adaptive_repairs.record(net, 'p', 'f', 'family', 40, 40)
        for _ in range(20):assert adaptive_repairs.policy_rows(net, 'p', 'f')[0]['attempted'] == 5
        assert len([r for r in reads if 'repair_policy' in r]) == 1
        trial_bookkeeping.flush(net.store)  # the counts move from the queue to the table: the copy must not hide them
        assert adaptive_repairs.policy_rows(net, 'p', 'f')[0]['attempted'] == 5
        assert len([r for r in reads if 'repair_policy' in r]) == 2
        adaptive_repairs.record(net, 'p', 'f', 'family', 40, 40)
        assert adaptive_repairs.policy_rows(net, 'p', 'f')[0]['attempted'] == 6
        clock[0] += trial_bookkeeping.STORED_SECONDS + .1
        adaptive_repairs.policy_rows(net, 'p', 'f')
        assert len([r for r in reads if 'repair_policy' in r]) == 3
