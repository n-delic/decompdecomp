"""Draft throughput preserves exact verdicts, live claims, and the saved-source lane."""
import threading
from types import SimpleNamespace

import pytest

from decompnet.app import autopilot_claims, draft_reports
from decompnet.app.engine import RomNetwork
from decompnet.app.engine.autopilot import _draft_pipeline
from decompnet.app.engine.judging import _search_evaluation
from tests.test_loading import tiny_game
from tests.test_overlay_drafts import network  # noqa: F401


@pytest.mark.parametrize('exact',[True,False])
def test_raw_draft_precedes_optional_repairs_and_uses_exact_verdict(network,monkeypatch,exact):  # noqa: F811
    net,proj,fn=network;events=[];records=[]
    source=f'int {fn["name"]}(int x) {{ return x ? 1 : 2; }}'
    monkeypatch.setattr(net,'_draft_plan',lambda *a,**kw:('plan','gcc-2.7.2-kmc',''))
    monkeypatch.setattr(net,'_seed_for',lambda *a:None)
    monkeypatch.setattr(net,'_jtbl_asm',lambda *a:[])
    monkeypatch.setattr('decompnet.seeds.generate_from_asm',lambda *a,**kw:source)
    monkeypatch.setattr('decompnet.app.return_repair_autopilot.run',lambda *a:False)
    def variants(*a):
        assert events and events[0]=='compile'
        events.append('variants');return []
    monkeypatch.setattr('decompnet.app.loop_storage_shapes.variants',variants)
    def compile(*a):
        events.append('compile');return SimpleNamespace(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile)
    candidate=net._target_obj(fn,proj)
    def evaluate(*a):
        assert _search_evaluation.get()
        return candidate,exact,[]
    monkeypatch.setattr(net,'_evaluate',evaluate)
    monkeypatch.setattr(net,'_record',lambda *a,**kw:records.append(kw['matched']))
    monkeypatch.setattr(net,'_function',lambda *a:pytest.fail('Function ID refresh must not synchronize the project'))
    agent,_=net.local_agent('autopilot')
    result=net._autopilot_one(agent,proj,fn['id'])
    assert records and all(value is exact for value in records)
    assert (result=='match') is exact
    assert ('variants' in events) is not exact
    assert draft_reports.read(net,fn['id'])['complete']
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


@pytest.mark.parametrize('stop',[False,True])
def test_all_machine_drafts_get_a_turn_before_saved_repairs(tmp_path,monkeypatch,stop):
    net=RomNetwork(tmp_path/'app.db',tmp_path);proj=tiny_game(net)
    ids=[row['id'] for row in net.store.all('SELECT id FROM functions')]
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='int f(void){return 1;}'")
    monkeypatch.setattr(net.twins,'full_pass',lambda *a,**kw:SimpleNamespace(matched=0))
    state=dict(state='running',tried=0,matched=0,near=0,twins=0,stop=threading.Event(),_foreground=threading.Event())
    state['_foreground'].set();events=[]
    def draft(agent,project,fid):
        assert _draft_pipeline.get()
        events.append(('draft',fid))
        if stop:state['stop'].set()
        return 'far'
    def repair(agent,project,fid):
        assert len([kind for kind,_ in events if kind=='draft'])==len(ids)
        events.append(('repair',fid));return 'match'
    monkeypatch.setattr(net,'_autopilot_repair_one',repair)
    net._autopilot_work(proj['slug'],ids,state,(1,1),draft)
    if stop:
        assert events==[('draft',ids[0])] and state['state']=='stopped'
    else:
        assert state['tried']==len(ids) and state['repair_done']==len(ids) and state['matched']==len(ids)
        assert state['state']=='done'


