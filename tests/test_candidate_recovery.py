"""Corrective alternatives need an explicit review and a new normal-judge verdict."""
import hashlib
import json

import pytest

from decompnet import candidate_quality, candidate_recovery
from decompnet.network import NetworkError
from tests.test_agent_evidence import dependency_game


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    net, a = dependency_game(tmp_path)
    monkeypatch.setattr(net, "_context", lambda *args: {"header":"", "version":1})
    fn = net._function('caller', 'game')
    bad = 'int caller(){int counter;return counter;}'
    good = 'int caller(){return 1;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10 WHERE id=?', (bad, fn['id']))
        ident = db.execute('INSERT INTO attempts(function_id,agent_id,source,compiled,score,context_version,created_at) '
                           'VALUES(?,?,?,1,20,1,0)', (fn['id'], a.id, good)).lastrowid
        digest = hashlib.sha256(bad.encode()).hexdigest()
        db.execute('INSERT INTO settings VALUES(?,?)', ('candidate-quality:'+str(fn['id']), json.dumps([
            {'source_hash':digest, 'agent':'worker', 'warning':'Uninitialized counter.', 'alternatives':[ident]}])))
    calls = []
    def attempt(agent, function, source, slug):
        calls.append((function, source, slug))
        net._record(fn, agent, 20, source, 1)
        return {'score':20, 'matched':False}
    monkeypatch.setattr(net, 'attempt', attempt)
    return net, fn, digest, ident, good, calls


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_explicit_review_rejudges_and_selects_a_worse_scoring_correction(recovery, kind):
    net, fn, digest, ident, good, calls = recovery
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':kind}),))
    result = candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    fresh = net._function('caller', 'game')
    assert result == {'selected':'caller', 'score':20, 'matched':False}
    assert fresh['best_source'] == good and fresh['best_score'] == 20 and fresh['status'] == 'open'
    assert calls == [('caller', good, 'game')]
    assert candidate_quality.pending(net, fresh) == []
    assert candidate_quality.records(net, fresh)[0]['source_hash'] == digest
    assert net.store.one('SELECT COUNT(*) n FROM notes')['n'] == 1


@pytest.mark.parametrize('bad_id', [True, '1', -1, 999999])
def test_only_attached_compiled_alternatives_are_eligible(recovery, bad_id):
    net, fn, digest, ident, good, calls = recovery
    with pytest.raises(NetworkError):
        candidate_recovery.adopt(net, 'game', 'caller', digest, bad_id)
    assert calls == []


def test_stale_report_does_not_compile_or_replace_source(recovery):
    net, fn, digest, ident, good, calls = recovery
    with net.store.tx() as db: db.execute("UPDATE functions SET best_source='changed' WHERE id=?", (fn['id'],))
    with pytest.raises(NetworkError, match='Refresh'):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert calls == []


@pytest.mark.parametrize('change', ['context', 'config', 'source', 'setaside'])
def test_concurrent_changes_are_not_overwritten(recovery, monkeypatch, change):
    net, fn, digest, ident, good, calls = recovery
    old_context = dict(net._context(fn['project_id'], fn['unit']))
    def attempt(*args):
        with net.store.tx() as db:
            if change == 'config': db.execute("UPDATE projects SET config='{}'")
            if change == 'source': db.execute("UPDATE functions SET best_source='another source' WHERE id=?", (fn['id'],))
            if change == 'setaside': db.execute("UPDATE functions SET status='asm' WHERE id=?", (fn['id'],))
        if change == 'context':
            monkeypatch.setattr(net, '_context', lambda *args: old_context | {'version':999})
        return {'score':20, 'matched':False}
    monkeypatch.setattr(net, 'attempt', attempt)
    with pytest.raises(NetworkError):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert net._function('caller', 'game')['best_source'] != good


def test_compile_failure_keeps_the_reported_source_and_pause(recovery, monkeypatch):
    net, fn, digest, ident, good, calls = recovery
    monkeypatch.setattr(net, 'attempt', lambda *args: {'score':None, 'matched':False})
    with pytest.raises(NetworkError, match='did not compile'):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert candidate_quality.pending(net, net._function('caller', 'game'))


def test_alternative_with_its_own_warning_is_not_selected(recovery):
    net, fn, digest, ident, good, calls = recovery
    reports = candidate_quality.records(net, fn)
    reports.append({'source_hash':hashlib.sha256(good.encode()).hexdigest(), 'review':'pending'})
    with net.store.tx() as db:
        db.execute('UPDATE settings SET value=? WHERE key=?',
                   (json.dumps(reports), 'candidate-quality:'+str(fn['id'])))
    with pytest.raises(NetworkError, match='also has an unresolved'):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert calls == []


def test_normal_judge_ownership_failure_is_preserved(recovery, monkeypatch):
    net, fn, digest, ident, good, calls = recovery
    def busy(*args):
        raise NetworkError('caller is claimed by worker')
    monkeypatch.setattr(net, 'attempt', busy)
    with pytest.raises(NetworkError, match='claimed by worker'):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert candidate_quality.pending(net, net._function('caller', 'game'))


def test_known_reported_shape_cannot_replace_the_reviewed_correction(recovery):
    net, fn, digest, ident, good, calls = recovery
    bad = net._function('caller', 'game')['best_source']
    candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    a, _ = net.local_agent('worker')
    net._record(net._function('caller', 'game'), a, 5, bad, 1)
    fresh = net._function('caller', 'game')
    assert fresh['best_source'] == good and fresh['best_score'] == 20
    assert net.store.one('SELECT score FROM attempts ORDER BY id DESC LIMIT 1')['score'] == 5


@pytest.mark.parametrize('revision', [1, 1000])
def test_progress_autosave_during_judge_does_not_invalidate_review(recovery, monkeypatch, revision):
    net, fn, digest, ident, good, calls = recovery
    original = net.attempt
    def attempt(*args):
        result = original(*args)
        config = dict(net.cfg(net.project_row('game')))
        config['progress_revision'] = revision
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(config), fn['project_id']))
        return result
    monkeypatch.setattr(net, 'attempt', attempt)
    result = candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert result['score'] == 20
    assert net._function('caller', 'game')['best_source'] == good
    assert net.cfg(net.project_row('game'))['progress_revision'] == revision


def test_autosave_does_not_hide_a_real_compiler_change(recovery, monkeypatch):
    net, fn, digest, ident, good, calls = recovery
    def attempt(*args):
        config = dict(net.cfg(net.project_row('game')))
        config.update(progress_revision=1000, compiler={'toolchain':'another-compiler'})
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(config), fn['project_id']))
        return {'score':20, 'matched':False}
    monkeypatch.setattr(net, 'attempt', attempt)
    with pytest.raises(NetworkError, match='Build settings changed'):
        candidate_recovery.adopt(net, 'game', 'caller', digest, ident)
    assert net._function('caller', 'game')['best_source'] != good
