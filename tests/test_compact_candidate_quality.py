"""Compact agent reads must not hide source-bound concerns behind the full brief."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from decompnet import candidate_quality
from decompnet.app import agent_tokens, claim_state
from tests.test_claims import _game


def report(net, fn, source, **extra):
    value = {'source_hash':hashlib.sha256(source.encode()).hexdigest(),
             'warning':'Uninitialized counter.\n' + 'x' * 700, 'agent':'worker', 'alternatives':[999], **extra}
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   (candidate_quality.PREFIX + str(fn['id']), json.dumps([value])))


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_compact_reads_include_only_the_current_report_without_heavy_brief(tmp_path, monkeypatch, mode):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    source = 'int f(){int counter;return counter;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (source,))
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':mode}),))
    agent, _ = net.local_agent('a')
    net.claim_row(agent, 'game', 'f')
    fn = net._function('f', 'game')
    report(net, fn, source, structured=True, corrected_attempt=999)
    def forbidden(*args, **kwargs):
        raise AssertionError('Compact quality cue must not hydrate the full brief or alternatives')
    monkeypatch.setattr(candidate_quality, 'brief', forbidden)
    monkeypatch.setattr(candidate_quality, 'corrected_alternative', forbidden)
    from decompnet.app import agent_evidence
    monkeypatch.setattr(agent_evidence, 'baseline', lambda *a: None)
    for text in (claim_state.brief(net, fn, agent, 'Claimed'),
                 agent_tokens.get_candidate(net, agent, 'game', 'f')):
        assert 'Candidate quality (agent report, not proven' in text
        assert 'Uninitialized counter.' in text and 'x' * 201 not in text
        assert 'get_candidate(base="reported_corrected")' in text
        assert 'Work remains available' in text
    assert net._function('f','game')['best_source'] == source
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 1


def test_source_hash_review_status_and_matched_state_bound_the_hint():
    source = 'int f(){return 1;}'
    fn = {'id':1, 'project_id':1, 'status':'open'}
    saved = [{'source_hash':hashlib.sha256(source.encode()).hexdigest(),
              'warning':'Concern', 'alternatives':[1]}]
    class Store:
        def one(self, sql, key):
            assert sql == 'SELECT value FROM settings WHERE key=?'
            return {'value':json.dumps(saved)}
    store = Store()
    net = SimpleNamespace(store=store, project_store=lambda pid: store)
    assert candidate_quality.source_hint(net, fn, source)
    assert not candidate_quality.source_hint(net, fn, source + ' ')
    saved[0]['review'] = 'resume'
    assert not candidate_quality.source_hint(net, fn, source)
    saved[0]['review'] = 'pending'
    fn['status'] = 'matched'
    assert not candidate_quality.source_hint(net, fn, source)


def test_unstructured_alternative_ids_are_not_advertised_as_corrected(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    fn = net._function('f','game')
    source = 'int f(){return 1;}'
    report(net, fn, source)
    assert 'reported_corrected' not in candidate_quality.source_hint(net, fn, source)


def test_candidate_read_does_not_attach_saved_best_report_to_agent_trial(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    saved = 'int f(){return 1;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', (saved,))
    agent, _ = net.local_agent('a')
    net.claim_row(agent, 'game', 'f')
    fn = net._function('f', 'game')
    report(net, fn, saved)
    net._record(fn, agent, 20, 'int f(){return 2;}', 1)
    from decompnet.app import agent_evidence
    monkeypatch.setattr(agent_evidence, 'baseline', lambda *a: None)
    text = agent_tokens.get_candidate(net, agent, 'game', 'f')
    assert 'return 2' in text and 'Candidate quality (agent report' not in text