def test_claim_cleanup_is_coalesced_but_selected_expiry_is_immediate(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path);tiny_game(net)
    ids=[row['id'] for row in net.store.all('SELECT id FROM functions')]
    agent,_=net.local_agent('autopilot')
    count=[];original=net._expire_claims
    monkeypatch.setattr(net,'_expire_claims',lambda *a,**kw:(count.append(1),original(*a,**kw)))
    now=[0.];monkeypatch.setattr(autopilot_claims.time,'monotonic',lambda:now[0])
    with autopilot_claims.scope(autopilot_claims.Batch()):
        identities=autopilot_claims.identities(net,net.store)
        for fid in ids:
            with net.store.tx() as db:autopilot_claims.expire(net,db,fid,identities)
        assert len(count)==1
        with net.store.tx() as db:
            db.execute('INSERT INTO claims VALUES(?,?,?,?)',(ids[0],agent.id,0,0))
            autopilot_claims.expire(net,db,ids[0],identities)
            assert not db.execute('SELECT 1 FROM claims WHERE function_id=?',(ids[0],)).fetchone()
        assert len(count)==2
        with net.store.tx() as db:
            db.execute('INSERT INTO claims VALUES(?,?,?,?)',(ids[0],agent.id,0,10**12))
            autopilot_claims.expire(net,db,ids[0],identities)
            assert db.execute('SELECT 1 FROM claims WHERE function_id=?',(ids[0],)).fetchone()
        assert len(count)==2
        now[0]=1
        with net.store.tx() as db:autopilot_claims.expire(net,db,ids[1],identities)
        assert len(count)==3


def test_repair_phase_rechecks_live_claims_and_saved_file_revision(network,monkeypatch):  # noqa: F811
    from decompnet.network import NetworkError
    net,proj,fn=network;agent,_=net.local_agent('autopilot')
    other,_=net.local_agent('other-agent')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],other.id,0,10**12))
    monkeypatch.setattr('decompnet.app.return_repair_autopilot.run',lambda *a:pytest.fail('Busy function must be skipped'))
    assert net._autopilot_repair_one(agent,proj,fn['id'])=='skip'
    with net.store.tx() as db:db.execute('DELETE FROM claims WHERE function_id=?',(fn['id'],))
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Repair must not synchronize the whole project'))
    def changed(*args):
        net.progress.revisions[proj['slug']]=net.progress.revisions.get(proj['slug'],0)+1
        net.progress.check_result()
    monkeypatch.setattr('decompnet.app.return_repair_autopilot.run',changed)
    with pytest.raises(NetworkError,match='Progress changed'):
        net._autopilot_repair_one(agent,proj,fn['id'])
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_pass_identity_reads_are_shared_but_new_identities_refresh(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path);tiny_game(net)
    times=[0.];calls=[]
    monkeypatch.setattr(autopilot_claims.time,'monotonic',lambda:times[0])
    monkeypatch.setattr(net,'_machine_agent_ids',lambda:calls.append(1) or {1})
    with autopilot_claims.scope(autopilot_claims.Batch()):
        for _ in range(12):assert autopilot_claims.identities(net,net.store)['machine_ids']=={1}
        assert len(calls)==1
        times[0]=1
        autopilot_claims.identities(net,net.store)
        assert len(calls)==2


def test_stopping_in_repair_phase_keeps_draft_progress_and_stops_new_repairs(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path);proj=tiny_game(net)
    ids=[row['id'] for row in net.store.all('SELECT id FROM functions')]
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='int f(void){return 1;}'")
    monkeypatch.setattr(net.twins,'full_pass',lambda *a,**kw:SimpleNamespace(matched=0))
    state=dict(state='running',tried=0,matched=0,near=0,twins=0,stop=threading.Event(),_foreground=threading.Event())
    state['_foreground'].set();calls=[]
    def repair(*args):
        calls.append(1);state['stop'].set();return 'match'
    monkeypatch.setattr(net,'_autopilot_repair_one',repair)
    net._autopilot_work(proj['slug'],ids,state,(1,1),lambda *a:'far')
    assert state['state']=='stopped' and state['tried']==len(ids) and state['repair_done']==1
    assert state['repair_matched']==1 and calls==[1]
    assert net.autopilot_status(proj['slug'])['repair_done']==1
