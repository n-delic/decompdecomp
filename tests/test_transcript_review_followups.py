"""Real claim/evidence paths with synthetic games; no provider calls or live projects."""
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import agents, data_regions, prepared_evidence, session_followups, source_preparation, tools
from decompnet.network import Agent, NetworkError
from tests.test_agent_audit_followups import setup
from tests.test_agent_workflow_efficiency import game
from tests.test_prepared_evidence import prepared_net


def region_game(tmp_path):
    net, agent, fn = game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=4096,rom_offset=100,size=8,unit='overlay-a',target=?", (bytes.fromhex('03e0000800000000'),))
        for name, unit, vram, offset, status in [('next','overlay-a',4104,108,'open'),
                    ('twin','overlay-b',4096,500,'open'),('matched','overlay-a',4112,116,'matched')]:
            db.execute('INSERT INTO functions(project_id,name,unit,vram,rom_offset,size,status,n_insns) VALUES(1,?,?,?,?,8,?,2)',
                       (name,unit,vram,offset,status))
    net.claim_row(agent,'game','f')
    return net, agent


def test_data_region_deduplicates_and_pauses_only_new_claims_in_its_original_image(tmp_path):
    net, agent = region_game(tmp_path)
    for _ in range(2):
        data_regions.report(net,agent,'game','f','0x1000','0x1018','Pointer table; both words point into code.')
    items = data_regions.listing(net,'game')['items']
    assert len(items)==1 and len(items[0]['evidence'])==1
    assert {f['name'] for f in items[0]['functions']}=={'f','next','matched'}
    key=items[0]['id']
    data_regions.decide(net,'game',key,'pause')
    assert net.claim_row(agent,'game','f')['name']=='f'  # held work may finish
    other,_=net.local_agent('other')
    with pytest.raises(NetworkError,match='data-region review'):
        net.claim_row(other,'game','next')
    assert net.claim_row(other,'game')['name']=='twin'
    assert net._function('matched','game')['status']=='matched'
    assert net.selection_remaining('game',agent)==1
    data_regions.decide(net,'game',key,'resume')
    assert net.selection_remaining('game',agent)==3


def test_changed_region_bytes_lift_pause_and_require_new_evidence(tmp_path):
    net, agent=region_game(tmp_path)
    data_regions.report(net,agent,'game','f','0x1000','0x1010','Pointer table')
    key=data_regions.listing(net,'game')['items'][0]['id']
    data_regions.decide(net,'game',key,'pause')
    with net.store.tx() as db:db.execute("UPDATE functions SET target=? WHERE name='next'",(bytes.fromhex('03e0000824020001'),))
    assert data_regions.blocked(net,net.project_row('game'))==[]
    assert data_regions.listing(net,'game')['items'][0]['stale']
    with pytest.raises(NetworkError,match='changed'):
        data_regions.decide(net,'game',key,'pause')
    data_regions.report(net,agent,'game','f','0x1000','0x1010','Rechecked changed table bytes')
    assert not data_regions.listing(net,'game')['items'][0]['stale']


def test_reports_require_a_live_claim_and_valid_region_and_remain_hypotheses(tmp_path):
    net, agent=region_game(tmp_path)
    other,_=net.local_agent('other')
    for start,end in [('bad','0x1010'),('0x1004','0x1010'),('0x1000','0x1000')]:
        with pytest.raises(NetworkError):data_regions.report(net,agent,'game','f',start,end,'table')
    with pytest.raises(NetworkError,match='Claim'):
        data_regions.report(net,other,'game','f','0x1000','0x1010','table')
    assert net._function('f','game')['status']=='open'
    with pytest.raises(NetworkError):data_regions.decide(net,'other','data-region:1:x','pause')


def test_unrelated_configuration_does_not_stale_bundle_but_own_profile_does():
    net,proj,fn=prepared_net()
    cfg=json.loads(proj['config']);before=source_preparation.inputs(net,proj,fn)
    cfg.update(knowledge_auto=True,boundaries=[{'function':'other'}],compiler_ranges=[{'start':9000,'end':9010,'toolchain':'other','flags':[]}])
    fresh={**dict(proj),'config':json.dumps(cfg)}
    assert source_preparation.inputs(net,fresh,fn)==before
    cfg['compiler']={'toolchain':'different','flags':['-O3']}
    assert source_preparation.inputs(net,{**fresh,'config':json.dumps(cfg)},fn)!=before


