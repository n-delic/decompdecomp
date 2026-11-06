"""Repair example review/export is available only through the local app's guarded routes."""
import json

import pytest
from starlette.testclient import TestClient

from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

from .test_app_server import PORT, SECRET, WINDOW


@pytest.fixture
def repair_app(tmp_path):
    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'app.db', tmp_path)
    mcp, manager, security, _ = build(net, home, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url=f'http://127.0.0.1:{PORT}', headers=WINDOW)
    return client, net, manager


def test_guarded_review_and_source_opt_in_export(repair_app, monkeypatch):
    client, net, manager = repair_app
    monkeypatch.setattr(net, 'on_match', lambda *a: None)
    agent, _ = net.local_agent('feedback-test')
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('feedback-demo', 'Feedback demo', '/unused', '{}')).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,unit,status,n_insns,size,target) VALUES(?,?,?,?,?,?,?)',
                         (pid, 'target', '', 'open', 1, 4, b'test')).lastrowid
    current = lambda: net.store.one('SELECT * FROM functions WHERE id=?', fid)
    net._record(current(), agent, 9, 'private before code', 0)
    net._record(current(), agent, 0, 'private matching code', 0, matched=True)
    url = '/api/repair-examples?slug=feedback-demo'
    assert client.get(url, headers={'User-Agent': 'outside'}).status_code == 401
    listing = client.get(url).json()
    case = listing['examples'][0]['id']
    detail = client.get(url + f'&id={case}').json()
    assert detail['evidence']['after']['source'] == 'private matching code'
    assert client.get('/api/repair-examples').status_code == 400
    assert client.get('/api/repair-examples?all=1').status_code == 200
    assert client.get(url + '&category=invalid').status_code == 400
    posted = {'slug': 'feedback-demo', 'id': case, 'status': 'reviewed'}
    assert client.post('/api/repair-examples', json=posted).status_code == 403
    assert client.post('/api/repair-examples', json=posted, headers={'X-Decomp': '1'}).status_code == 200
    summary = client.get('/api/repair-examples/export?slug=feedback-demo')
    assert 'private matching code' not in summary.text and 'private before code' not in summary.text
    assert summary.headers['content-disposition'].endswith('"repair-examples.json"')
    full = client.get('/api/repair-examples/export?slug=feedback-demo&include_source=1')
    assert json.loads(full.text)['examples'][0]['evidence']['after']['source'] == 'private matching code'
    assert client.get('/api/repair-examples/export?slug=feedback-demo', headers={'User-Agent': 'outside'}).status_code == 401


def test_investigation_route_requires_write_guard_and_scoped_example(repair_app, monkeypatch):
    from decompnet.app import repair_investigations
    client, net, _ = repair_app
    monkeypatch.setattr(repair_investigations, 'choices', lambda: [{'id': 'codex', 'name': 'Codex CLI', 'installed': True}])
    launched = []
    monkeypatch.setattr(repair_investigations, 'launch', lambda home, row, kind, log: launched.append(row['id']) or {'repository': 'scratch'})
    monkeypatch.setattr(net, 'on_match', lambda *a: None)
    agent, _ = net.local_agent('feedback-test')
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('feedback-demo', 'Demo', '/unused', '{}')).lastrowid
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   ('other', 'Other', '/unused', '{}'))
        fid = db.execute('INSERT INTO functions(project_id,name,unit,status,n_insns,size,target) VALUES(?,?,?,?,?,?,?)',
                         (pid, 'target', '', 'open', 1, 4, b'test')).lastrowid
    f = net.store.one('SELECT * FROM functions WHERE id=?', fid)
    net._record(f, agent, 0, 'private matching code', 0, matched=True)
    case = net.repair_feedback.listing('feedback-demo')['examples'][0]['id']
    url = '/api/repair-examples/investigate'
    assert client.get(url, headers={'User-Agent': 'outside'}).status_code == 401
    assert client.get(url).json()['agents'][0]['id'] == 'codex'
    body = {'id': case, 'slug': 'feedback-demo', 'agent': 'codex'}
    assert client.post(url, json=body).status_code == 403
    headers = {'X-Decomp': '1'}
    assert client.post(url, json=body | {'slug': 'other'}, headers=headers).status_code == 400
    assert client.post(url, json=body | {'slug': ''}, headers=headers).status_code == 400
    assert client.post(url, json=body | {'agent': 'unknown'}, headers=headers).status_code == 400
    assert not launched
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 200 and response.json()['job']
    # The guarded route starts preparation in a job, never blocks the page until an agent completes.
    import time
    for _ in range(100):
        if launched:
            break
        time.sleep(.01)
    assert launched == [case]


