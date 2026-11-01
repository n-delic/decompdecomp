"""Manual MCP credentials stay scoped, survive restart and can be revoked."""
import json

import pytest

from decompnet.app import manual_mcp
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Settings
from decompnet.network import NetworkError


def test_credentials_are_scoped_persistent_and_revocable(tmp_path):
    net = RomNetwork(tmp_path / 'test.db', tmp_path)
    settings = Settings(tmp_path / 'settings.json')
    with net.store.tx() as db:
        for slug in ('one', 'two'):
            db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                       (slug, slug, 'mips', str(tmp_path), '{}'))
    for slug in ('', 'missing'):
        with pytest.raises(NetworkError):
            manual_mcp.create(net, settings, slug)
    first = manual_mcp.create(net, settings, 'one')
    second = manual_mcp.create(net, settings, 'two')
    assert first['token'] not in settings.path.read_text()
    assert 'token' not in json.dumps(manual_mcp.connections(settings))
    agent = net.authenticate(first['token'])
    restored = Settings(settings.path)
    assert manual_mcp.project_for(net, restored, agent.id) == 'one'
    assert manual_mcp.project_for(net, restored, second['id']) == 'two'
    manual_mcp.revoke(restored, agent.id)
    assert manual_mcp.project_for(net, Settings(settings.path), agent.id) is None
    assert manual_mcp.project_for(net, restored, second['id']) == 'two'
    with net.store.tx() as db:
        db.execute('DELETE FROM projects WHERE slug=?', ('two',))
    assert manual_mcp.project_for(net, restored, second['id']) is None


def test_server_resolves_manual_credentials_and_rejects_revoked_tokens(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from starlette.testclient import TestClient

    from decompnet.app import tools, window
    from decompnet.app.server import build

    resolve = []
    original = tools.build_mcp

    def capture(net, agent_of, **kwargs):
        resolve.append(agent_of)
        return original(net, agent_of, **kwargs)

    monkeypatch.setattr(tools, 'build_mcp', capture)
    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'test.db', tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('game', 'Game', 'mips', str(tmp_path), '{}'))
    mcp, _, security, _ = build(net, home, 6501, 'manual-test-secret')
    client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url='http://127.0.0.1:6501',
                        headers={'User-Agent': window.TOKEN_AGENT + 'manual-test-secret', 'X-Decomp': '1'})
    connection = client.post('/api/manual-mcp', json={'action': 'create', 'slug': 'game'}).json()
    context = SimpleNamespace(headers={'Authorization': 'Bearer ' + connection['token']})
    agent, slug = resolve[0](context)
    assert agent.id == connection['id'] and slug == 'game'
    with pytest.raises(NetworkError):
        resolve[0](SimpleNamespace(headers={'Authorization': 'Bearer invalid'}))
    assert client.post('/api/manual-mcp', json={'action': 'revoke', 'id': agent.id}).status_code == 200
    with pytest.raises(NetworkError, match='no longer assigned'):
        resolve[0](context)
