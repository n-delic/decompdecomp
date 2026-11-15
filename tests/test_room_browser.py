"""Browser invites have member permissions, not the host's private GUI permissions."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app import collab
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import DAMAGE
from .test_rom import built  # noqa: F401


@pytest.fixture
def room(tmp_path):
    net = RomNetwork(tmp_path / 'd.db', tmp_path)
    cfg = {'rom': {'sha1': 'abc', 'title': 'Demo', 'game_code': 'NDQE', 'region': 'World'},
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': '-O2'}}
    with net.store.tx() as db:
        for pid, slug in [(1, 'shared'), (2, 'private')]:
            db.execute('INSERT INTO projects(id,slug,root,config,arch) VALUES(?,?,?,?,?)',
                       (pid, slug, str(tmp_path), json.dumps(cfg), 'mips'))
            db.execute('INSERT INTO functions(project_id,name,seed,asm,target,size,n_insns,unit) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, slug + '_fn', 'int work(void) { return 1; }', 'SECRET TARGET ASSEMBLY', b'SECRET', 4, 1, ''))
            db.execute('INSERT INTO context_versions(project_id,version,header) VALUES(?,1,?)', (pid, 'typedef int s32;'))
    net.rooms['shared'] = collab.Room(net, 'shared')
    code = net.rooms['shared'].open_invite()
    client = TestClient(collab.host_app(net, net.rooms))
    token, _ = net.rooms['shared'].join('alex', 'abc')
    return SimpleNamespace(net=net, room=net.rooms['shared'], code=code, client=client,
                           headers={'Authorization': f'Bearer {token}'})


def test_invite_serves_browser_without_private_controls(room):
    response = room.client.get('/join/' + room.code)
    assert response.status_code == 200 and 'Join in your browser' in response.text
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert room.client.get('/room/workspace.js').status_code == 200
    assert room.client.get('/room/secret.py').status_code == 404
    for path in ['/api/projects', '/api/agents', '/mcp', '/login']:
        assert room.client.get(path, headers=room.headers).status_code == 404


def test_tasks_are_room_scoped_saved_work_only_and_do_not_claim(room):
    for path in ['/collab/browser/functions', '/collab/browser/task?function=shared_fn']:
        assert room.client.get(path).status_code == 400
        response = room.client.get(path, headers=room.headers)
        assert response.status_code == 200
        assert 'SECRET' not in response.text and 'target' not in response.json()
    assert room.client.get('/collab/browser/task?function=private_fn', headers=room.headers).status_code == 400
    assert room.client.get('/collab/browser/task?room=private&function=private_fn', headers=room.headers).status_code == 400
    assert not room.net.store.one('SELECT 1 FROM claims')
    task = room.client.get('/collab/browser/task?function=shared_fn', headers=room.headers).json()
    assert task['header'] == 'typedef int s32;' and 'return 1' in task['source']


def test_approval_removal_and_invite_rotation_apply_to_browser(room):
    room.room.configure(approve=True)
    token, name = room.room.join('waiting', 'abc')
    headers = {'Authorization': 'Bearer ' + token}
    assert 'waiting' in room.client.get('/collab/browser/functions', headers=headers).json()['error']
    room.room.approve(name)
    assert room.client.get('/collab/browser/functions', headers=headers).status_code == 200
    room.room.remove(name)
    assert room.client.get('/collab/browser/functions', headers=headers).status_code == 400
    assert room.client.post('/collab/browser/compile', headers=headers,
                            json={'function': 'shared_fn', 'source': 'int work(void){return 1;}'}).status_code == 400
    room.room.open_invite()
    assert room.client.get('/collab/hello', params={'invite': room.code}).status_code == 400
    assert room.client.get('/collab/browser/functions', headers=room.headers).status_code == 200


@pytest.mark.parametrize('fork', [False, True])
def test_compile_dispatches_to_normal_engine_without_leaking_diff(room, monkeypatch, fork):
    # RomNetwork._attempt dispatches project builds to ForkEngine, ROM builds to Network.
    seen = []
    def attempt(agent, function, source, project):
        seen.append((agent.name, function, source, project))
        return {'score': 10, 'matched': False, 'text': 'SECRET TARGET ASSEMBLY'}
    if fork:
        # This test isolates engine dispatch; build-scope hashing is covered with real project trees.
        monkeypatch.setattr(room.net, '_remember_search_output', lambda *a:None)
        monkeypatch.setattr(room.net, 'fork_slug', lambda slug: slug)
        monkeypatch.setattr(room.net.forks, 'attempt', lambda agent, slug, name, source: attempt(agent, name, source, slug))
    else:
        from decompnet.network import Network
        monkeypatch.setattr(Network, 'attempt', lambda self, agent, name, source, project: attempt(agent, name, source, project))
    response = room.client.post('/collab/browser/compile', headers=room.headers,
                                json={'function': 'shared_fn', 'source': 'int work(void){return 2;}'})
    assert response.status_code == 200 and response.json()['score'] == 10, response.text
    assert 'SECRET' not in response.text
    assert seen == [('alex.browser', 'shared_fn', 'int work(void){return 2;}', 'shared')]


def test_browser_fork_task_uses_source_tree_not_disassembly(room, monkeypatch):
    room.room.fork = True
    monkeypatch.setattr(room.net.forks, 'get', lambda slug: (None, 'tree', None))
    monkeypatch.setattr(room.net.forks, '_current_source', lambda tree, fn: 'int latest(void){return 3;}')
    out = room.client.get('/collab/browser/task?function=shared_fn', headers=room.headers).json()
    assert 'latest' in out['source'] and not out['header'] and 'asm' not in out


def test_browser_worker_cannot_impersonate_an_existing_local_agent(room, monkeypatch):
    room.net.local_agent('alex.browser')
    def attempt(*args):
        pytest.fail('a room member must not act as an existing local worker')
    monkeypatch.setattr(room.net, 'attempt', attempt)
    out = room.client.post('/collab/browser/compile', headers=room.headers,
                           json={'function': 'shared_fn', 'source': 'int work(void){return 2;}'})
    assert out.status_code == 400 and 'another connection' in out.json()['error']


def test_browser_compile_is_bounded_and_busy_per_room(room, monkeypatch):
    calls = []
    def attempt(*args):
        nested = room.client.post('/collab/browser/compile', headers=room.headers,
                                  json={'function': 'shared_fn', 'source': 'int work(void){return 2;}'})
        assert nested.status_code == 400 and 'running' in nested.json()['error']
        calls.append(1)
        raise NetworkError('compiler unavailable')
    monkeypatch.setattr(room.net, 'attempt', attempt)
    for source in ['', 'x' * 65537, None]:
        assert room.client.post('/collab/browser/compile', headers=room.headers,
                                json={'function': 'shared_fn', 'source': source}).status_code == 400
    for _ in range(2):  # an error must release the build lock
        assert 'unavailable' in room.client.post('/collab/browser/compile', headers=room.headers,
                   json={'function': 'shared_fn', 'source': 'int work(void){return 2;}'}).json()['error']
    assert len(calls) == 2
    chunks = iter([b'{"source":"', b'x' * (collab.MAX_BODY + 1), b'"}'])
    assert room.client.post('/collab/browser/compile', headers=room.headers, content=chunks).status_code == 400


def test_real_browser_attempt_preserves_other_agents_claim_and_records_verified_match(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / 'real.db', tmp_path)
    slug = net.import_rom(built, confirmed=True, log=lambda *_: None)
    room = collab.Room(net, slug); room.open_invite()
    client = TestClient(collab.host_app(net, {slug: room}))
    token, _ = room.join('browserguest', hashlib.sha1(built).hexdigest())
    headers = {'Authorization': 'Bearer ' + token}
    agent, _ = net.local_agent('other')
    net.claim_row(agent, slug, 'func_80000808')
    payload = {'function': 'func_80000808', 'source': DAMAGE}
    assert client.post('/collab/browser/compile', headers=headers, json=payload).status_code == 400
    net.release(agent, 'func_80000808', '', slug)
    out = client.post('/collab/browser/compile', headers=headers, json=payload).json()
    assert out['matched'] and out['score'] == 0
    fn = net._function('func_80000808', slug)
    assert fn['status'] == 'matched' and fn['matched_source'] == DAMAGE
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id'])


def test_lan_hash_fallback_and_byte_order_normalization():
    import shutil
    if not shutil.which('node'):
        pytest.skip('needs node for browser hash checks')
    script = Path('src/decompnet/app/static/room/workspace.js').read_text().split('(async function workspace()')[0]
    check = """
const assert = require('node:assert/strict'), crypto = require('node:crypto');
for (const n of [0, 3, 55, 56, 63, 64, 1000]) {
 const bytes = new Uint8Array(n).fill(97);
 assert.equal(sha1(bytes), crypto.createHash('sha1').update(bytes).digest('hex'));
}
(async () => {
 const base = new Uint8Array([0x80,0x37,0x12,0x40,1,2,3,4]);
 const swaps = [base, new Uint8Array([0x37,0x80,0x40,0x12,2,1,4,3]), new Uint8Array([0x40,0x12,0x37,0x80,4,3,2,1])];
 for(const bytes of swaps) assert.equal(await gameDigest(new Blob([bytes])), sha1(base));
})().catch(e => {console.error(e);process.exit(1)});
"""
    subprocess.run(['node', '-e', script + check], check=True, capture_output=True, text=True)
