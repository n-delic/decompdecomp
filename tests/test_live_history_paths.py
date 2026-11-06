"""New score annotations remain incremental; real corrections and shared evidence still refresh."""
import json
from types import SimpleNamespace

from decompnet.app.agent_metrics import results
from decompnet.app.history_summary import summary
from decompnet.app.sdk_identity import identify
from decompnet.app.view_snapshots import incremental_map

from .test_claims import _game


def test_new_attempt_annotations_do_not_recount_old_history(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    agent, _ = net.local_agent('worker')
    sessions = [SimpleNamespace(agent_name='worker', options={})]
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled) VALUES(1,?,100,1)', (agent.id,))
    summary(net.store, 1); results(net, 'game', sessions)
    with net.store.tx() as db:
        aid = db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled) VALUES(1,?,50,1)', (agent.id,)).lastrowid
        db.execute('UPDATE attempts SET score_improved=1 WHERE id=?', (aid,))
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    assert summary(net.store, 1)['agents'][agent.id]['compiles'] == 2
    assert results(net, 'game', sessions)['worker']['compiles'] == 2
    reads = [q for q in queries if 'JOIN attempts t' in q]
    assert len(reads) == 2 and all('t.id>1' in q for q in reads)
    with net.store.tx() as db:
        db.execute('UPDATE attempts SET score=10000 WHERE id=1')
    queries.clear()
    summary(net.store, 1); results(net, 'game', sessions)
    assert any('JOIN attempts t' in q and 't.id>0' in q for q in queries)


def test_map_reads_only_changed_rows_and_retains_previous_snapshot(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [(f'f{i}', 40, 'open') for i in range(10)])
    first = incremental_map(net, 'game')
    calls = []
    original = net.function_map
    def measured(slug, **kw):
        calls.append(kw.get('function_ids'))
        return original(slug, **kw)
    monkeypatch.setattr(net, 'function_map', measured)
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_score=100,attempts=1 WHERE id=1')
    second = incremental_map(net, 'game')
    assert calls == [[1]] and second[0]['best'] == 100 and first[0]['best'] is None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='renamed' WHERE id=1")
    assert incremental_map(net, 'game')[0]['name'] == 'renamed'
    assert calls[-1] is None


def test_sdk_identity_ignores_score_updates_but_refreshes_inventory(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    with net.store.tx() as db:
        db.execute("INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES('lib','ppc',1,'helper','exact',40)")
    calls = []
    monkeypatch.setattr(net.twins, 'prints', lambda _: calls.append(1) or {1: SimpleNamespace(key='exact', size=40, asm=False)})
    assert identify(net, proj, {})[1]['names'] == ['helper']
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_score=10 WHERE id=1')
    assert identify(net, proj, {})[1]['names'] == ['helper'] and len(calls) == 1
    with net.store.tx() as db:
        db.execute("UPDATE sdk_signatures SET name='other'")
    assert identify(net, proj, {})[1]['names'] == ['other'] and len(calls) == 2
    # Recorded identities are merged at call time, not captured from another caller's dictionary.
    assert identify(net, proj, {99: {'names': ['recorded']}})[99]['names'] == ['recorded']


def test_handoff_lookup_uses_worker_sessions_and_latest_handoff(tmp_path):
    from decompnet.app.session_handoffs import previous
    from decompnet.app.transcripts import Transcripts
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    Transcripts(net.store)
    with net.store.tx() as db:
        db.execute('CREATE TABLE helper_sessions(id INTEGER PRIMARY KEY,helper_id INTEGER,project TEXT)')
        db.execute("INSERT INTO helper_sessions VALUES(1,1,'game'),(2,2,'game'),(3,1,'game')")
        for sid, text in [(1, 'old'), (2, 'other worker'), (3, 'latest')]:
            db.execute('INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(?,0,?,?)',
                       (sid, 'handoff', json.dumps({'last_reply': text, 'functions': []})))
        db.execute("INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(1,0,'text','not a handoff')")
    text = previous(net, 1, 'game')
    assert 'latest' in text and 'other worker' not in text and 'not a handoff' not in text


def test_boundary_cache_survives_score_changes(tmp_path, monkeypatch):
    from decompnet.app import code_coverage
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    calls = []
    monkeypatch.setattr(code_coverage, '_boundaries', lambda *a: calls.append(1) or [])
    code_coverage.boundaries(net, net._project('game'))
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_score=1 WHERE id=1')
    code_coverage.boundaries(net, net._project('game'))
    assert len(calls) == 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=1")
    code_coverage.boundaries(net, net._project('game'))
    assert len(calls) == 2
