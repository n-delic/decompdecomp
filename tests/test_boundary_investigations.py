"""Boundary advice explains original evidence without consuming or changing matching work."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet import agents as kinds
from decompnet.app import agents, boundary_evidence, boundary_repairs, tools
from decompnet.app import boundary_investigations as investigation
from decompnet.app.tool_policy import visible
from decompnet.network import NetworkError
from tests.test_agents import URL, quiet  # noqa: F401
from tests.test_boundary_repairs import game, raw  # noqa: F401


def investigator(net, name='head'):
    agent, _ = net.local_agent('boundary-reviewer')
    session = SimpleNamespace(id='review1', label='Reviewer', model='test-model', slug='game', stop=threading.Event(),
                              options={'task':'boundary','boundary_function':name,'boundary_until':time.time()+600})
    investigation.bind(net, session, agent)
    return agent, session


def suggest(net, agent, **changes):
    args = dict(function='head', action='delay_slot', supporting='Final jr needs a delay slot at 0x80001004.',
                counter='No contradictory direct edge was observed in this bounded scan.',
                uncertainty='Indirect callers and raw gaps remain unresolved.', citations='0x80001000', confidence='strong')
    return tools.call(net, agent, 'game', 'suggest_boundary', args | changes)


def test_evidence_distinguishes_delay_slots_from_adjacency_and_small_padding(game):  # noqa: F811
    net, _ = game
    packet = boundary_evidence.read(net, 'game', 'head')
    assert 'delay slot' in packet['diagnosis']
    assert packet['assembly'][0]['return'] and packet['assembly'][0]['delay_slot']
    assert packet['neighbours'][0]['name'] == 'tail'
    assert packet['segment'] == {'name':'code','start':0x80001000,'end':0x80001020}
    tail = boundary_evidence.read(net, 'game', 'tail')
    assert any('Adjacency alone' in e['text'] for e in tail['evidence'])
    audit = {r['function']:r for r in boundary_repairs.audit(net, 'game')['findings']}
    assert audit['tail']['suggested_action'] == ''
    assert audit['padding']['suggested_action'] == ''
    assert 'intentional instruction' in boundary_evidence.read(net, 'game', 'padding')['diagnosis']
    assert 'Indirect calls' in packet['limits'] and 'Symbols may be generated' in packet['limits']


def test_large_preview_keeps_both_disputed_ends_and_neighbour_starts(game):  # noqa: F811
    net, _ = game
    proj = net.project_row('game'); cfg = json.loads(proj['config'])
    from pathlib import Path
    image = raw(*([0x24020001]*300+[0x03e00008,0,0]))
    (Path(proj['root'])/'rom.z64').write_bytes(image)
    cfg['segments'][0]['rom_end'] = len(image)
    with net.store.tx() as db:
        db.execute('DELETE FROM functions WHERE name!=\'head\'')
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
        db.execute('UPDATE functions SET size=?,target=?,n_insns=?', (1204,image[:1204],301))
    plan = boundary_repairs.preview(net,'game','head','delay_slot')
    addresses = {line['address'] for line in plan['assembly']}
    assert {0x80001000, 0x80001000+1200, plan['end'], plan['old_end']} <= addresses
    assert len(addresses) < 80


def test_incoming_calls_are_from_original_image_not_stored_candidate_assembly(game):  # noqa: F811
    net, _ = game
    proj = net.project_row('game')
    from pathlib import Path
    path = Path(proj['root'])/'rom.z64'; image = path.read_bytes()
    path.write_bytes(image[:16] + raw(0x0c000400,0) + image[24:])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=?,asm='misleading text',unit='another unit' WHERE name='normal'",(raw(0x0c000400,0),))
    packet = boundary_evidence.read(net,'game','head')
    assert any(e['from']==0x80001010 and e['to']==0x80001000 and e['kind']=='call' for e in packet['edges'])


def test_tool_scope_blocks_claim_compile_and_cross_project_reads_before_side_effects(game, monkeypatch):  # noqa: F811
    net, _ = game; agent, session = investigator(net)
    monkeypatch.setattr(net.progress, 'sync', lambda *a,**kw:pytest.fail('investigation must not synchronize matching'))
    assert set(visible(net,'game',list(tools.HANDLERS),agent)) == investigation.TOOLS
    for tool, args in [('claim_function',{}),('compile_and_diff',{'function':'head','source':'C'}),
                       ('get_boundary_evidence',{'function':'tail'}),('project_info',{'detail':True})]:
        with pytest.raises(NetworkError):tools._invoke(net,agent,'game',tool,args)
    with pytest.raises(NetworkError):tools._invoke(net,agent,'other','project_info',{})
    assert not net.store.all('SELECT * FROM claims') and not net.store.all('SELECT * FROM attempts')
    session.stop.set()
    with pytest.raises(NetworkError,match='ended'):tools._invoke(net,agent,'game','get_boundary_evidence',{'function':'head'})


def test_report_requires_fresh_evidence_and_real_instruction_citations_and_never_changes_layout(game):  # noqa: F811
    net, _ = game; agent, session = investigator(net)
    before = [dict(r) for r in net.store.all('SELECT * FROM functions')]
    assert suggest(net,agent).startswith('error:')
    packet = json.loads(tools.call(net,agent,'game','get_boundary_evidence',{'function':'head'}))
    assert suggest(net,agent,citations='0xdeadbeef').startswith('error:')
    assert suggest(net,agent,counter='').startswith('error:')
    assert 'saved' in suggest(net,agent)
    assert [dict(r) for r in net.store.all('SELECT * FROM functions')] == before
    suggestions = investigation.suggestions(net,'game',packet)
    assert len(suggestions)==1 and not suggestions[0]['stale']
    assert suggestions[0]['model']=='test-model' and suggestions[0]['citations']==[0x80001000]
    with net.store.tx() as db:db.execute("UPDATE functions SET size=8 WHERE name='head'")
    assert suggest(net,agent).startswith('error:')
    assert investigation.suggestions(net,'game',{'function':'head','revision':'changed','image':packet['image']})[0]['stale']


def test_busy_entry_can_be_investigated_without_stealing_claim_and_unclear_is_valid(game):  # noqa: F811
    net, _ = game; agent, session = investigator(net)
    other, _ = net.local_agent('working')
    fid = net._function('head','game')['id']
    with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,other.id,time.time(),time.time()+3600))
    tools.call(net,agent,'game','get_boundary_evidence',{'function':'head'})
    assert 'saved' in suggest(net,agent)
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==other.id
    assert 'already saved' in suggest(net,agent,action='unclear',confidence='unclear')
    session.id='review2'
    assert 'saved' in suggest(net,agent,action='unclear',confidence='unclear')


def test_deadline_and_completed_restore_stop_without_starting_provider(game):  # noqa: F811
    net, _ = game; agent, session = investigator(net); finished=[]
    manager = SimpleNamespace(net=net,_finish=lambda s,why:finished.append(why),
                              _run_work=lambda s:pytest.fail('must not start provider'))
    session.options['boundary_until']=time.time()-1
    investigation.run(manager,session)
    assert 'time limit' in finished[-1]
    session.options['boundary_until']=time.time()+600
    tools.call(net,agent,'game','get_boundary_evidence',{'function':'head'});suggest(net,agent)
    investigation.run(manager,session)
    assert 'recommendation saved' in finished[-1]


@pytest.mark.parametrize('kind',[k.id for k in kinds.AGENTS])
def test_each_provider_starts_same_bounded_advisory_task_without_matching_preparation(game,tmp_path,quiet,monkeypatch,kind):  # noqa: F811
    net, _ = game
    monkeypatch.setattr(agents.Manager,'_prepare',lambda *a:pytest.fail('matching preparation must not run'))
    manager = agents.Manager(net,tmp_path,URL)
    options = {'task':'boundary','boundary_function':'head'}
    if kinds.get(kind).uses_endpoints:options.update(base_url='https://example.invalid/v1',model='test-model')
    sid = manager.start(kind,'game',options=options)[0];s=manager.sessions[sid]
    assert 590<s.options['boundary_until']-time.time()<=600
    assert 'get_boundary_evidence' in manager.prompt(s) and 'unclear' in manager.prompt(s)
    manager.prepare_session(s)
    assert not net.store.all('SELECT * FROM claims')
    restored = agents.Manager(net,tmp_path,URL)
    assert restored.sessions[sid].options['boundary_until']==s.options['boundary_until']


def test_one_shot_loop_finishes_after_report_even_when_matching_queue_is_quarantined(game,tmp_path,quiet,monkeypatch):  # noqa: F811
    net, _ = game;manager=agents.Manager(net,tmp_path,URL)
    sid=manager.start('codex','game',options={'task':'boundary','boundary_function':'head'})[0];s=manager.sessions[sid]
    calls=[]
    def cli(session, token, workdir):
        calls.append(True)
        agent=net.authenticate(token)
        manager.prepare_session(session,agent)
        tools.call(net,agent,'game','project_info',{'detail':False})
        assert not net.store.all('SELECT * FROM claims')
        tools.call(net,agent,'game','get_boundary_evidence',{'function':'head'})
        assert 'saved' in suggest(net,agent)
        return True
    monkeypatch.setattr(manager,'_run_cli',cli)
    monkeypatch.setattr(manager,'_left',lambda *a:pytest.fail('matching queue must not gate investigations'))
    monkeypatch.setattr(manager,'_build_problem',lambda *a:pytest.fail('no build needed'))
    manager._run_work(s)
    assert calls==[True] and s.state=='stopped'
    row=net.store.one('SELECT * FROM helper_sessions')
    assert row['outcome']=='investigated' and row['compiles']==0 and row['task']=='boundary'
    assert not net.store.all('SELECT * FROM attempts')


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_guided_evidence_and_agent_proposal_use_each_console_byte_order(game,platform):  # noqa: F811
    from pathlib import Path

    from decompnet.consoles import get
    net, _ = game;console=get(platform);proj=net.project_row('game');cfg=json.loads(proj['config'])
    cfg['platform']=platform;cfg['big_endian']=console.big_endian
    path=Path(proj['root'])/'rom.z64';image=path.read_bytes()
    if not console.big_endian:image=b''.join(image[i:i+4][::-1] for i in range(0,len(image),4))
    path.write_bytes(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?,big_endian=?',(json.dumps(cfg),int(console.big_endian)))
        for row in net.store.all('SELECT id,rom_offset,size FROM functions'):
            db.execute('UPDATE functions SET target=? WHERE id=?',(image[row['rom_offset']:row['rom_offset']+row['size']],row['id']))
    agent, _ = investigator(net)
    packet=json.loads(tools.call(net,agent,'game','get_boundary_evidence',{'function':'head'}))
    assert packet['assembly'][0]['return'] and packet['assembly'][0]['delay_slot']
    assert 'saved' in suggest(net,agent)
    assert net._function('head','game')['size']==4


def test_edge_scan_limits_are_visible_and_do_not_turn_absence_into_proof(game,monkeypatch):  # noqa: F811
    net, _ = game
    monkeypatch.setattr(boundary_evidence,'EDGE_WORD_LIMIT',1)
    packet=boundary_evidence.read(net,'game','head')
    assert 'scan limit reached' in packet['limits'] and 'not proof of padding' in packet['limits']


def test_operator_api_reads_evidence_and_tracks_investigation_without_starting_matching(game,tmp_path,quiet,monkeypatch):  # noqa: F811
    from starlette.testclient import TestClient

    from decompnet.app import window
    from decompnet.app.server import build
    net,_=game
    mcp,manager,security,_=build(net,tmp_path/'api',6499,'boundary-test')
    application=mcp.streamable_http_app(transport_security=security,host='127.0.0.1')
    client=TestClient(application,base_url='http://127.0.0.1:6499',headers={'User-Agent':window.TOKEN_AGENT+'boundary-test'})
    packet=client.get('/api/boundary-evidence?slug=game&function=head')
    assert packet.status_code==200 and packet.json()['function']=='head'
    monkeypatch.setattr(net,'warm_briefs_soon',lambda *a:pytest.fail('must not generate matching drafts'))
    response=client.post('/api/agents/start',headers={'X-Decomp':'1'},json={
        'slug':'game','kind':'codex','count':1,'options':{'task':'boundary','boundary_function':'head'}})
    assert response.status_code==200
    sid=response.json()['started'][0]
    status=client.get('/api/boundary-investigations?slug=game&function=head').json()
    assert any(item['id']==sid for item in status['sessions'])
    assert client.get('/api/boundary-evidence?slug=game&function=missing').status_code==400
    assert not net.store.all('SELECT * FROM claims')
    manager.shutdown()


def test_api_investigation_submits_advice_and_stops_without_an_extra_model_turn(game,tmp_path,quiet,monkeypatch):  # noqa: F811
    import httpx
    net,_=game;manager=agents.Manager(net,tmp_path,URL)
    sid=manager.start('api','game',options={'task':'boundary','boundary_function':'head',
        'base_url':'https://example.invalid/v1','model':'test-model'})[0]
    session=manager.sessions[sid]
    steps=iter([('project_info',{'detail':False}),('get_boundary_evidence',{'function':'head'}),
                ('suggest_boundary',dict(function='head',action='unclear',supporting='The final jr needs a delay slot.',
                    counter='Indirect entry references remain unknown.',uncertainty='Need a wider caller analysis.',
                    citations='0x80001000',confidence='unclear'))])
    requests=[]
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        assert {t['function']['name'] for t in body['tools']}==investigation.TOOLS
        assert 'boundary investigator' in body['messages'][0]['content']
        for message in body['messages']:
            if message.get('role')=='tool': assert not message['content'].startswith('error:'), message['content']
        name,args=next(steps)
        message={'role':'assistant','content':None,'tool_calls':[{'id':str(len(requests)),'type':'function',
                 'function':{'name':name,'arguments':json.dumps(args)}}]}
        return httpx.Response(200,json={'choices':[{'message':message}],'usage':{'prompt_tokens':100,'completion_tokens':10}})
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    manager._run_work(session)
    assert len(requests)==3 and session.state=='stopped'
    row=net.store.one('SELECT outcome,compiles FROM helper_sessions WHERE helper_id=?',sid)
    assert row['outcome']=='investigated' and row['compiles']==0
    assert not net.store.all('SELECT * FROM claims')


def test_usage_and_candidate_scores_do_not_invalidate_original_boundary_evidence(game):  # noqa: F811
    net,_=game
    original=boundary_evidence.read(net,'game','head')
    net.add_spend('game',0.5,500)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=95 WHERE name='head'")
        db.execute("UPDATE functions SET status='matched',meta=json_set(meta,'$.analysis_note','unrelated') WHERE name='normal'")
    current=boundary_evidence.read(net,'game','head')
    assert current['revision']==original['revision'] and current['image']==original['image']


def test_global_tool_disables_are_respected_by_boundary_investigation(game):  # noqa: F811
    from decompnet.app.tool_policy import set_global
    net,_=game
    set_global(net,'suggest_boundary',False)
    with pytest.raises(ValueError,match='Enable'):
        investigation.configure(net,'game',{'task':'boundary','boundary_function':'head'},1)
    agent,_=investigator(net)
    assert 'suggest_boundary' not in visible(net,'game',list(tools.HANDLERS),agent)
