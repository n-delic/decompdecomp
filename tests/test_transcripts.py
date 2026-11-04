"""Full transcripts survive feed eviction and restart, without exposing credentials or other projects."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.agents import Manager, Session
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def setup(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('demo', 'Synthetic transcript project', 'mips', str(tmp_path), '{}'))
    manager = Manager(net, tmp_path, 'http://127.0.0.1/mcp')
    session = Session('1', 'codex', 'helper', 'demo', 'helper', {'api_key': 'private-key'})
    manager.sessions['1'] = session
    row = manager._session_begin(session)
    return net, manager, session, row


def test_full_text_survives_short_feed_and_restart(setup, tmp_path):
    net, manager, session, row = setup
    text = 'full prompt ' * 1000
    session.record('prompt', text)
    for i in range(405):
        session.push('text', f'reply {i}')
    assert len(session.feed) == 400
    assert manager.transcript('demo', 1)['events'][0]['text'] == text
    restored = Manager(net, tmp_path, 'http://127.0.0.1/mcp')
    first = restored.transcript('demo', 1, row)
    assert first['more'] and len(first['events']) == 25
    second = restored.transcript('demo', 1, row, first['events'][-1]['id'])
    assert second['events'][0]['id'] > first['events'][-1]['id']
    assert first['events'][0]['kind'] == 'prompt'


def test_redaction_and_project_ownership(setup):
    net, manager, session, row = setup
    session.record('tool_request', {'api_key': 'unknown-key', 'code': 'private-key', 'password': 'password-value'})
    session.record('tool_response', 'Authorization: Bearer bearer-secret')
    text = json.dumps(manager.transcript('demo', 1)['events'])
    assert all(value not in text for value in ('unknown-key', 'private-key', 'password-value', 'bearer-secret'))
    with pytest.raises(NetworkError):
        manager.transcript('other', 1, row)
    with pytest.raises(NetworkError):
        manager.transcript('demo', 2, row)


def test_tool_exchange_and_live_usage(setup):
    net, manager, session, row = setup
    agent = SimpleNamespace(name='helper')
    manager.record_agent(agent, 'demo', 'tool_request', {'name': 'get_function', 'arguments': {'function': 'target'}})
    manager.record_agent(agent, 'demo', 'tool_response', {'content': 'full source ' * 1000})
    session.usage.update(input=123, output=12, cache_read=456)
    data = manager.transcript('demo', 1)
    assert len(data['events'][1]['text']) > 4000
    assert data['session']['input_tokens'] == 123
    assert data['session']['cache_read_tokens'] == 456
    manager.record_agent(agent, 'other', 'tool_request', 'not recorded')
    assert len(manager.transcript('demo', 1)['events']) == 2


def test_old_sessions_are_not_fabricated_and_session_paging(setup):
    net, manager, session, row = setup
    for _ in range(35):
        manager._session_begin(session)
    first = manager.transcript('demo', 1)
    assert len(first['sessions']) == 30 and not first['events']
    second = manager.transcript('demo', 1, before=first['sessions'][-1]['id'])
    assert len(second['sessions']) == 6
    assert second['sessions'][0]['id'] < first['sessions'][-1]['id']


def test_mcp_records_full_arguments_results_and_errors(setup, monkeypatch):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    from decompnet.app import tools
    net, manager, session, row = setup
    agent = SimpleNamespace(id=1, name='helper')
    monkeypatch.setitem(tools.HANDLERS, 'get_function', lambda *a, **kw: 'source ' * 1000)
    monkeypatch.setattr(tools, '_named', lambda *a: a[-1])
    mcp = tools.build_mcp(net, lambda context: (agent, 'demo'), observe=manager.record_agent)
    anyio.run(mcp.call_tool, 'get_function', {'function': 'target'})
    events = manager.transcript('demo', 1)['events']
    assert json.loads(events[0]['text'])['arguments']['function'] == 'target'
    assert len(json.loads(events[1]['text'])['content']) == 7000
    first_id = json.loads(events[0]['text'])['call_id']
    assert first_id and first_id == json.loads(events[1]['text'])['call_id']

    def fail(*args, **kw):
        raise NetworkError('Missing function.')
    monkeypatch.setitem(tools.HANDLERS, 'get_function', fail)
    with pytest.raises(ToolError, match='Missing function'):
        anyio.run(mcp.call_tool, 'get_function', {'function': 'missing'})
    events = manager.transcript('demo', 1)['events']
    assert 'Missing function.' in events[-1]['text']
    assert json.loads(events[-2]['text'])['call_id'] == json.loads(events[-1]['text'])['call_id']
    assert json.loads(events[-1]['text'])['call_id'] != first_id


def test_export_all_sessions_and_events_without_paging_or_other_agents(setup, tmp_path, monkeypatch):
    from pathlib import Path
    net, manager, session, row = setup
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    for i in range(275):
        session.record('tool_response', {'index': i, 'password': 'secret'})
    for _ in range(35):
        manager._session_begin(session)
    session.record('text', 'last session')
    result = manager.transcripts.export('demo', 1)
    data = json.loads(Path(result['path']).read_text())
    assert result['sessions'] == len(data['sessions']) == 36
    assert result['events'] == 276
    assert len(data['sessions'][0]['events']) == 275
    assert data['sessions'][-1]['events'][0]['text'] == 'last session'
    assert 'secret' not in Path(result['path']).read_text()
    other = manager.transcripts.export('other', 1)
    assert json.loads(Path(other['path']).read_text())['sessions'] == []
    assert manager.transcripts.export('demo', 2)['sessions'] == 0
    assert manager.transcripts.export('demo', 1)['path'] != result['path']


def test_whole_session_includes_unloaded_events_and_checks_ownership(setup):
    net, manager, session, row = setup
    for index in range(60):
        session.record('text', f'message {index}')
    result = manager.transcript('demo', 1, row, whole=True)
    assert len(result['events']) == 60 and not result['more']
    assert result['events'][-1]['text'] == 'message 59'
    assert len(manager.transcript('demo', 1, row)['events']) == 25
    with pytest.raises(NetworkError):
        manager.transcript('demo', 2, row, whole=True)
    with pytest.raises(NetworkError):
        manager.transcript('other', 1, row, whole=True)


def test_legacy_pre_prompt_probe_is_labelled_without_rewriting_history(setup):
    net,manager,session,row=setup
    session.record('tool_definitions',[{'name':'project_info'}])
    session.record('tool_request',{'name':'project_info','arguments':{'detail':False}})
    session.record('tool_response',{'name':'project_info','content':'probe'})
    session.record('prompt','actual initial prompt')
    session.record('tool_definitions',[{'name':'project_info'}])
    events=manager.transcript('demo',session.id,row)['events']
    assert [e['kind'] for e in events]==['connection_check_tools','connection_check_tool_request','connection_check_tool_response','prompt','tool_definitions']
    assert net.store.one('SELECT kind FROM helper_messages WHERE session_id=? ORDER BY id',row)['kind']=='tool_definitions'


def test_notes_append_per_session_and_persist_score_difference(setup):
    import time

    from decompnet import agent_notes
    net, manager, session, sid = setup
    agent = net.authenticate(net.register_agent('helper', 'test'))
    proj = net._project('demo')
    started = net.store.one('SELECT started_at FROM helper_sessions WHERE id=?', sid)['started_at']
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (proj['id'], 'target')).lastrowid
        for aid, score, at in [(agent.id, 1040, started-1), (999, 900, started+.01),
                               (agent.id, 660, started+.02)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,created_at) VALUES(?,?,?,?)',
                       (fid, aid, score, at))
    # Use explicit times so the test does not depend on the wall clock advancing.
    with net.store.tx() as db:
        db.execute('UPDATE helper_sessions SET ended_at=? WHERE id=?', (started+10, sid))
        agent_notes.save(db, proj['id'], fid, agent.id, 'First discovery', started+1)
        agent_notes.save(db, proj['id'], fid, agent.id, 'Additional detail', started+2)
        agent_notes.save(db, proj['id'], fid, agent.id, 'Additional detail', started+3)
    notes = net.store.all('SELECT * FROM notes WHERE function_id=?', fid)
    assert len(notes) == 1
    assert notes[0]['text'] == 'First discovery\n\nAdditional detail'
    assert (notes[0]['score_before'], notes[0]['score_after']) == (900, 660)
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,created_at) VALUES(?,?,?,?)',
                   (fid, agent.id, 600, started+4))
        agent_notes.finish(db, sid)
    assert net.store.one('SELECT score_after FROM notes WHERE function_id=?', fid)['score_after'] == 600
    with net.store.tx() as db:
        agent_notes.save(db, proj['id'], fid, agent.id, 'Different session', started+20)
    assert len(net.store.all('SELECT * FROM notes WHERE function_id=?', fid)) == 2
    assert time.time() >= started


def test_historical_notes_group_only_with_session_evidence(setup):
    from decompnet import agent_notes
    net, manager, session, sid = setup
    agent = net.authenticate(net.register_agent('helper', 'test'))
    proj = net._project('demo')
    started = net.store.one('SELECT started_at FROM helper_sessions WHERE id=?', sid)['started_at']
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (proj['id'], 'target')).lastrowid
        for text, at in [('Older', started-10), ('First', started+1), ('Second', started+2)]:
            db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                       (proj['id'], fid, agent.id, text, at))
    rows = net.store.all('SELECT n.*,a.name AS agent FROM notes n JOIN agents a ON a.id=n.agent_id ORDER BY n.id DESC')
    grouped = agent_notes.grouped(net.store, rows, proj['id'])
    assert len(grouped) == 2
    assert grouped[0]['text'] == 'First\n\nSecond'
    assert len(grouped[0]['ids']) == 2
    assert grouped[0]['score_before'] is None
    assert grouped[1]['text'] == 'Older'


def test_notes_recorded_together_without_session_history_merge_without_invented_scores(setup):
    from decompnet import agent_notes
    net, manager, session, sid = setup
    agent = net.authenticate(net.register_agent('imported-agent', 'test'))
    proj = net._project('demo')
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (proj['id'], 'imported')).lastrowid
        for text in ['First note', 'Second note']:
            db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                       (proj['id'], fid, agent.id, text, 1))
    rows = net.store.all('SELECT n.*,a.name AS agent FROM notes n JOIN agents a ON a.id=n.agent_id ORDER BY n.id DESC')
    grouped = agent_notes.grouped(net.store, rows, proj['id'])
    assert len(grouped) == 1 and len(grouped[0]['ids']) == 2
    assert grouped[0]['text'] == 'First note\n\nSecond note'
    assert grouped[0]['session_id'] is None and grouped[0]['score_before'] is None


def test_transcript_records_saved_score_improvements_once_for_own_session(setup):
    import time
    net, manager, session, sid = setup
    agent = net.authenticate(net.register_agent('helper', 'test'))
    pid = net._project('demo')['id']
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (pid, 'target')).lastrowid
        for before, score, improved, aid in [(1040,660,1,agent.id),(660,800,0,agent.id),(660,600,1,999)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,score_before,score_improved,created_at) VALUES(?,?,?,?,?,?)',
                       (fid, aid, score, before, improved, time.time()))
    session.record('tool_response', {'name':'compile_patch','content':'done'})
    session.record('tool_response', {'name':'get_candidate','content':'read'})
    changes = [json.loads(e['text']) for e in manager.transcript('demo',1,sid,whole=True)['events'] if e['kind']=='score_improvement']
    assert len(changes) == 1
    assert changes[0]['name'] == 'target'
    assert (changes[0]['score_before'], changes[0]['score']) == (1040,660)


def test_judge_persists_only_real_best_score_improvements(setup, monkeypatch):
    net, manager, session, sid = setup
    agent = net.authenticate(net.register_agent('helper', 'test'))
    pid = net._project('demo')['id']
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name,best_score) VALUES(?,?,?)', (pid,'target',1040)).lastrowid
    monkeypatch.setattr(net, 'on_improvement', lambda *args: None)
    monkeypatch.setattr(net, 'check_attempt_limit', lambda *args: None)
    for score in [660,800,None,500]:
        fn = net._function('target','demo')
        net._record(fn,agent,score,'int target(){return 1;}',0)
    rows = net.store.all('SELECT score_before,score_improved FROM attempts WHERE function_id=? ORDER BY id',fid)
    assert [tuple(row) for row in rows] == [(1040,1),(660,0),(660,0),(660,1)]


def test_unmanaged_notes_record_improvement_and_zero_change(setup):
    from decompnet import agent_notes
    net, _, _, _ = setup
    agent = net.authenticate(net.register_agent('unmanaged', 'test'))
    proj = net._project('demo')
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (proj['id'], 'target')).lastrowid
        db.execute('INSERT INTO attempts(function_id,agent_id,score,score_before,created_at) VALUES(?,?,?,?,?)',
                   (fid, agent.id, 120, 150, 1))
        agent_notes.save(db, proj['id'], fid, agent.id, 'Improved', at=2)
        agent_notes.save(db, proj['id'], fid, agent.id, 'No new improvement', at=3)
        agent_notes.save(db, proj['id'], fid, agent.id, 'Measured local search', at=4, measured=(120, 120))
    rows = net.store.all('SELECT score_before,score_after FROM notes WHERE function_id=? ORDER BY id', fid)
    assert [(r['score_before'], r['score_after']) for r in rows] == [(150,120), (120,120), (120,120)]


def test_room_notes_preserve_zero_scores_and_update_existing_note(setup):
    net, _, _, _ = setup
    proj = net._project('demo')
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name,vram) VALUES(?,?,?)', (proj['id'], 'target', 4096)).lastrowid
    net.apply_remote_note('demo', 1, 4096, 'Room note', 'guest', 1)
    net.apply_remote_note('demo', 1, 4096, 'Room note', 'guest', 1, 120, 0)
    net.apply_remote_note_named('demo', 'target', 'Other note', 'guest', 2, 0, 0)
    rows = net.store.all('SELECT score_before,score_after FROM notes WHERE function_id=? ORDER BY id', fid)
    assert [(r['score_before'], r['score_after']) for r in rows] == [(120,0), (0,0)]


def test_deep_note_updates_preserve_measured_scores(setup):
    net, _, _, _ = setup
    agent = net.authenticate(net.register_agent('permuter-test', 'test'))
    proj = net._project('demo')
    with net.store.tx() as db:
        fid = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (proj['id'], 'target')).lastrowid
    net.add_note(agent, 'demo', 'Deep permuter: first turn', 'target', score_before=150, score_after=120)
    net.add_note(agent, 'demo', 'Deep permuter: second turn', 'target', score_before=120, score_after=120)
    rows = net.store.all('SELECT score_before,score_after FROM notes WHERE function_id=?', fid)
    assert len(rows) == 1
    assert (rows[0]['score_before'], rows[0]['score_after']) == (120, 120)
