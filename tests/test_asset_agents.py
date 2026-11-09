"""Advisory discovery uses original synthetic bytes, never build or matching mutations."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet.app import asset_agents as discovery
from decompnet.app import rom_explorer, tools
from decompnet.network import NetworkError

from .test_rom_explorer import Log, project  # noqa: F401


@pytest.fixture
def discovered(project):  # noqa: F811
    net,pid,root=project
    rom_explorer.run(net,'game',Log())
    run=discovery.prepare(net,'game',{'mode':'range','start':8,'end':128})
    agent,_=net.local_agent('asset-test')
    session=SimpleNamespace(id='1',slug='game',label='Asset tester',model='test-model',stop=threading.Event(),options={'task':'assets','asset_run':run})
    discovery.bind(net,session,agent)
    return net,pid,root,run,agent,session


def call(net,agent,name,**args):
    result=tools.call(net,agent,'game',name,args)
    assert not result.startswith('error:'),result
    return json.loads(result)


def proposal(packet):
    row=packet['region']
    return dict(name='message_text',kind='strings',confidence='tentative',evidence='Printable bytes at this ROM offset.',counter='No text-rendering call has been proven.',uncertainty='It may be metadata rather than displayed text.',approach='Inspect ASCII and original references',citations=[dict(offset=row['start'])],start=row['start'],end=row['end'],format_notes='NUL-terminated ASCII')


def test_worker_scope_claim_recovery_proposal_review_and_no_judging(discovered,monkeypatch):
    net,pid,root,run,agent,session=discovered
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Data discovery must not synchronize code'))
    before=[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    packet=call(net,agent,'claim_asset_region');again=call(net,agent,'claim_asset_region')
    assert packet['claim_token']==again['claim_token']
    assert 'error:' in tools.call(net,agent,'game','rename_symbol',{'old':'reader','new':'changed'})
    assert 'error:' in tools.call(net,agent,'game','claim_function',{})
    result=call(net,agent,'propose_asset_region',region=packet['region']['id'],claim_token=packet['claim_token'],proposal=proposal(packet))
    assert not rom_explorer._read(net,net.store.one('SELECT * FROM projects WHERE id=?',pid))['annotations']
    read=discovery.review(net,'game',result['proposal'])
    discovery.review(net,'game',result['proposal'],'accept',annotation_version=read['annotation_version'])
    note=rom_explorer._read(net,net.store.one('SELECT * FROM projects WHERE id=?',pid))['annotations'][packet['region']['id']]
    assert note['name']=='message_text' and note['kind']=='strings' and note['discovery']['author']=='Asset tester'
    assert [tuple(r) for r in net.store.all('SELECT * FROM functions')]==before
    with pytest.raises(NetworkError,match='already reviewed'):discovery.review(net,'game',result['proposal'],'reject')


def test_two_workers_get_unique_claims_and_bad_tokens_cannot_submit(discovered):
    net,pid,root,run,agent,session=discovered
    other,_=net.local_agent('asset-other');second=SimpleNamespace(**{**session.__dict__,'id':'2','label':'Other'})
    discovery.bind(net,second,other)
    with ThreadPoolExecutor(2) as pool:packets=list(pool.map(lambda a:json.loads(discovery.claim(net,a,'game')),[agent,other]))
    assert len({p['region']['id'] for p in packets})==2
    first=packets[0]
    with pytest.raises(NetworkError,match='expired'):discovery.propose(net,other,'game',first['region']['id'],first['claim_token'],proposal(first))


def test_deferrals_history_completion_resume_and_expired_leases(discovered):
    net,pid,root,run,agent,session=discovered
    packet=json.loads(discovery.claim(net,agent,'game'));identity=packet['region']['id']
    discovery.defer(net,agent,'game',identity,packet['claim_token'],'Try indexed textures','No coherent palette found; try text references next.')
    while True:
        packet=json.loads(discovery.claim(net,agent,'game'))
        if packet.get('done'):break
        discovery.defer(net,agent,'game',packet['region']['id'],packet['claim_token'],'Inspect raw bytes','No convincing format; no type proposed.')
    assert discovery.status(net,'game')['run']['state']=='complete'
    newer=discovery.prepare(net,'game',{'mode':'range','start':8,'end':128});session.options['asset_run']=newer
    packet=json.loads(discovery.claim(net,agent,'game'));assert packet['previous_attempts']
    assert packet['previous_attempts'][0]['body']['findings']
    store=net.project_store(pid)
    with store.tx() as db:db.execute('UPDATE asset_queue SET lease=0 WHERE run=?',(newer,))
    recovered=json.loads(discovery.claim(net,agent,'game'));assert recovered['claim_token']!=packet['claim_token']
    manager=SimpleNamespace(sessions={},stop=lambda *a:None)
    discovery.stop(net,'game',manager)
    with pytest.raises(NetworkError,match='stopped'):discovery.claim(net,agent,'game')
    with pytest.raises(NetworkError,match='explicitly'):discovery.prepare(net,'game',identity=newer)
    discovery.prepare(net,'game',identity=newer,resume=True)
    assert json.loads(discovery.claim(net,agent,'game'))['region']


def test_proposals_validate_citations_boundaries_previews_and_concurrent_notes(discovered):
    net,pid,root,run,agent,session=discovered
    packet=json.loads(discovery.claim(net,agent,'game'));args=(net,agent,'game',packet['region']['id'],packet['claim_token'])
    with pytest.raises(NetworkError,match='Read the cited'):discovery.propose(*args,{**proposal(packet),'citations':[{'offset':100000}]})
    with pytest.raises(NetworkError,match='functions'):discovery.propose(*args,{**proposal(packet),'kind':'code'})
    with pytest.raises(NetworkError,match='overlap'):discovery.propose(*args,{**proposal(packet),'start':100,'end':101})
    result=json.loads(discovery.propose(*args,proposal(packet)));review=discovery.review(net,'game',result['proposal'])
    with net.progress.view():rom_explorer.annotate(net,'game',dict(region=packet['region']['id'],image_hash=rom_explorer.listing(net,'game')['image_hash'],name='Human label'))
    with pytest.raises(NetworkError,match='changed during review'):discovery.review(net,'game',result['proposal'],'accept',annotation_version=review['annotation_version'])
    (root/'game.rom').write_bytes(bytes(128))
    assert discovery.review(net,'game',result['proposal'])['stale']
    with pytest.raises(NetworkError,match='older image'):discovery.review(net,'game',result['proposal'],'accept',annotation_version=review['annotation_version'])


def test_tool_catalogues_and_api_schema_match_task_scope(discovered):
    from decompnet.agents.api import api_tool_schemas
    net,pid,root,run,agent,session=discovered
    names=tools.project_tools(net,'game',agent)
    assert set(names)==discovery.TOOLS
    schemas=api_tool_schemas(names=names)
    assert {s['function']['name'] for s in schemas}==discovery.TOOLS
    assert not discovery.TOOLS.intersection(tools.tool_names(True)) - {'project_info','get_assembly','guide'}
    assert 'Read previous_attempts' in discovery.prompt(session)


def test_normal_worker_lifecycle_processes_entire_queue_without_matching(discovered,tmp_path,monkeypatch):
    from decompnet.app import agents
    from tests.test_agents import URL, _no_agent_settings
    net,pid,root,run,agent,session=discovered
    _no_agent_settings(monkeypatch)
    monkeypatch.setattr(agents.Manager,'_launch',lambda *a:None)
    manager=agents.Manager(net,tmp_path,URL)
    monkeypatch.setattr(manager,'_prepare',lambda *a:pytest.fail('No matching preparation'))
    monkeypatch.setattr(manager,'_build_problem',lambda *a:pytest.fail('No build needed'))
    sid=manager.start('codex','game',options={'task':'assets','asset_run':run,'per_session':1})[0];worker=manager.sessions[sid]
    turns=[]
    def cli(session,token,workdir):
        current_agent=net.authenticate(token);turns.append(True);manager.prepare_session(session,current_agent)
        assert 'No function has been claimed' in session.startup_context
        assert 'claim_asset_region' in manager.prompt(session)
        packet=json.loads(tools.call(net,current_agent,'game','claim_asset_region',{}))
        result=tools.call(net,current_agent,'game','defer_asset_region',dict(region=packet['region']['id'],claim_token=packet['claim_token'],approach='Check imported evidence',findings='Unresolved; another pass should try palettes.'))
        assert not result.startswith('error:'),result
        return True
    monkeypatch.setattr(manager,'_run_cli',cli)
    monkeypatch.setattr(agents,'SESSION_GAP',0)
    manager._run_work(worker)
    state=discovery.status(net,'game')
    assert len(turns)==state['run']['total'] and state['run']['state']=='complete' and worker.state=='stopped'
    assert not net.store.all('SELECT * FROM claims') and not net.store.all('SELECT * FROM attempts')
    assert all(r['compiles']==0 and r['task']=='assets' for r in net.store.all('SELECT * FROM helper_sessions'))


def test_other_project_runs_and_reviews_are_isolated(discovered):
    net,pid,root,run,agent,session=discovered
    packet=json.loads(discovery.claim(net,agent,'game'))
    finding=json.loads(discovery.propose(net,agent,'game',packet['region']['id'],packet['claim_token'],proposal(packet)))['proposal']
    proj=net.store.one('SELECT * FROM projects WHERE id=?',pid)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',('other','Other','mips',1,proj['root'],proj['config'])).lastrowid
    assert discovery.status(net,'other')['run'] is None
    with pytest.raises(NetworkError,match='Choose a saved'):discovery.review(net,'other',finding)
    with pytest.raises(NetworkError):discovery.prepare(net,'other',identity=run)
    discovery.stop(net,'other',SimpleNamespace(sessions={}))
    assert discovery.status(net,'game')['run']['state']=='active'


def test_proposals_and_progress_survive_restart_and_dead_claims_requeue(discovered):
    net,pid,root,run,agent,session=discovered
    packet=json.loads(discovery.claim(net,agent,'game'))
    with net.project_store(pid).tx() as db:db.execute("UPDATE asset_queue SET instance='previous-app' WHERE run=?",(run,))
    net.__dict__.pop('_asset_discovery_stores',None)
    state=discovery.status(net,'game')
    assert state['run']['counts']['queued']==state['run']['total']
    recovered=json.loads(discovery.claim(net,agent,'game'))
    assert recovered['claim_token']!=packet['claim_token']


def test_discovery_routes_require_window_write_access_and_do_not_recheck(discovered,monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW
    net,pid,root,run,agent,session=discovered
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Discovery routes must not recheck saved code'))
    with net.progress.view():mcp,manager,security,_=build(net,root/'.decomp',PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    path='/api/rom-explorer/discovery';request=dict(slug='game',action='stop')
    assert client.get(path+'?slug=game').json()['run']['id']==run
    assert client.post(path,json=request).status_code==403
    assert client.post(path,json=request,headers={'User-Agent':'outside','X-Decomp':'1'}).status_code==401
    assert client.post(path,json=request,headers={'X-Decomp':'1'}).status_code==200
    assert client.get(path+'?slug=game').json()['run']['state']=='paused'
    assert client.post(path,json=dict(slug='game',action='resume',run=run),headers={'X-Decomp':'1'}).status_code==200


def test_advisory_subrange_acceptance_never_reclassifies_whole_region(discovered):
    net,pid,root,run,agent,session=discovered;packet=json.loads(discovery.claim(net,agent,'game'))
    row=packet['region'];body=proposal(packet);body['end']=row['end']-1
    result=json.loads(discovery.propose(net,agent,'game',row['id'],packet['claim_token'],body))
    read=discovery.review(net,'game',result['proposal'])
    discovery.review(net,'game',result['proposal'],'accept',edits={'name':'edited_name'},annotation_version=read['annotation_version'])
    with net.progress.view():note=rom_explorer.detail(net,'game',row['id'],read['image_hash'])['annotation']
    assert note['name']=='edited_name' and not note['kind'] and note['discovery']['end']==row['end']-1


def test_all_findings_are_pageable_and_rejected_attempts_reappear_in_history(discovered):
    net,pid,root,run,agent,session=discovered
    packet=json.loads(discovery.claim(net,agent,'game'))
    finding=json.loads(discovery.propose(net,agent,'game',packet['region']['id'],packet['claim_token'],proposal(packet)))['proposal']
    discovery.review(net,'game',finding,'reject',feedback='Check palette pointers rather than ASCII next time.')
    store=net.project_store(pid)
    with store.tx() as db:
        for i in range(54):db.execute('INSERT INTO asset_findings(id,run,region,agent,author,model,outcome,body,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(str(i),run,packet['region']['id'],agent.id,'Tester','model','unresolved','{}','recorded',i))
    first=discovery.status(net,'game');second=discovery.status(net,'game',page=1)
    assert first['finding_total']==55 and len(first['findings'])==50 and len(second['findings'])==5
    assert not {r['id'] for r in first['findings']}&{r['id'] for r in second['findings']}


def test_api_provider_uses_discovery_tools_and_rotates_after_structured_finding(discovered,tmp_path,monkeypatch):
    import httpx

    from decompnet import agents as kinds
    from decompnet.app import agents
    from tests.test_agents import URL, _no_agent_settings
    net,pid,root,run,agent,session=discovered
    _no_agent_settings(monkeypatch);manager=agents.Manager(net,tmp_path,URL)
    worker=manager._record('game','api','asset-api','API asset investigator',dict(task='assets',asset_run=run,base_url='http://mock/v1',model='m',context_tokens=32768,per_session=1))
    worker.state='working';owner,_=net.local_agent(worker.agent_name);discovery.bind(net,worker,owner)
    packet=json.loads(discovery.claim(net,owner,'game'));requests=[]
    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        assert {t['function']['name'] for t in payload['tools']}==discovery.TOOLS
        assert 'Never claim functions' in payload['messages'][0]['content']
        args=dict(region=packet['region']['id'],claim_token=packet['claim_token'],approach='Check palette and byte order',findings='No recognizable layout; try loader evidence next.')
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'report','type':'function','function':{'name':'defer_asset_region','arguments':json.dumps(args)}}]}}],'usage':{'prompt_tokens':100,'completion_tokens':5}})
    real=httpx.Client;monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    assert kinds.get('api').run(manager,worker,owner)
    assert len(requests)==1 and discovery.progress(net,'game',owner.id)==1
    assert not net.store.all('SELECT * FROM attempts')


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_discovery_reaches_each_rom_console_through_the_same_tools(project,platform):  # noqa: F811
    from decompnet.consoles import get
    net,pid,root=project;console=get(platform)
    with net.progress.view():cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))))
    cfg['platform']=platform
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?,big_endian=? WHERE id=?',(json.dumps(cfg),int(console.big_endian),pid))
    rom_explorer.run(net,'game',Log());run=discovery.prepare(net,'game',{'mode':'range','start':8,'end':128})
    agent,_=net.local_agent('console-investigator')
    session=SimpleNamespace(slug='game',label='Console investigator',model='fixture',stop=threading.Event(),options={'task':'assets','asset_run':run})
    discovery.bind(net,session,agent);packet=call(net,agent,'claim_asset_region')
    result=call(net,agent,'propose_asset_region',region=packet['region']['id'],claim_token=packet['claim_token'],proposal=proposal(packet))
    assert discovery.review(net,'game',result['proposal'])['status']=='pending'


def test_creation_job_discovery_deduplicates_and_stop_cancels_only_this_project(discovered,monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app import support
    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW
    net,pid,root,run,agent,session=discovered;made=[];original=support.Jobs.__init__
    def init(self,*args,**kwargs):original(self,*args,**kwargs);made.append(self)
    monkeypatch.setattr(support.Jobs,'__init__',init)
    with net.progress.view():mcp,manager,security,_=build(net,root/'.decomp',PORT,SECRET)
    jobs=made[-1]
    def waiting(log):log.cancel_event.wait(5);log.check_cancel()
    identity=jobs.start('Create asset discovery agents',waiting,project='game')
    other=jobs.start('Create asset discovery agents',waiting,project='other')
    monkeypatch.setattr(manager,'start',lambda *a,**k:pytest.fail('Existing startup must be reattached'))
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers={**WINDOW,'X-Decomp':'1'})
    response=client.get('/api/rom-explorer/discovery?slug=game&view=activity')
    assert response.json()['creation_job']['id']==identity
    response=client.post('/api/agents/start',json=dict(slug='game',kind='codex',count=1,background=True,options=dict(task='assets',asset_run=run)))
    assert response.json()['job']==identity
    assert client.post('/api/rom-explorer/discovery',json=dict(slug='game',action='stop')).status_code==200
    assert jobs.cancel_events[identity].is_set() and not jobs.cancel_events[other].is_set()
    jobs.cancel(other)
