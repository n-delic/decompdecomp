"""Focused workers compact and resume without ordinary batch-session rotation."""
import threading
from types import SimpleNamespace as NS

import httpx

from decompnet.agents import api, claude, codex
from decompnet.app import focused_agents, session_budget

SID = '12345678-1234-1234-1234-123456789abc'


def test_focused_budget_removes_old_rotation_registration():
    net = NS()
    assert session_budget.start(net, 1, 'game', {}).tool_limit == 80
    assert session_budget.start(net, 1, 'game', {'focus_id':7}) is None
    assert session_budget.current(net, NS(id=1), 'game') is None
    assert session_budget.start(net, 2, 'game', {}).tool_limit == 80


def test_focused_prompt_has_no_batch_exit():
    text = focused_agents.prompt(NS(options={'focus_function':'test_function'}))
    assert 'one long conversation' in text and 'compaction' in text
    assert 'current best candidate' in text and 'failed experiments' in text


def test_native_resume_commands_keep_isolation_and_exact_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, 'codex_config', lambda: {})
    for kind in (codex.CODEX, claude.CLAUDE):
        initial, _ = kind.focused_session_command('helper', tmp_path, 'http://local/mcp', 'secret', 'test')
        resumed, _ = kind.focused_session_command('helper', tmp_path, 'http://local/mcp', 'secret', 'test', SID)
        assert '--ephemeral' not in initial
        assert SID in resumed and '--last' not in resumed and '--continue' not in resumed
        if kind is codex.CODEX:
            assert resumed[-3:] == ['resume', SID, '-']
            assert 'read-only' in resumed and 'approval_policy="never"' in resumed
        else:
            assert '--resume' in resumed and '--strict-mcp-config' in resumed
    assert ('conversation', SID) in codex.CODEX.parse({'type':'thread.started','thread_id':SID}, {})
    assert ('conversation', SID) in claude.CLAUDE.parse({'type':'system','subtype':'init','session_id':SID}, {})


def history():
    result = [{'role':'system','content':'rules'}, {'role':'user','content':'focused task'}]
    for n in range(5):
        result += [{'role':'assistant','content':'hypothesis','tool_calls':[{'id':str(n),'type':'function','function':{'name':'get_candidate','arguments':'{}'}}]},
                   {'role':'tool','tool_call_id':str(n),'content':'saved candidate '+str(n)}]
    return result


def test_api_compaction_preserves_instructions_latest_pair_and_accounts_summary():
    messages = history()
    before = messages[:2]
    latest = messages[-2:]
    recorded, usage, feed, requests = [], [], [], []
    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={'choices':[{'message':{'content':'Best score 20; trial A failed; try B.'}}],
                                        'usage':{'prompt_tokens':100,'completion_tokens':20}})
    session = NS(record=lambda *args:recorded.append(args), push=lambda *args:feed.append(args), stop=threading.Event())
    manager = NS(account=lambda s, counts:usage.append(counts))
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        assert api.compact_focus(client, 'http://mock', {}, 'model', messages, 10000, manager, session)
    assert messages[:2] == before and messages[-2:] == latest
    assert len(messages) == 5 and 'trial A failed' in messages[2]['content']
    assert usage and any(kind == 'context_compaction' for kind, _ in recorded)
    assert len(requests) == 1 and feed


def test_failed_compaction_keeps_history():
    messages = history()
    before = list(messages)
    session = NS(record=None)
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(503))) as client:
        assert not api.compact_focus(client, 'http://mock', {}, 'model', messages, 10000, NS(), session)
    assert messages == before


def test_focused_api_keeps_same_history_past_ordinary_step_limit(tmp_path, monkeypatch):
    import json
    import time

    from decompnet.app import agents, tools
    from tests.test_agents import _game
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, 'http://127.0.0.1:9/mcp')
    monkeypatch.setattr(manager, 'prepare_session', lambda *a, **kw:None)
    monkeypatch.setattr(manager, 'prompt', lambda s:'continuous task')
    monkeypatch.setattr(manager, 'app_instructions', lambda s:'rules')
    fn = net._function('f0', 'game')
    s = agents.Session('focused', 'api', 'focused', 'game', 'focused', {
        'focus_id':fn['id'], 'focus_function':'f0', 'focus_until':time.time()+60,
        'base_url':'http://mock/v1', 'model':'test', 'context_tokens':32768, 'max_steps':1})
    agent, _ = net.local_agent(s.agent_name)
    requests = []
    def provider(request):
        requests.append(json.loads(request.content))
        if len(requests) == 3:
            s.stop.set()
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','tool_calls':[
            {'id':str(len(requests)), 'type':'function','function':{'name':'project_info','arguments':'{}'}}]}}]})
    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw:real(transport=httpx.MockTransport(provider), **kw))
    monkeypatch.setattr(tools, 'call', lambda *args:'measured facts')
    assert api.API.run(manager, s, agent)
    assert len(requests) == 3
    assert len(requests[2]['messages']) > len(requests[0]['messages'])
    assert not s.rotated


def test_native_followup_turns_share_one_app_session(tmp_path, monkeypatch):
    import time

    from tests.test_agent_audit_followups import setup
    net, manager, s, agent = setup(tmp_path, monkeypatch)
    fn = net._function('f0', 'game')
    s.options.update(focus_id=fn['id'], focus_function=fn['name'], focus_until=time.time()+60)
    turns = []
    def provider_turn(session, *args):
        turns.append(session.session_id)
        session.options['focus_conversation'] = SID
        if len(turns) == 3:
            session.stop.set()
        return True
    monkeypatch.setattr(manager, '_run_cli', provider_turn)
    manager._run_work(s)
    assert len(turns) == 3 and len(set(turns)) == 1
    assert len(net.store.all('SELECT id FROM helper_sessions')) == 1