def test_quality_review_is_guarded_scoped_and_rejects_a_stale_source(repair_app):
    import hashlib

    from decompnet import candidate_quality
    client, net, _ = repair_app
    agent,_=net.local_agent('quality-test')
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',('quality','Quality','/unused','{}')).lastrowid
        fid=db.execute('INSERT INTO functions(project_id,name,unit,status,n_insns,size) VALUES(?,?,?,?,?,?)',
                       (pid,'f','','open',1,4)).lastrowid
    net.claim_row(agent,'quality','f');fn=net._function('f','quality')
    source='int f(){int x;return x;}'
    net._record(fn,agent,10,source,0)
    net._record(fn,agent,20,'int f(){return 1;}',0)
    net.release(agent,'f','Historical candidate uses an uninitialized value.','quality')
    url='/api/repair-examples/quality'
    assert client.get(url+'?slug=quality',headers={'User-Agent':'outside'}).status_code==401
    assert client.get(url).status_code==400
    reports=client.get(url+'?slug=quality').json()['reports']
    assert len(reports)==1 and reports[0]['function']=='f'
    assert client.get(url+'?slug=quality&function=f').json()['evidence'].find('return 1')>=0
    data={'slug':'quality','function':'f','source_hash':hashlib.sha256(source.encode()).hexdigest()}
    assert client.post(url,json=data).status_code==403
    assert client.post(url,json=data|{'source_hash':'stale'},headers={'X-Decomp':'1'}).status_code==400
    assert client.post(url,json=data,headers={'X-Decomp':'1'}).status_code==200
    assert not candidate_quality.pending(net,net._function('f','quality'))
    assert client.post(url,json=data,headers={'X-Decomp':'1'}).status_code==400
    assert net.store.one('SELECT best_score FROM functions WHERE id=?',fid)['best_score']==10


def test_classify_route_is_project_scoped_and_write_guarded(repair_app):
    client, net, _ = repair_app
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES('review-only','Demo','/unused','{}')")
    url = '/api/repair-examples/classify'
    assert client.get(url).status_code == 400
    assert client.get(url+'?slug=review-only',headers={'User-Agent':'outside'}).status_code == 401
    assert client.get(url+'?slug=review-only').json()['applied'] is False
    assert client.post(url,json={'slug':'review-only'}).status_code == 403
    assert client.post(url,json={'slug':'review-only'},headers={'X-Decomp':'1'}).json()['applied'] is True


def test_replay_is_guarded_scoped_and_cancellable(repair_app,monkeypatch):
    import threading
    import time

    from decompnet.app import repair_replay_jobs
    client,net,_=repair_app
    seen=[];entered=threading.Event()
    monkeypatch.setattr(net.repair_feedback,'listing',lambda slug,*args,**kwargs: {'examples':[{'id':42,'slug':'demo','function':'f'},{'id':43,'slug':'demo','function':'f'}]})
    def run(feedback,slug,ids,trials,seconds,log,**options):
        seen.append((slug,ids,trials,seconds));entered.set()
        for _ in range(100):
            log.check_cancel();time.sleep(.01)
        return {'examples':[]}
    monkeypatch.setattr(repair_replay_jobs,'run',run)
    url='/api/repair-examples/replay';data={'slug':'demo'};headers={'X-Decomp':'1'}
    assert client.post(url,json=data).status_code==403
    assert client.post(url,json={},headers=headers).status_code==400
    assert client.post(url,json=data|{'trials':True},headers=headers).status_code==400
    assert client.post(url,json=data|{'seconds':121},headers=headers).status_code==400
    assert client.post(url,json=data|{'diagnostics':'yes'},headers=headers).status_code==400
    assert client.post(url,json=data|{'minimize':1},headers=headers).status_code==400
    assert client.post(url,json=data|{'combinations':'yes'},headers=headers).status_code==400
    assert client.post(url,json=data|{'combinations':True,'minimize':True},headers=headers).status_code==400
    response=client.post(url,json=data,headers=headers)
    assert response.status_code==200 and entered.wait(2)
    assert seen==[('demo',[42],12,30)]
    assert client.post(url,json=data,headers=headers).status_code==400
    job=response.json()['job']
    assert client.post('/api/job/cancel',json={'id':job},headers=headers).status_code==200
    for _ in range(100):
        state=client.get('/api/job?id='+job).json()
        if state['state']!='running':break
        time.sleep(.01)
    assert state['state']=='cancelled'


def test_automatic_analysis_setting_is_guarded_and_boolean(repair_app):
    client,_,_=repair_app
    url='/api/repair-examples/analysis'
    assert client.get(url,headers={'User-Agent':'outside'}).status_code==401
    assert client.get(url).json()['enabled'] is True
    assert client.post(url,json={'enabled':False}).status_code==403
    assert client.post(url,json={'enabled':'false'},headers={'X-Decomp':'1'}).status_code==400
    response=client.post(url,json={'enabled':False},headers={'X-Decomp':'1'})
    assert response.status_code==200 and response.json()['enabled'] is False
    assert client.get(url).json()['enabled'] is False
