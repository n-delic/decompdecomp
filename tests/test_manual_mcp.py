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
    manual_mcp.revoke(net, restored, agent.id)
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


def test_revoke_frees_only_its_connection_claims_and_keeps_attempts(tmp_path):
    import time
    net=RomNetwork(tmp_path/'test.db',tmp_path);settings=Settings(tmp_path/'settings.json')
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(1,'one','one','mips',?,'{}')",(str(tmp_path),))
        for fid in (1,2):db.execute("INSERT INTO functions(id,project_id,name) VALUES(?,1,?)",(fid,'f'+str(fid)))
    own=manual_mcp.create(net,settings,'one');other=manual_mcp.create(net,settings,'one')
    with net.store.tx() as db:
        for fid,aid in ((1,own['id']),(2,other['id'])):db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,aid,time.time(),time.time()+1800))
        db.execute('INSERT INTO attempts(function_id,agent_id,score,source) VALUES(1,?,10,?)',(own['id'],'saved C'))
    manual_mcp.revoke(net,settings,own['id'])
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=1')
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=2')['agent_id']==other['id']
    assert net.store.one('SELECT source FROM attempts')['source']=='saved C'
    with pytest.raises(NetworkError,match='revoked'):
        with net.attempt_turn(net.authenticate(own['token']),net.store.one('SELECT * FROM functions WHERE id=1')):pass
    manual_mcp.revoke(net,settings,other['id']+100)
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=2')


def test_revocation_drains_an_inflight_attempt_before_releasing_its_claim(tmp_path):
    import threading
    import time
    net=RomNetwork(tmp_path/'test.db',tmp_path);settings=Settings(tmp_path/'settings.json')
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(1,'one','one','mips',?,'{}')",(str(tmp_path),))
        db.execute("INSERT INTO functions(id,project_id,name) VALUES(1,1,'f')")
    connection=manual_mcp.create(net,settings,'one');agent=net.authenticate(connection['token'])
    with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(1,?,?,?)',(agent.id,time.time(),time.time()+1800))
    fn=net.store.one('SELECT * FROM functions WHERE id=1')
    entered=threading.Event();finish=threading.Event();revoked=threading.Event()
    def compile_attempt():
        with net.attempt_turn(agent,fn):
            entered.set()
            assert finish.wait(5)
            with net.store.tx() as db:
                db.execute('INSERT INTO attempts(function_id,agent_id,score,source) VALUES(1,?,10,?)',(agent.id,'finished before release'))
    def revoke():
        manual_mcp.revoke(net,settings,agent.id);revoked.set()
    compiler=threading.Thread(target=compile_attempt);compiler.start();assert entered.wait(5)
    worker=threading.Thread(target=revoke);worker.start()
    try:
        assert not revoked.wait(.05)
        assert net.store.one('SELECT 1 FROM claims WHERE function_id=1')
    finally:
        finish.set();compiler.join(5);worker.join(5)
    assert revoked.is_set() and not net.store.one('SELECT 1 FROM claims WHERE function_id=1')
    assert net.store.one('SELECT source FROM attempts')['source']=='finished before release'
    with pytest.raises(NetworkError,match='revoked'):net.claim_row(agent,'one','f')
