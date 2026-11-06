"""Permutations improve shared work without taking ownership from agents."""
import time
from types import SimpleNamespace

import pytest

from decompnet import permuter_claims
from decompnet.app import collab, room_matches
from decompnet.app.engine import RomNetwork
from decompnet.app.permute import Permuter
from decompnet.app.permuter_worker import PermuterWorker
from tests.test_app_engine import DAMAGE
from tests.test_claims import _game
from tests.test_rom import built  # noqa: F401
from tests.test_room_match_profiles import pair  # noqa: F401


@pytest.mark.parametrize('name,owner,expected', [('permuter',None,True), ('permuter-123',None,True),
    ('client.permuter-123','client',True), ('permuter-helper',None,False),
    ('other.permuter-1','client',False), ('client.claude-1','client',False), ('autopilot',None,False)])
def test_only_internal_worker_labels_are_nonexclusive(name, owner, expected):
    assert permuter_claims.nonexclusive(SimpleNamespace(name=name,owner=owner)) is expected


@pytest.mark.parametrize('worker', [Permuter, PermuterWorker])
def test_permutation_hold_does_not_change_an_agents_lease(tmp_path, worker):
    net, _ = _game(tmp_path, [('f',10,'open')])
    owner,_=net.local_agent('coding-agent')
    net.claim_row(owner,'game','f')
    fn=net._function('f','game')
    claim=dict(net.store.one('SELECT * FROM claims'))
    agent,_=net.local_agent('permuter-1')
    search=worker(net)
    assert search._hold(agent,fn)
    assert dict(net.store.one('SELECT * FROM claims')) == claim
    search._let_go(agent,fn)
    assert dict(net.store.one('SELECT * FROM claims')) == claim
    assert net._function('f','game')['permuted'] == 1


def test_startup_removes_legacy_permuter_leases_only(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open'),('g',20,'open'),('h',30,'open')])
    with net.store.tx() as db:
        for name,owner,fn in [('permuter-1',None,'f'), ('member.permuter-2','member','g'), ('permuter-helper',None,'h')]:
            aid=db.execute('INSERT INTO agents(name,owner,token_hash,created_at) VALUES(?,?,?,?)',
                           (name,owner,'test:'+name,time.time())).lastrowid
            fid=db.execute('SELECT id FROM functions WHERE name=?',(fn,)).fetchone()['id']
            db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,aid,time.time(),time.time()+3600))
    reopened=RomNetwork(tmp_path/'app.db',tmp_path)
    assert [row['name'] for row in reopened.store.all('SELECT a.name FROM claims c JOIN agents a ON a.id=c.agent_id')] == ['permuter-helper']
    assert not reopened.store.one('SELECT 1 FROM functions WHERE abandons>0')


def test_room_permutation_near_miss_and_exact_submission_preserve_work_until_verified(pair,monkeypatch):  # noqa: F811
    from starlette.testclient import TestClient
    host,slug,guest,gslug=pair
    monkeypatch.setattr(host,'on_match',lambda *args:None)
    owner,_=host.local_agent('coding-agent')
    fn=host.claim_row(owner,slug,'func_80000808')
    original=dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id']))
    room=collab.Room(host,slug);host.rooms[slug]=room
    token,member=room.join('client',host.cfg(host.project_row(slug))['rom']['sha1'])
    client=TestClient(collab.host_app(host,host.rooms))
    headers={'authorization':'Bearer '+token}
    agent='permuter-123'
    body={'room':slug,'agent':agent,'function':fn['name']}
    writes=[]
    host.store.conn.set_trace_callback(lambda sql:writes.append(sql) if 'INSERT INTO CLAIMS' in sql.upper() else None)
    try:
        for _ in range(2):
            response=client.post('/collab/claim',json=body,headers=headers)
            assert response.status_code == 200,response.text
            assert response.json()['function'] == fn['name']
            assert response.json()['claim_token'] == ''
        with host.store.tx() as db:db.execute('UPDATE functions SET best_score=1000 WHERE id=?',(fn['id'],))
        evidence=room_matches.packet(guest,gslug,fn['name'],DAMAGE)
        response=client.post('/collab/best',json=body|{'source':DAMAGE,'score':20,'evidence':evidence},headers=headers)
        assert response.status_code == 200,response.text
        assert host._function(fn['name'],slug)['best_score'] == 20
        assert dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id'])) == original
        response=client.post('/collab/release',json=body,headers=headers)
        assert response.status_code == 200,response.text
        assert dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id'])) == original
        response=client.post('/collab/attempt',json=body|{'source':DAMAGE,'evidence':evidence},headers=headers)
        assert response.status_code == 200,response.text
        assert response.json()['matched'],response.json()
        assert host._function(fn['name'],slug)['status'] == 'matched'
        assert not host.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
        assert not writes  # even host verification never creates a permutation reservation
    finally:host.store.conn.set_trace_callback(None)


