"""Broken tool connections and stale model selections stop before paid work."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from decompnet.app.agents import Fatal, Manager
from decompnet.app.engine import RomNetwork


@pytest.mark.parametrize('missing,denied', [(False,False), (True,False), (False,True)])
def test_mcp_preflight_checks_actual_advertised_tools(monkeypatch, missing, denied):
    import mcp
    import mcp.client.streamable_http as transport
    import mcp.shared._httpx_utils as http

    seen = []

    @asynccontextmanager
    async def client(**kwargs):
        seen.append(kwargs)
        yield object()

    @asynccontextmanager
    async def stream(url, **kwargs):
        seen.append(url)
        yield (None, None, None)

    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def initialize(self): seen.append('initialize')
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in
                (['claim_function'] if missing else ['project_info','claim_function', 'compile_and_diff', 'release_function'])])
        async def call_tool(self, name, arguments):
            seen.append((name,arguments))
            return SimpleNamespace(is_error=denied)

    monkeypatch.setattr(http, 'create_mcp_http_client', client)
    monkeypatch.setattr(transport, 'streamable_http_client', stream)
    monkeypatch.setattr(mcp, 'ClientSession', Session)
    manager = Manager.__new__(Manager)
    import threading
    manager._tool_startup_slots = threading.BoundedSemaphore(2)
    manager.net = SimpleNamespace(authenticate=lambda token: seen.append('authenticated'))
    manager.mcp_url = 'http://127.0.0.1:1234/mcp'
    if missing or denied:
        with pytest.raises(Fatal, match='No model request'):
            manager._preflight_tools('test-token')
    else:
        manager._preflight_tools('test-token')
        assert ('project_info', {'detail':False}) in seen
    assert seen[0] == 'authenticated'
    assert seen[1]['headers'] == {'Authorization': 'Bearer test-token', 'X-Decomp-Connection-Check':'1'}
    assert 'initialize' in seen


def test_restart_closes_only_unfinished_sessions(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    monkeypatch.setattr(Manager, '_restore', lambda self: None)
    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    with net.store.tx() as db:
        for ended, outcome in [(None, ''), (10, 'ok')]:
            db.execute('INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at,outcome) VALUES(?,?,?,?,?,?,?,?)',
                       (1, 'game', 'claude', '', 'helper', 1, ended, outcome))
    Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    rows = net.store.all('SELECT ended_at,outcome FROM helper_sessions ORDER BY id')
    assert rows[0]['ended_at'] and rows[0]['outcome'] == 'interrupted'
    assert rows[1]['ended_at'] == 10 and rows[1]['outcome'] == 'ok'


def test_batch_excludes_already_held_and_returned_functions(tmp_path, monkeypatch):
    from decompnet.network import Network
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    agent, _ = net.local_agent('test')
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)', ('game','Game',str(tmp_path),json.dumps({'kind':'rom'}))).lastrowid
        ids = [db.execute('INSERT INTO functions(project_id,name,size,n_insns) VALUES(?,?,?,?)', (pid, f'f{i}', 16, 4)).lastrowid for i in range(3)]
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (ids[0], agent.id, 1, 9999999999))
    monkeypatch.setattr(net, 'sdk_functions', lambda proj: set())
    calls = []
    def claim(self, agent, project, **kwargs):
        calls.append(list(kwargs['batch_from']['deferred']))
        return {'id': ids[len(calls)], 'name': f'f{len(calls)}'}
    monkeypatch.setattr(Network, 'claim_row', claim)
    rows = net._batch_more(agent, {'id':pid,'slug':'game'}, {'id':ids[0],'unit':''}, 2)
    assert len(rows) == 2
    assert ids[0] in calls[0] and ids[1] in calls[1]


def test_preflight_against_real_isolated_app(tmp_path, monkeypatch):
    import socket
    import threading
    import time

    import uvicorn

    from decompnet.app.server import build

    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        mcp, manager, security, _ = build(net, tmp_path, port, 'test-window-secret')
        app = mcp.streamable_http_app(transport_security=security, host='127.0.0.1')
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            assert server.started
            _, token = net.local_agent('test-helper')
            from decompnet.app.agents import Session
            events=[]
            session=Session('probe','codex','Probe','game','test-helper',{})
            session.record=lambda kind,value:events.append((kind,value))
            manager.sessions[session.id]=session
            monkeypatch.setattr(manager, 'project_of', lambda agent: 'game')
            from decompnet.app import tools
            with net.store.tx() as db:
                db.execute('INSERT INTO projects(slug,root,config) VALUES(?,?,?)',('game',str(tmp_path),'{}'))
            def slow_facts(*args, **kwargs):
                raise AssertionError('Full project facts must not run during connection checks')
            monkeypatch.setitem(tools.HANDLERS, 'project_info', slow_facts)
            monkeypatch.setattr(net, 'overview', slow_facts)
            manager._preflight_tools(token)
            assert [kind for kind,_ in events] == ['connection_check_tools','connection_check_tool_request','connection_check_tool_response']
            assert net.store.one('SELECT calls FROM agent_tool_usage WHERE name=?','project_info') is None
            monkeypatch.setattr(manager, 'project_of', lambda agent: None)
            with pytest.raises(Fatal, match='No model request'):
                manager._preflight_tools(token)
            with pytest.raises(Fatal, match='No model request'):
                manager._preflight_tools('invalid')
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.mark.parametrize('message', ['invalid API token', "The decomp tools aren't available in this session", 'no decomp tools are available'])
def test_lost_tool_connection_pauses_instead_of_paid_retries(tmp_path, monkeypatch, message):
    import os
    import sys

    from decompnet.app.agents import Session
    from tests.test_agents import _game

    net = _game(tmp_path)  # Work exists; this case exercises a connection lost after launch.
    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    manager = Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    monkeypatch.setattr(manager, '_preflight_tools', lambda token, **kwargs: None)
    # This case represents a connection lost after the initial handshake succeeded.
    def prepared(*args, **kwargs):
        net._pending_tool_handshakes.clear()
    monkeypatch.setattr(manager, "prepare_session", prepared)
    event = {'type':'assistant', 'message':{'content':[{'type':'text', 'text':message}]}}
    code = f'print({json.dumps(event)!r})'
    monkeypatch.setattr(manager, '_command', lambda *args: ([sys.executable, '-c', code], dict(os.environ), ''))
    session = Session('1', 'claude', 'helper', 'game', 'helper', {})
    _, token = net.local_agent('helper')
    with pytest.raises(Fatal, match='lost access'):
        manager._run_cli(session, token, tmp_path)


def test_transient_preflight_retains_phase_and_redacted_leaf_error(monkeypatch):
    import mcp.shared._httpx_utils as http

    from decompnet.app.agents import ToolConnectionUnavailable

    @asynccontextmanager
    async def unavailable(**kwargs):
        raise ExceptionGroup('transport failed', [TimeoutError('busy secret-token')])
        yield  # pragma: no cover

    monkeypatch.setattr(http,'create_mcp_http_client',unavailable)
    manager=Manager.__new__(Manager)
    import threading
    manager._tool_startup_slots=threading.BoundedSemaphore(2)
    manager.net=SimpleNamespace(authenticate=lambda token: None)
    manager.mcp_url='http://127.0.0.1:1/mcp'
    with pytest.raises(ToolConnectionUnavailable) as error:
        manager._preflight_tools('secret-token')
    text=str(error.value)
    assert 'opening MCP transport' in text and 'TimeoutError' in text and 'after ' in text
    assert 'secret-token' not in text and '[redacted]' in text
    assert 'No model request was started' in text


def test_connection_retries_do_not_exhaust_paid_failure_budget(tmp_path,monkeypatch):
    from decompnet.app import agents
    from tests.test_agents import URL, Quick, _game, _no_agent_settings

    _no_agent_settings(monkeypatch)
    net=_game(tmp_path)
    manager=Manager(net,tmp_path,URL)
    s=manager._record('game','claude','worker','worker',{})
    s.stop=Quick()
    calls=[]
    def run(session,token,workdir):
        calls.append(1)
        if len(calls)<=7:
            raise agents.ToolConnectionUnavailable('MCP transport timed out. No model request was started.')
        session.stop.set()
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    manager._run(s)
    assert len(calls)==8 and s.state!='paused'
    waits=[t for t in s.stop.waits if t]
    offset=int(s.id)%31
    assert [round(t) for t in waits]==[n+offset for n in [5,10,20,40,80,160,300]] and max(waits)<=330
    rows=net.store.all('SELECT id,outcome,compiles,tool_calls FROM helper_sessions WHERE helper_id=? ORDER BY id',s.id)
    assert [r['outcome'] for r in rows[:7]]==['connection_retry']*7
    assert all(r['compiles']==r['tool_calls']==0 for r in rows[:7])
    assert any('transport timed out' in r['text'] for r in net.store.all('SELECT text FROM helper_messages WHERE session_id=?',rows[0]['id']))


def test_legacy_connection_pause_resumes_but_manual_stops_do_not(tmp_path,monkeypatch):
    from decompnet.app import agents
    from tests.test_agents import URL, _game, _no_agent_settings

    _no_agent_settings(monkeypatch)
    monkeypatch.setattr(Manager,'_prepare',lambda *args:None)
    monkeypatch.setattr(Manager,'_run',lambda *args:None)
    net=_game(tmp_path)
    manager=Manager(net,tmp_path,URL)
    retry=manager._record('game','claude','retry','retry',{})
    retry.state='paused';retry.why="The app's tool connection is unavailable. Restart the helper after checking the app connection. No model request was started."
    manual=manager._record('game','claude','manual','manual',{})
    manual.state='stopped';manual.stopped_by=agents.BY_YOU
    auth=manager._record('game','claude','auth','auth',{})
    auth.state='paused';auth.why='Sign in again'
    assert manager.resume()==[retry.id]
    assert manual.state=='stopped' and auth.state=='paused'


def test_proposals_need_not_wait_for_consensus():
    from decompnet.app import tools
    for prompt in (tools.WORK_PROMPT,tools.FORK_PROMPT):
        assert 'a second model is required for automatic acceptance, not for proposing' in prompt
