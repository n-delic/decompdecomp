"""Transcript regressions use synthetic metadata, fake CLIs and the ordinary claim/record paths."""
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anyio
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.agents.opencode import OPENCODE
from decompnet.app import address_lookup, agent_tokens, agents, candidate_changes, tools
from decompnet.network import NetworkError
from decompnet.release_receipts import current as claim_token
from decompnet.repair_feedback import trial
from tests.test_agent_audit_followups import setup
from tests.test_agent_workflow_efficiency import game


def address_game(tmp_path):
    net,agent,fn=game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='func_80001000_a',unit='overlay-a',vram=2147487744,rom_offset=100,size=32,target=?",(bytes.fromhex('03e0000800000000'),))
        db.execute("INSERT INTO functions(project_id,name,unit,vram,rom_offset,size,status,n_insns) VALUES(1,'func_80001000_b','overlay-b',2147487744,500,32,'open',8)")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'table_bytes',2147487760,4,'data')")
    return net,agent


def test_address_batch_distinguishes_entries_interiors_data_and_overlay_ambiguity(tmp_path):
    net,agent=address_game(tmp_path)
    items=address_lookup.resolve(net,agent,'game',['0x80001000','0x80001004','table_bytes','missing'])['results']
    assert [i['kind'] for i in items]==['ambiguous','ambiguous','data','unknown']
    net.claim_row(agent,'game','func_80001000_a')
    items=address_lookup.resolve(net,agent,'game',['D_80001000','0x80001004','func_80001000_a+0x8'])['results']
    assert [i['kind'] for i in items]==['function_entry','function_interior','function_interior']
    assert items[1]['candidates'][0]['function']=='func_80001000_a' and items[1]['candidates'][0]['offset']==4
    assert items[2]['candidates'][0]['offset']==8
    other=address_lookup.resolve(net,agent,'game',['0x80001000'],'func_80001000_b')['results'][0]
    assert other['candidates'][0]['function']=='func_80001000_b'
    assert net._function('func_80001000_a','game')['status']=='open'


def test_function_reads_resolve_unambiguous_aliases_but_mutations_do_not(tmp_path,monkeypatch):
    net,agent=address_game(tmp_path)
    net.claim_row(agent,'game','func_80001000_a')
    monkeypatch.setitem(tools.HANDLERS,'get_function',lambda *a,**kw:kw['function'])
    result=tools.call(net,agent,'game','get_function',{'function':'D_80001000'})
    assert 'Resolved read' in result and result.endswith('func_80001000_a')
    result=tools.call(net,agent,'game','get_function',{'function':'0x80001004'})
    assert 'func_80001000_a' in result
    assert '"kind": "data"' in tools.call(net,agent,'game','get_function',{'function':'table_bytes'})
    assert tools.call(net,agent,'game','release_function',{'function':'D_80001000','notes':'x','not_c':True}).startswith('error:')
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)


def test_resolver_bounds_and_mcp_api_share_the_schema(tmp_path):
    net,agent=address_game(tmp_path)
    for queries in ([], ['a']*21, [1], ['x'*201]):
        with pytest.raises(NetworkError):address_lookup.resolve(net,agent,'game',queries)
    api=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='resolve_addresses')
    assert api['parameters']['properties']['queries']['maxItems']==20
    defs=anyio.run(tools.build_mcp(net,lambda ctx:(agent,'game')).list_tools)
    assert any(t.name=='resolve_addresses' for t in defs)


def test_opencode_short_names_require_unique_discovery_and_preserve_full_ids():
    known=OPENCODE.read_models(['opencode/example-free','provider/deep/model','invalid/','provider//broken'])
    assert [r['id'] for r in known]==['opencode/example-free','provider/deep/model']
    assert OPENCODE.resolve_model('example-free#high',known)=='opencode/example-free#high'
    assert OPENCODE.resolve_model('other/custom-model',known)=='other/custom-model'
    assert OPENCODE.validate_model('opencode/example-free') is None
    for invalid in ('example-free','provider/','/model','provider//model','provider/model#','provider/.','provider/..'):
        assert OPENCODE.validate_model(invalid)
    with pytest.raises(ValueError,match='ambiguous'):
        OPENCODE.resolve_model('example-free',known+[{'id':'other/example-free'}])
    with pytest.raises(ValueError,match='Refresh models'):
        OPENCODE.resolve_model('not-discovered',known)