def test_local_rom_permuter_saves_judged_improvements_without_taking_a_claim(pair,monkeypatch):  # noqa: F811
    host,slug,_,_=pair
    monkeypatch.setattr(host,'on_match',lambda *args:None)
    owner,_=host.local_agent('coding-agent')
    fn=host.claim_row(owner,slug,'func_80000808')
    original=dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id']))
    with host.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10000 WHERE id=?',
                   (DAMAGE.replace('= 60;','= 65;'),fn['id']))
    agent,_=host.local_agent('permuter-123')
    search=Permuter(host)
    fn=host._function(fn['name'],slug);proj=host.project_row(slug)
    source=DAMAGE.replace('= 60;','= 61;')
    score=search._score(agent,proj,fn,source,10000,None)
    assert 0 < score < 10000
    assert host._function(fn['name'],slug)['best_source'] == source
    assert dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id'])) == original
    assert search._score(agent,host.project_row(slug),host._function(fn['name'],slug),DAMAGE,score,None) == 0
    assert host._function(fn['name'],slug)['status'] == 'matched'
    assert not host.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_failed_room_permutation_verification_keeps_another_agents_claim(pair,monkeypatch):  # noqa: F811
    host,slug,guest,gslug=pair
    owner,_=host.local_agent('coding-agent')
    fn=host.claim_row(owner,slug,'func_80000808')
    claim=dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id']))
    agent,_=host.local_agent('permuter-123')
    monkeypatch.setattr(host,'_record',lambda *a,**k:pytest.fail('invalid C must not be recorded as exact'))
    source='this is not C;'
    result=room_matches.verify(host,slug,agent,fn['name'],source,room_matches.packet(guest,gslug,fn['name'],source))
    assert not result['matched']
    assert host._function(fn['name'],slug)['status'] == 'open'
    assert dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id'])) == claim


def test_nonexclusive_exact_submission_rejects_changed_header(pair,monkeypatch):  # noqa: F811
    host,slug,guest,gslug=pair
    owner,_=host.local_agent('coding-agent')
    fn=host.claim_row(owner,slug,'func_80000808')
    claim=dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id']))
    agent,_=host.local_agent('permuter-123')
    compile=host._compile
    def changed(*args):
        result=compile(*args)
        with host.store.tx() as db:
            ctx=host._context(fn['project_id'],fn['unit'])
            db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)',
                       (fn['project_id'],host._next_version(db,fn['project_id']),ctx['header']+'\n/* new header */',fn['unit']))
        return result
    monkeypatch.setattr(host,'_compile',changed)
    result=room_matches.verify(host,slug,agent,fn['name'],DAMAGE,room_matches.packet(guest,gslug,fn['name'],DAMAGE))
    assert not result['matched']
    assert host._function(fn['name'],slug)['status'] == 'open'
    assert dict(host.store.one('SELECT * FROM claims WHERE function_id=?',fn['id'])) == claim