def test_bundle_retries_symbol_change_once_and_never_bypasses_byte_judge(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda *a:net.store.one('SELECT * FROM projects WHERE id=1')
    calls=[]
    def compile(*args):
        calls.append(1)
        if len(calls)==1:
            with net.store.tx() as db:db.execute("INSERT INTO symbols(project_id,name,address) VALUES(1,'new_symbol',5000)")
        return SimpleNamespace(ok=True,obj=b'object',effective_settings={})
    net._compile=compile;net._evaluate=lambda *a:('candidate',True,[])
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *a:True)
    assert prepared_evidence.promote_measured(net,proj,fn,'new','context',{'toolchain':'ido-5.3','flags':['-O3']},
                                              Agent(1,'autopilot',None),source_preparation.inputs(net,proj,fn))
    assert len(calls)==2 and net._function('f')['status']=='matched'


def test_stale_source_is_explained_without_recompilation_or_overwrite(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda *a:net.store.one('SELECT * FROM projects WHERE id=1')
    expected=source_preparation.inputs(net,proj,fn)
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='user edit'")
    net._compile=lambda *a:pytest.fail('No compile for stale source')
    with pytest.raises(NetworkError,match='saved source'):
        prepared_evidence.promote_measured(net,proj,fn,'old','context',{},Agent(1,'a',None),expected)
    assert net._function('f')['best_source']=='user edit'


def test_successful_reports_count_as_investigation_but_notes_do_not(tmp_path,monkeypatch):
    net,agent=region_game(tmp_path);began=time.time()
    net.add_note(agent,'game','A suspicion','f')
    assert session_followups.investigation_outcome(net,agent,began) is None
    result=tools.call(net,agent,'game','report_data_region',dict(function='f',start='0x1000',end='0x1010',reason='Pointer words'))
    assert not result.startswith('error:'),result
    assert session_followups.investigation_outcome(net,agent,began)=='Data investigation completed'
    assert net._function('f','game')['status']=='open'


def test_cli_handshake_gates_edits_and_preflight_cannot_satisfy_it(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    net._pending_tool_handshakes={(agent.id,'game')}
    monkeypatch.setitem(tools.HANDLERS,'project_info',lambda *a,**kw:'Authenticated facts')
    assert 'First call project_info' in tools.call(net,agent,'game','claim_function',{})
    assert not net.store.one('SELECT 1 FROM claims')
    assert tools._dispatch(net,agent,'game','project_info',{},count_usage=False)=='Authenticated facts'
    assert net._pending_tool_handshakes  # app-only check is not actual CLI handshake
    assert tools.call(net,agent,'game','project_info',{})=='Authenticated facts'
    assert not net._pending_tool_handshakes
    monkeypatch.setitem(tools.HANDLERS,'claim_function',lambda *a,**kw: 'CLAIMED '+net.claim_row(agent,'game')['name'])
    assert 'CLAIMED f' in tools.call(net,agent,'game','claim_function',{})


def test_cli_missing_handshake_retries_once_then_pauses_without_claiming(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a:None)
    monkeypatch.setattr(manager,'_command',lambda *a:(['stub'],{},'Check tools'))
    monkeypatch.setattr(agents,'_proc_start',lambda *a:None)
    monkeypatch.setattr(manager,'_save',lambda *a:None)
    def process(*a,**kw):
        return SimpleNamespace(pid=123456789,stdin=io.StringIO(),stdout=io.StringIO(json.dumps(
            {'type':'result','result':'The tools are unavailable.'})+'\n'),stderr=io.StringIO(),wait=lambda:0,returncode=0)
    monkeypatch.setattr(agents.subprocess,'Popen',process)
    token=net.local_agent(s.agent_name)[1]
    with pytest.raises(agents.ToolConnectionUnavailable,match='Reconnecting once'):manager._run_cli(s,token,tmp_path)
    assert not net.store.one('SELECT 1 FROM claims')
    with pytest.raises(agents.Fatal,match='two attempts'):manager._run_cli(s,token,tmp_path)


def test_knowledge_hides_same_model_backing_and_queues_independent_examples(tmp_path):
    net,agent,_=game(tmp_path)
    proposer,_=net.local_agent('model-a-1');reviewer,_=net.local_agent('model-b-1')
    result=net.knowledge.share(proposer,'game','Prefer a narrow signed type when the target sign extends.',evidence='f: score 50 to 0')
    kid=int(result.split('#')[1].split('.')[0])
    assert f'#{kid}' not in net.knowledge.for_prompt('game',proposer)
    focused=SimpleNamespace(id='0',slug='game',model='model-b',state='working',stop=threading.Event(),options={'focus_id':'x'})
    active=SimpleNamespace(id='1',slug='game',model='model-b',state='working',stop=threading.Event(),options={},agent_name='model-b-1',label='Model B')
    with pytest.raises(NetworkError,match='different model'):net.knowledge.request_verification('game',kid,[focused])
    queued=net.knowledge.request_verification('game',kid,[focused,active])
    assert queued['model']=='model-b'
    prompt=net.knowledge.for_prompt('game',reviewer)
    assert 'Requested independent verification' in prompt and 'f: score 50 to 0' in prompt
    assert net.knowledge.listing('game')['items'][0]['status']=='proposed'
    net.knowledge.share(reviewer,'game',supports=kid,evidence='Independent reproduction: score 0')
    assert f'#{kid}' not in net.knowledge.for_prompt('game',reviewer)


def test_data_region_ui_and_shared_tool_schemas(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    import anyio

    from decompnet.agents.api import api_tool_schemas
    net,agent,_=game(tmp_path)
    schema=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='report_data_region')
    assert set(schema['parameters']['required'])=={'function','start','end','reason'}
    definitions=anyio.run(tools.build_mcp(net,lambda ctx:(agent,'game')).list_tools)
    assert any(t.name=='report_data_region' for t in definitions)
    node=shutil.which('node')
    if node:
        subprocess.run([node,str(Path(__file__).with_name('gui_data_region_reviews.js'))],check=True,timeout=20)


def test_region_resume_invalidates_idle_receipt_immediately(tmp_path):
    from decompnet.app import work_availability
    net,agent=region_game(tmp_path)
    data_regions.report(net,agent,'game','f','0x1000','0x1010','Pointer table')
    key=data_regions.listing(net,'game')['items'][0]['id']
    before=work_availability.version(net,agent,'game')
    data_regions.decide(net,'game',key,'pause')
    assert work_availability.version(net,agent,'game')!=before
    paused=work_availability.version(net,agent,'game')
    data_regions.decide(net,'game',key,'resume')
    assert work_availability.version(net,agent,'game')!=paused


def test_manager_records_investigation_without_a_compilation_nudge(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    calls=[]
    def run(*args):
        calls.append(1)
        if len(calls)==1:
            session_followups.investigation_completed(net,agent,'game','report_boundary')
        else:
            manager.stop(s.id)
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    manager._run(s)
    assert [r['outcome'] for r in net.store.all('SELECT outcome FROM helper_sessions')]==['investigated','stopped']
    assert not any('without compiling' in r['text'] or 'compilation guidance' in r['text'] for r in s.feed)


def test_startup_waits_without_paid_request_when_other_agents_hold_every_function(tmp_path,monkeypatch):
    from decompnet.app import session_budget
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    other,_=net.local_agent('busy-worker')
    net.claim_row(other,'game')
    assert net.selection_remaining('game',agent)>0
    assert net.selection_remaining('game',agent,available_only=True)==0
    session_budget.start(net,agent.id,'game',{})
    s.context_budget=session_budget.current(net,agent,'game')
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a:None)
    monkeypatch.setattr(tools,'_project_info',lambda *a,**kw:'Project facts')
    monkeypatch.setattr(manager,'_command',lambda *a:pytest.fail('No model request while all functions are held'))
    assert manager._run_cli(s,net.local_agent(s.agent_name)[1],tmp_path)
    assert s.rotated and s.context_budget.waiting


def test_verification_uses_the_recorded_model_when_launcher_uses_its_default(tmp_path):
    net,_,_=game(tmp_path)
    proposer,_=net.local_agent('default-a-1');other,_=net.local_agent('default-b-1')
    text=net.knowledge.share(proposer,'game','Prefer a signed byte local for sign-extension patterns.')
    kid=int(text.split('#')[1].split('.')[0])
    session=SimpleNamespace(id='1',slug='game',model='',state='working',stop=threading.Event(),options={},agent_name=other.name,label='Default B')
    assert net.knowledge.request_verification('game',kid,[session])['model']=='default-b'
    assert 'Requested independent verification' in net.knowledge.for_prompt('game',other)


def test_expired_bundle_claim_does_not_start_a_recheck():
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda *a:net.store.one('SELECT * FROM projects WHERE id=1')
    expected=source_preparation.inputs(net,proj,fn)
    with net.store.tx() as db:db.execute('UPDATE claims SET expires_at=1')
    net._compile=lambda *a:pytest.fail('Expired claims must not compile')
    with pytest.raises(NetworkError,match='claim expired'):
        prepared_evidence.promote_measured(net,proj,fn,'source','header',{},Agent(1,'autopilot',None),expected)


def test_data_region_review_is_available_even_without_a_compiler_panel():
    from pathlib import Path
    html=Path('src/decompnet/app/static/index.html').read_text()
    assert html.index('data-pane="settings"') < html.index('id="dataRegionReviews"') < html.index('id="compilerPanel"')
