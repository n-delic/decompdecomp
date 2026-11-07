"""Sessions and transcript retention follow their owner in real separate files."""
import pytest

from decompnet.app import database_cleanup, session_history
from decompnet.app.agents import Manager
from decompnet.app.transcripts import service
from decompnet.network import NetworkError
from tests import test_project_storage as base

scoped = base.scoped


def seed(net):
    transcripts = service(net)
    for pid, slug in ((1, 'first'), (2, 'second')):
        target = transcripts.for_project(slug)
        with target.sessions.tx() as db:
            db.execute('INSERT INTO helper_sessions(id,helper_id,project,kind,model,agent,started_at,ended_at,tokens) '
                       'VALUES(?,?,?,?,?,?,?,?,?)', (pid,pid,slug,'codex','test','worker',1,2,pid*100))
        transcripts.append(pid,'prompt',slug,helper=pid,project=slug)
    return transcripts


def test_history_transcripts_and_cleanup_are_project_owned(scoped):
    net, stores = scoped
    transcripts = seed(net)
    assert transcripts.read('first',1)['events'][0]['text'] == 'first'
    assert transcripts.read('second',2)['events'][0]['text'] == 'second'
    with pytest.raises(NetworkError):transcripts.read('first',1,2)
    assert session_history.records(net,1,'worker',project='first')[1][0]['tokens'] == 100
    assert session_history.records(net,1,'worker',project='second')[1] == []
    assert not net.store.one('SELECT 1 FROM helper_messages')
    # Retention uses shared helper identities; no local identity copies are needed.
    from decompnet.app.agents import SCHEMA
    with net.store.tx() as db:
        db.execute(SCHEMA[0])
        db.execute("INSERT INTO helpers(id,project,kind,name,label,state) VALUES(1,'first','codex','worker','worker','stopped')")
    assert database_cleanup.summary(net)['messages'] == 1
    rows = database_cleanup.eligible(net, {'removed':True})
    assert len(rows) == 1 and rows[0]['_store'] is transcripts.for_project('second').store
    transcripts.clear_helper(2,'second','worker')
    transcripts.append(2,'text','late callback',helper=2,project='second')
    assert transcripts.read('second',2)['events'] == []
    assert transcripts.read('first',1)['events'][0]['text'] == 'first'
    assert session_history.records(net,2,'worker',project='second')[1][0]['tokens'] == 200


def test_restart_closes_sessions_in_each_project(scoped, tmp_path, monkeypatch):
    net, stores = scoped
    seed(net)
    for pid in (1,2):
        with stores[pid].tx() as db:db.execute('UPDATE helper_sessions SET ended_at=NULL')
    monkeypatch.setattr(Manager, 'refresh_detected', lambda self:None)
    Manager(net,tmp_path,'http://127.0.0.1/mcp')
    for pid in (1,2):
        row = stores[pid].one('SELECT ended_at,outcome FROM helper_sessions')
        assert row['ended_at'] is not None and row['outcome'] == 'interrupted'
    assert not net.store.one('SELECT 1 FROM helper_sessions')


def test_id_only_callback_rejects_ambiguous_session_owner(scoped):
    net, stores = scoped
    transcripts = seed(net)
    with stores[2].tx() as db:db.execute('UPDATE helper_sessions SET id=1')
    with pytest.raises(NetworkError, match='several project databases'):
        transcripts.append(1,'text','ambiguous')
    assert len(transcripts.read('first',1)['events']) == 1
    # An explicit owner still works without inspecting another project's session.
    transcripts.append(1,'text','second only',project='second',helper=2)
    assert transcripts.read('second',2)['events'][0]['text'] == 'second only'


def test_model_filters_use_catalog_identity_and_project_attempts(scoped):
    from types import SimpleNamespace

    from decompnet import model_checks, pause_scope
    from decompnet.app.agents import SCHEMA
    net, stores = scoped
    seed(net)
    with net.store.tx() as db:
        db.execute(SCHEMA[0])
        db.execute("INSERT INTO helpers(id,project,kind,name,label,state,model) VALUES(1,'first','codex','worker','worker','stopped','test')")
    for pid in (1,2):
        with stores[pid].tx() as db:
            db.execute("UPDATE agents SET name='stale copied identity'")
            db.execute('INSERT INTO attempts(function_id,agent_id,source,score,compiled,created_at) VALUES(?,?,?,?,?,?)',
                       (pid*10,1,'candidate',100,1,1.5))
    worker = SimpleNamespace(id=1,name='worker')
    assert model_checks.tried(net,'first',worker) == {10}
    assert model_checks.tried(net,'second',worker) == {20}
    assert pause_scope.recorded_model(net,net._project('first'),worker) == 'test'


def test_transcript_writer_is_independent_of_operational_writer(scoped, tmp_path, monkeypatch):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    net, stores = scoped
    transcripts = seed(net)
    target = transcripts.for_project('first').store
    assert target.path != stores[1].path
    assert target.path != transcripts.for_project('second').store.path
    assert not target.one("SELECT 1 FROM sqlite_master WHERE name IN ('functions','attempts','helper_sessions')")
    assert not stores[1].one("SELECT 1 FROM sqlite_master WHERE name='helper_messages'")
    # A long project write must not make transcript recording wait for its commit.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with stores[1].tx():
            pool.submit(transcripts.append,1,'text','reply '*1000,project='first',helper=1).result(timeout=3)
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    exported = transcripts.export('first',1)
    data = json.loads(Path(exported['path']).read_text())
    assert len(data['sessions']) == 1
    assert data['sessions'][0]['tokens'] == 100
    assert data['sessions'][0]['events'][-1]['text'] == 'reply '*1000
    assert target.one('SELECT COUNT(*) n FROM text_payloads')['n'] == 1
