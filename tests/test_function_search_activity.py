"""Nonexclusive jobs remain visible in Working without occupying a claim."""
import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app import deep_permuter, view_snapshots
from decompnet.app.engine import RomNetwork
from decompnet.app.function_searches import Searches
from decompnet.app.server import build
from decompnet.app.support import Jobs
from tests.test_app_server import PORT, SECRET, WINDOW
from tests.test_loading import tiny_game


@pytest.fixture
def searching(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    tiny_game(net)
    tiny_game(net,'other')
    jobs=Jobs()
    net.function_searches=Searches(jobs)
    net.function_searches['game','func_0']='job'
    jobs.jobs['job']={'state':'running','t':100}
    return net,jobs


def snapshot(net,slug='game'):
    with net.progress.view():
        payload=view_snapshots.map_payload(net,slug)
        rows=[dict(zip(payload['keys'],r)) for r in payload['rows']]
        return rows,net.live(slug)['claims']


@pytest.mark.parametrize('ending',['done','error','cancelled'])
def test_job_lifecycle_updates_working_maps_and_live_snapshots_without_claiming(searching,ending):
    net,jobs=searching
    rows,claims=snapshot(net)
    assert rows[0]['status']=='working' and rows[0]['agent']=='permuter'
    assert len(claims)==1 and claims[0]['name']=='func_0' and claims[0]['nonexclusive']
    assert not net.store.one('SELECT 1 FROM claims')
    assert not snapshot(net,'other')[1]
    assert snapshot(net,'other')[0][0]['status']=='open'
    jobs.jobs['job']['state']=ending
    rows,claims=snapshot(net)
    assert rows[0]['status']=='open' and rows[0]['agent'] is None and claims==[]
    jobs.jobs['job']['state']='running'
    assert snapshot(net)[0][0]['status']=='working'


def test_concurrent_claim_owner_is_preserved_and_matched_functions_stay_matched(searching):
    net,jobs=searching
    agent,_=net.local_agent('another-worker')
    fid=net.store.one("SELECT id FROM functions WHERE name='func_0' ORDER BY id LIMIT 1")['id']
    import time
    with net.store.tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id,claimed_at,expires_at) VALUES(?,?,?,?)',
                   (fid,agent.id,time.time(),time.time()+3600))
    rows,claims=snapshot(net)
    assert rows[0]['agent']=='another-worker'
    assert len(claims)==1 and claims[0]['agent']=='another-worker'
    jobs.jobs['job']['state']='done'
    assert snapshot(net)[0][0]['status']=='working'
    with net.store.tx() as db:
        db.execute('DELETE FROM claims WHERE function_id=?',(fid,))
        db.execute("UPDATE functions SET status='matched' WHERE id=?",(fid,))
    jobs.jobs['job']['state']='running'
    rows,claims=snapshot(net)
    assert rows[0]['status']=='matched' and claims==[]


def test_room_activity_is_local_and_does_not_duplicate_remote_claims(searching):
    net,_=searching
    def forbidden(*args):pytest.fail('activity must not contact the room host')
    net.links['game']=SimpleNamespace(fork=False,claims=[{'vram':0x100000,'agent':'room-worker'}],call=forbidden)
    with net.progress.view():
        rows=net.function_map('game')
    assert rows[0]['status']=='working' and rows[0]['agent']=='@room-worker'


def test_single_function_route_exposes_work_before_first_compile_and_reuses_the_job(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    tiny_game(net)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(void){return 1;}',best_score=50 WHERE name='func_0'")
    mcp,_,security,_=build(net,tmp_path,PORT,SECRET)
    net.autosave.close()
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    started=threading.Event();release=threading.Event();calls=[]
    def run(net,slug,name,*args,**kwargs):
        calls.append((slug,name));started.set()
        assert release.wait(10)
        return {'tested':0}
    monkeypatch.setattr(deep_permuter,'run',run)
    payload={'slug':'game','function':'func_0','minutes':1,'max_variants':10}
    try:
        job=client.post('/api/permuter/function',json=payload,headers={'X-Decomp':'1'}).json()
        assert 'job' in job and started.wait(5)
        assert client.post('/api/permuter/function',json=payload,headers={'X-Decomp':'1'}).json()==job
        live=client.get('/api/live?slug=game').json()
        assert live['claims'][0]['name']=='func_0' and live['claims'][0]['job']==job['job']
        assert calls==[('game','func_0')]
        assert not net.store.one('SELECT 1 FROM claims')
    finally:release.set()


def test_imported_function_without_address_appears_as_working(searching):
    import json
    net,_=searching
    with net.store.tx() as db:
        cfg=json.loads(db.execute("SELECT config FROM projects WHERE slug='game'").fetchone()['config'])
        cfg['kind']='fork'
        db.execute("UPDATE projects SET config=? WHERE slug='game'",(json.dumps(cfg),))
        db.execute("UPDATE functions SET vram=NULL,unit='src/example.c' WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    with net.progress.view():rows=net.function_map('game')
    assert rows[0]['name']=='func_0' and rows[0]['vram'] is None and rows[0]['status']=='working'
