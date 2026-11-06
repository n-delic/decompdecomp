"""Batch diagnostics retain source and pauses, cache stable verdicts and release temporary claims."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from decompnet import candidate_quality
from decompnet.app import candidate_rechecks as checks
from tests.test_candidate_recovery import recovery  # noqa: F401
from tests.test_deep_permuter import Log


@pytest.fixture
def batch(recovery, monkeypatch):  # noqa: F811
    net, fn, digest, ident, good, calls = recovery
    tested = []
    monkeypatch.setattr(checks, 'stamp', lambda *args: 'build-v1')
    def measure(net, agent, proj, fn, source):
        tested.append(fn['name'])
        return {'score':20, 'percent':80, 'exact':False, 'error':''}
    monkeypatch.setattr(checks, 'measure', measure)
    return net, fn, digest, ident, tested


def test_batch_is_measurement_only_and_skips_fresh_cached_results(batch):
    net, fn, digest, ident, tested = batch
    before = dict(net._function('caller', 'game'))
    result = checks.run(net, 'game', 1, Log())
    assert result['tested'] == 1 and tested == ['caller']
    assert dict(net._function('caller', 'game')) == before
    assert candidate_quality.pending(net, net._function('caller', 'game'))
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert checks.run(net, 'game', 1, Log())['cached'] == 1
    assert tested == ['caller']
    evidence = checks.evidence(net, 'game', 'caller', digest, ident)
    assert '-int caller(){int counter;' in evidence['diff']
    assert evidence['measurement']['score'] == 20


def test_changed_build_rejudges_and_changed_source_hides_old_verdict(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    checks.run(net, 'game', 1, Log())
    monkeypatch.setattr(checks, 'stamp', lambda *args: 'build-v2')
    assert checks.listing(net, 'game')[0]['rechecks'][0]['measurement'] is None
    checks.run(net, 'game', 1, Log())
    assert tested == ['caller', 'caller']
    with net.store.tx() as db: db.execute("UPDATE functions SET best_source='changed' WHERE id=?", (fn['id'],))
    assert checks.listing(net, 'game') == []


def test_busy_functions_are_left_for_a_later_batch(batch):
    net, fn, digest, ident, tested = batch
    worker, _ = net.local_agent('busy')
    net.claim_row(worker, 'game', 'caller')
    result = checks.run(net, 'game', 1, Log())
    assert result['busy'] == 1 and result['tested'] == 0 and tested == []
    assert net.store.one('SELECT agent_id FROM claims')['agent_id'] == worker.id


def test_cancellation_keeps_completed_measurements_and_releases_claim(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    log = Log()
    def measure(*args):
        raise RuntimeError('cancelled')
    monkeypatch.setattr(checks, 'measure', measure)
    with pytest.raises(RuntimeError, match='cancelled'):
        checks.run(net, 'game', 1, log)
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_input_change_during_compile_discards_measurement(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    def measure(*args):
        with net.store.tx() as db: db.execute("UPDATE functions SET best_source='changed' WHERE id=?", (fn['id'],))
        return {'score':0,'percent':100,'exact':True,'error':''}
    monkeypatch.setattr(checks, 'measure', measure)
    assert checks.run(net, 'game', 1, Log())['tested'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM candidate_rechecks')['n'] == 0


def test_exact_diagnostic_does_not_mark_a_match(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    monkeypatch.setattr(checks, 'measure', lambda *args: {'score':0,'percent':100,'exact':True,'error':''})
    assert checks.run(net, 'game', 1, Log())['exact'] == 1
    assert net._function('caller', 'game')['status'] == 'open'


def test_failed_compiles_are_saved_as_failures_not_scores(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    monkeypatch.setattr(checks, 'measure', lambda *args: {'score':None,'percent':None,'exact':False,'error':'compile failed'})
    assert checks.run(net, 'game', 1, Log())['failed'] == 1
    assert checks.listing(net, 'game')[0]['rechecks'][0]['measurement']['score'] is None


def test_imported_projects_use_the_restoring_diagnostic_judge(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    monkeypatch.undo()  # Exercise the real dispatch rather than the measurement stub.
    calls = []
    monkeypatch.setattr(net, 'fork_slug', lambda _: 'game')
    net.forks = SimpleNamespace(_attempt=lambda *a,**kw: calls.append(kw) or {'score':25,'percent':75,'matched':False,'text':'diagnostic'})
    result = checks.measure(net, None, net.project_row('game'), fn, 'int caller(){return 1;}')
    assert result['score'] == 25
    assert calls == [{'keep':False,'quiet':0,'diagnostic':True}]


def test_next_batch_advances_past_cached_small_functions(batch):
    net, fn, digest, ident, tested = batch
    helper = net._function('helper', 'game')
    with net.store.tx() as db:
        aid = db.execute('SELECT agent_id FROM attempts WHERE id=?', (ident,)).fetchone()[0]
        alt = db.execute('INSERT INTO attempts(function_id,agent_id,source,compiled,score,context_version,created_at) '
                         "VALUES(?,?, 'int helper(){return 2;}',1,30,1,0)", (helper['id'], aid)).lastrowid
        db.execute('INSERT INTO settings VALUES(?,?)', ('candidate-quality:'+str(helper['id']), json.dumps([
            {'source_hash':hashlib.sha256(helper['best_source'].encode()).hexdigest(), 'agent':'worker',
             'warning':'Review this candidate.', 'alternatives':[alt]}])))
    checks.run(net, 'game', 1, Log())
    assert tested == ['caller']
    checks.run(net, 'game', 1, Log())
    assert tested == ['caller','helper']


def test_missing_toolchain_is_a_recorded_failure_and_releases_claim(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    def missing(*args):
        raise checks.toolchains.ToolchainError('Install the selected compiler first.')
    monkeypatch.setattr(checks, 'measure', missing)
    assert checks.run(net, 'game', 1, Log())['failed'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_explicit_retry_remeasures_failed_results_without_changing_source(batch, monkeypatch):
    net, fn, digest, ident, tested = batch
    monkeypatch.setattr(checks, 'measure', lambda *args: {'score':None,'percent':None,'exact':False,'error':'missing tool'})
    checks.run(net, 'game', 1, Log())
    assert checks.run(net, 'game', 1, Log())['tested'] == 0
    monkeypatch.setattr(checks, 'measure', lambda *args: {'score':20,'percent':80,'exact':False,'error':''})
    assert checks.run(net, 'game', 1, Log(), retry_failed=True)['tested'] == 1
    assert candidate_quality.pending(net, net._function('caller','game'))