def test_model_validation_precedes_project_preparation_and_worker_creation(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(OPENCODE,'models',lambda *a,**kw:[])
    monkeypatch.setattr(manager,'_prepare',lambda *a:pytest.fail('Invalid model must not prepare a project'))
    before=net.store.one('SELECT COUNT(*) n FROM helpers')['n']
    with pytest.raises(ValueError,match='provider/model'):
        manager.start('opencode','game',options={'model':'example-free'})
    assert net.store.one('SELECT COUNT(*) n FROM helpers')['n']==before


def test_unknown_model_from_stderr_is_fatal_before_handshake_retry(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    s.kind='opencode';s.options['model']='opencode/removed-model'
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a, **kw:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *a,**kw:None)
    monkeypatch.setattr(manager,'_command',lambda *a:(['stub'],{},''))
    monkeypatch.setattr(manager,'_save',lambda *a:None)
    monkeypatch.setattr(agents,'_proc_start',lambda *a:None)
    proc=SimpleNamespace(pid=987654321,stdin=io.StringIO(),stdout=io.StringIO(),
                         stderr=io.StringIO('ProviderModelNotFoundError: Model not found: opencode/removed-model\n'),wait=lambda:1)
    monkeypatch.setattr(agents.subprocess,'Popen',lambda *a,**kw:proc)
    with pytest.raises(agents.Fatal,match='Model not found'):
        manager._run_cli(s,net.local_agent(s.agent_name)[1],tmp_path)
    assert s.tool_handshake_failures==0


def test_repeated_release_is_one_event_and_one_abandon_even_in_parallel(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    def release():return net.release_function(agent,'f','Hand off findings','game',move_on=True)
    with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(lambda _:release(),range(2)))
    assert replies[0]==replies[1]
    assert net._function('f','game')['abandons']==1
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='release'")['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM notes')['n']==1
    other,_=net.local_agent('other');net.claim_row(other,'game','f')
    assert release()==replies[0]
    assert net.store.one('SELECT agent_id FROM claims')['agent_id']==other.id


def test_old_release_retry_never_releases_a_new_claim_and_current_token_can(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    old=claim_token(net,fn,agent)
    first=net.release_function(agent,'f','Same findings','game',move_on=True,claim_token=old)
    net.claim_row(agent,'game','f');new=claim_token(net,fn,agent)
    assert old!=new
    assert net.release_function(agent,'f','Same findings','game',move_on=True,claim_token=old)==first
    replay=net.release_function(agent,'f','Same findings','game',move_on=True)
    assert 'newer claim' in replay
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)
    with pytest.raises(NetworkError,match='stale'):
        net.release_function(agent,'f','Different findings','game',move_on=True,claim_token=old)
    net.release_function(agent,'f','Same findings','game',move_on=True,claim_token=new)
    assert not net.store.one('SELECT 1 FROM claims')
    assert net._function('f','game')['abandons']==2


def test_not_c_duplicate_is_harmless_and_pushback_is_not_a_completed_receipt(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    first=net.release_function(agent,'f','More work required','game')
    assert first.startswith('Not released') and net.store.one('SELECT 1 FROM claims')
    first=net.release_function(agent,'f','Review incomplete boundary evidence','game',not_c=True)
    assert net.release_function(agent,'f','Review incomplete boundary evidence','game',not_c=True)==first
    assert net._function('f','game')['abandons']==1


def test_candidate_origin_uses_recorded_writer_and_score_delta_without_compiling(tmp_path):
    net,agent,fn=game(tmp_path)
    net.claim_row(agent,'game','f')
    before,revision=agent_tokens.candidate(net,agent,'game','f')
    other,_=net.local_agent('permuter')
    with trial({'kind':'local repair','family':'declaration_order','variant':2}):
        net._record(fn,other,5,'int f(){return 2;}',1,via='permuter')
    current,actual=agent_tokens.candidate(net,agent,'game','f')
    assert current!=before
    error=str(agent_tokens.stale(net,agent,fn,revision,current,actual))
    assert 'permuter' in error and '10 → 5' in error and 'declaration_order' in error and 'variant 2' in error
    assert 'Historical scores are not a fresh verification' in error
    assert net._function('f','game')['status']=='open'


def test_recovery_selection_is_distinct_from_source_writer(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    monkeypatch.setattr('decompnet.app.agent_evidence.baseline',lambda *a:None)
    text=agent_tokens.get_candidate(net,agent,'game','f',base='saved_best')
    assert 'You selected saved_best' in text and 'claim_token:' in text
    source,_=agent_tokens.candidate(net,agent,'game','f')
    assert 'Recorded attempt' in candidate_changes.describe(net,agent,fn,source)
    assert not net.store.one("SELECT 1 FROM attempts WHERE source='unknown source'")
    assert 'no writer or score change inferred' in candidate_changes.describe(net,agent,fn,'unknown source')


def test_batch_selection_origin_names_the_kept_variant(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    def compile(who,function,source,slug):
        score=5 if 'return 2' in source else 7
        net._record(net._function(function,slug),who,score,source,1)
        return 'score '+str(score)
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *a:None)
    monkeypatch.setattr('decompnet.app.agent_evidence.baseline',lambda *a:None)
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    reply=agent_tokens.variants(net,agent,'game','f',revision,[{'old':'return 1','new':'return 2'},{'old':'return 1','new':'return 3'}])
    assert 'Your explicit patches batch selected variant 1' in reply
    source,current=agent_tokens.candidate(net,agent,'game','f')
    assert 'return 2' in source
    assert 'batch selected variant 1' in str(agent_tokens.stale(net,agent,fn,revision,source,current))


def test_room_transport_release_requires_current_host_token_after_reclaim(tmp_path):
    from decompnet.release_receipts import transport_release
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    old=claim_token(net,fn,agent)
    first=transport_release(net,agent,'f','Findings','game',claim_token=old)
    net.claim_row(agent,'game','f');new=claim_token(net,fn,agent)
    assert transport_release(net,agent,'f','Findings','game',claim_token=old)==first
    with pytest.raises(NetworkError,match='claimed again'):
        transport_release(net,agent,'f','Changed findings','game')
    assert net.store.one('SELECT 1 FROM claims')
    transport_release(net,agent,'f','Changed findings','game',claim_token=new)
    assert not net.store.one('SELECT 1 FROM claims')


def test_explicit_function_plus_offset_keeps_its_image_and_loose_names_keep_units(tmp_path):
    net,agent=address_game(tmp_path);net.claim_row(agent,'game','func_80001000_a')
    item=address_lookup.resolve(net,agent,'game',['func_80001000_b+0x4'])['results'][0]
    assert item['candidates'][0]['function']=='func_80001000_b'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=? WHERE name='func_80001000_b'",('{"real":"Class::Update"}',))
    item=address_lookup.resolve(net,agent,'game',['Class::Update'])['results'][0]
    assert item['kind']=='function_entry' and item['candidates'][0]['function']=='func_80001000_b'


def test_claim_tokens_distinguish_reclaims_even_when_the_clock_is_identical(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    monkeypatch.setattr('decompnet.network.time.time',lambda:1234.0)
    net.claim_row(agent,'game','f');old=claim_token(net,fn,agent)
    net.release(agent,'f','First turn','game',claim_token=old)
    net.claim_row(agent,'game','f');new=claim_token(net,fn,agent)
    assert old!=new
    net.release(agent,'f','First turn','game',claim_token=old)
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)


def test_model_short_name_is_resolved_in_the_worker_record_without_running_discovery(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    calls=[];caller=threading.get_ident()
    def cached(path,run=True):
        assert run is False
        # Other tests can leave a concurrent catalog refresh finishing here.
        # Count this synchronous start request, while stubbing discovery for all callers.
        if threading.get_ident()==caller:calls.append(1)
        return [{'id':'opencode/example-free','name':'example-free'}]
    monkeypatch.setattr(OPENCODE,'models',cached)
    monkeypatch.setattr(manager,'_prepare',lambda *a:None)
    monkeypatch.setattr(manager,'_launch',lambda *a:None)
    ids=manager.start('opencode','game',options={'model':'example-free'})
    assert manager.sessions[ids[0]].model=='opencode/example-free'
    assert len(calls)==1


def test_release_notes_after_an_automatic_release_are_not_duplicated(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    net.release(agent,'f','Automatic pass ended','game')
    reply=net.release_function(agent,'f','Additional findings','game')
    count=net.store.one('SELECT COUNT(*) n FROM notes')['n']
    assert net.release_function(agent,'f','Additional findings','game')==reply
    assert net.store.one('SELECT COUNT(*) n FROM notes')['n']==count
