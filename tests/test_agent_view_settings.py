"""Settings shown in Your AI agents change the prompts and tools agents actually receive."""
import json

import pytest

from decompnet.app import prompt_settings, tool_policy, tools
from decompnet.app.agents import Manager, Session
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_prompt_overrides_validate_inherit_and_snapshot(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    manager = Manager(net, tmp_path, 'http://localhost/mcp')
    session = Session('1', 'codex', 'Reader', 'game', 'reader', {'per_session': 3})
    manager.sessions[session.id] = session
    monkeypatch.setattr(net, 'overview', lambda _: {
        'title': 'Synthetic game', 'kind': 'rom', 'platform': 'n64',
        'compiler': {'toolchain': 'gcc-2.7.2-kmc'},
    })
    prompt_settings.save_global(net, {'initial_rom': 'Global {title}: {count}',
                                      'instructions': 'Global instructions'})
    assert manager._prompt(session) == 'Global Synthetic game: 3'
    assert manager.app_instructions(session) == 'Global instructions'
    manager.set_prompts(session.id, {'initial_rom': 'Agent {title}: {count}'})
    manager._session_begin(session)
    manager.set_prompts(session.id, {'initial_rom': 'Changed {title}: {count}',
                                     'instructions': 'New instructions'})
    assert manager._prompt(session) == 'Agent Synthetic game: 3'
    assert manager.app_instructions(session) == 'Global instructions'
    manager._session_begin(session)
    assert manager._prompt(session) == 'Changed Synthetic game: 3'
    assert manager.app_instructions(session) == 'New instructions'
    for value in ({'unknown': 'x'}, {'initial_rom': '{bad}'},
                  {'initial_rom': '{title.__class__}'}, {'initial_rom': '{'}):
        with pytest.raises(NetworkError):
            prompt_settings.validate(value)
    assert prompt_settings.effective(net, {'prompts': {'initial_rom': '{removed}'}})['initial_rom'] == 'Global {title}: {count}'


def test_stopped_helper_keeps_its_prompts_and_disabled_tools_on_restart(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    manager = Manager(net, tmp_path, 'http://localhost/mcp')
    session = manager._record('game', 'codex', 'codex-1', 'Reader', {'model': 'first'})
    manager.set_prompts(session.id, {'instructions': 'Keep this'})
    manager.set_tool(session.id, 'guide', False)
    restarted = manager._record('game', 'codex', 'codex-1', 'Reader', {'model': 'second'})
    assert restarted is session
    assert restarted.options['prompts']['instructions'] == 'Keep this'
    assert 'guide' in restarted.options['disabled_tools']
    assert restarted.options['model'] == 'second'
    stored = json.loads(net.store.one('SELECT options FROM helpers WHERE id=?', session.id)['options'])
    assert stored == restarted.options


def test_tool_policy_applies_to_lists_and_calls_and_counts_usage(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('reader')
    manager = Manager(net, tmp_path, 'http://localhost/mcp')
    session = manager._record('game', 'codex', agent.name, 'Reader', {})
    manager.set_tool(session.id, 'guide', False)
    assert 'guide' not in tools.project_tools(net, 'game', agent)
    assert 'guide' in tools.project_tools(net, 'game', include_disabled=True)
    with pytest.raises(NetworkError, match='disabled'):
        tools._invoke(net, agent, 'game', 'guide', {'topic': 'mips'})
    manager.set_tool(session.id, 'guide', True)
    monkeypatch.setitem(tools.HANDLERS, 'guide', lambda *args, **kwargs: 'guide text')
    assert tools._invoke(net, agent, 'game', 'guide', {}) == 'guide text'
    assert net.store.one('SELECT calls FROM agent_tool_usage WHERE name=?', 'guide')['calls'] == 1
    tool_policy.set_global(net, 'guide', False)
    assert 'guide' not in tools.project_tools(net, 'game', agent)
    with pytest.raises(NetworkError):
        tool_policy.set_global(net, 'claim_function', False)
    with pytest.raises(NetworkError):
        tool_policy.preset_disabled('unknown')
    assert 'claim_function' not in tool_policy.preset_disabled('matching')
    row = net.store.one('SELECT options FROM helpers WHERE project=? AND name=?', 'game', agent.name)
    assert json.loads(row['options'])['disabled_tools'] == []


def test_agent_settings_routes_require_window_and_validate_changes(tmp_path):
    from starlette.testclient import TestClient

    from decompnet.app import window
    from decompnet.app.engine import RomNetwork
    from decompnet.app.server import build

    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'test.db', tmp_path)
    mcp, manager, security, _ = build(net, home, 6502, 'settings-secret')
    app = mcp.streamable_http_app(transport_security=security, host='127.0.0.1')
    url = 'http://127.0.0.1:6502'
    client = TestClient(app, base_url=url, headers={'User-Agent': window.TOKEN_AGENT + 'settings-secret'})
    outsider = TestClient(app, base_url=url)
    assert outsider.get('/api/agent-prompts').status_code == 401
    assert outsider.get('/api/agent-tools').status_code == 401
    headers = {'X-Decomp': '1'}
    assert client.post('/api/agent-prompts', json={'action': 'save'}, headers={}).status_code == 403
    saved = client.post('/api/agent-prompts', headers=headers,
                        json={'action': 'save', 'overrides': {'initial_rom': 'Try {title}'}})
    assert saved.status_code == 200 and saved.json()['overrides']['initial_rom'] == 'Try {title}'
    preview = client.post('/api/agent-prompts', headers=headers,
                          json={'action': 'preview', 'overrides': {'instructions': 'Preview only'}})
    assert preview.status_code == 200 and preview.json()['instructions'] == 'Preview only'
    assert 'instructions' not in client.get('/api/agent-prompts').json()['overrides']
    tools_data = client.get('/api/agent-tools').json()
    assert tools_data['tools'] and tools_data['presets']
    denied = client.post('/api/agent-tools', headers=headers,
                         json={'name': 'claim_function', 'enabled': False})
    assert denied.status_code == 400
    changed = client.post('/api/agent-tools', headers=headers,
                          json={'name': 'guide', 'enabled': False})
    assert changed.status_code == 200
    guide = next(t for t in changed.json()['tools'] if t['name'] == 'guide')
    assert guide['enabled'] is False
    reset = client.post('/api/agent-tools', headers=headers, json={'preset': 'full'})
    assert reset.status_code == 200
    assert next(t for t in reset.json()['tools'] if t['name'] == 'guide')['enabled'] is True
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('settings-game', 'Settings game', 'mips', str(tmp_path), '{}'))
    helper = manager._record('settings-game', 'codex', 'settings-helper', 'Settings helper', {})
    response = client.post('/api/agent-prompts', headers=headers,
                           json={'helper': helper.id, 'action': 'save',
                                 'overrides': {'instructions': 'Helper instructions'}})
    assert response.status_code == 200 and response.json()['overrides']['instructions'] == 'Helper instructions'
    response = client.post('/api/agent-tools', headers=headers,
                           json={'helper': helper.id, 'name': 'guide', 'enabled': False})
    assert response.status_code == 200
    guide = next(t for t in response.json()['tools'] if t['name'] == 'guide')
    assert guide['enabled'] is False
    assert client.get(f'/api/agent-tools?helper={helper.id}').status_code == 200
