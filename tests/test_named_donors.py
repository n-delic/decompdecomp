import time

import pytest

from decompnet.app import named_donors as named
from decompnet.app.twins import Job
from decompnet.network import NetworkError
from tests.test_similar_matches import pair as pair_fixture

pair=pytest.fixture(pair_fixture.__wrapped__)


@pytest.fixture
def same(pair,monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET name='sdkCompute',matched_source=replace(matched_source,'old','sdkCompute') WHERE id=1")
        db.execute("UPDATE functions SET name='sdkCompute',best_score=100 WHERE id=2")
    monkeypatch.setattr(named.draft_repairs,'variants',lambda *a,**kw:[])
    return pair


def judge(net,calls,score=20):
    def attempt(worker,proj,fn,source,header,agent,fork,**kw):
        calls.append((source,header,fork))
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status=?,best_score=?,best_source=? WHERE id=?",
                       ('matched' if score==0 else 'open',score,source,fn['id']))
        return score
    return attempt


def test_named_rom_lead_does_not_need_matching_bytes_and_caches_misses(same,monkeypatch):
    calls=[]
    monkeypatch.setattr(named.source_preparation,'attempt',judge(same,calls))
    assert named.run(same.twins,'destination',Job())==0
    assert len(calls)==1 and 'sdkCompute' in calls[0][0]
    assert 'Same-name source lead' in calls[0][0]
    assert named.run(same.twins,'destination',Job())==0
    assert len(calls)==1
    assert same.twins.cross_game_results('destination')['counts']=={'miss':1}
    # A different target must renew the cached result.
    with same.store.tx() as db:db.execute("UPDATE functions SET target=? WHERE id=2",(b'other bytes',))
    named.run(same.twins,'destination',Job())
    assert len(calls)==2


@pytest.mark.parametrize('donor_fork,dest_fork',[(True,False),(False,True),(True,True)])
def test_all_mode_pairs_use_destination_judge(same,monkeypatch,donor_fork,dest_fork):
    monkeypatch.setattr(same,'fork_slug',lambda s:s if (s=='source' and donor_fork or s=='destination' and dest_fork) else None)
    with same.store.tx() as db:
        for pid,flag in [(1,donor_fork),(2,dest_fork)]:
            if flag:db.execute("UPDATE projects SET config=json_set(config,'$.kind','fork'),arch='',big_endian=0 WHERE id=?",(pid,))
    monkeypatch.setattr(named.donor_sources,'imported',lambda *a:[('int sdkCompute(int x){return x+60;}','typedef int SomeType;','donor defaults')])
    preparations=[]
    def prepare(*args):
        preparations.append(args)
        return args[-2:]
    monkeypatch.setattr(named.donor_sources,'preprocess',prepare)
    calls=[]
    monkeypatch.setattr(named.source_preparation,'attempt',judge(same,calls,0))
    job=Job()
    assert named.run(same.twins,'destination',job)==1
    assert calls[0][2]==('destination' if dest_fork else None)
    assert len(preparations)==int(donor_fork and not dest_fork)
    assert job.cross_matched==1 and job.reuse_matched==1
    assert same.store.one('SELECT COUNT(*) n FROM claims')['n']==0


def test_preprocessing_cache_tracks_header_and_profile_changes(same,monkeypatch):
    monkeypatch.setattr(same,'fork_slug',lambda s:'source' if s=='source' else None)
    with same.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.kind','fork'),arch='',big_endian=0 WHERE id=1")
    state={'header':'typedef int Word;'}
    monkeypatch.setattr(named.donor_sources,'imported',lambda *a:[('int sdkCompute(int x){return x;} ',state['header'],'donor defaults')])
    preparations=[]
    monkeypatch.setattr(named.donor_sources,'preprocess',lambda *args:(preparations.append(args) or args[-2:]))
    calls=[]
    monkeypatch.setattr(named.source_preparation,'attempt',judge(same,calls))
    named.run(same.twins,'destination',Job());named.run(same.twins,'destination',Job())
    assert len(preparations)==len(calls)==1
    state['header']='typedef unsigned int Word;'
    named.run(same.twins,'destination',Job())
    assert len(preparations)==len(calls)==2
    with same.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.compiler.flags',json('[\"-O1\"]')) WHERE id=2")
    named.run(same.twins,'destination',Job())
    assert len(preparations)==len(calls)==3


def test_name_or_score_alone_never_marks_matched(same,monkeypatch):
    monkeypatch.setattr(named.source_preparation,'attempt',lambda *a,**kw:0)
    assert named.run(same.twins,'destination',Job())==0
    assert same._function('sdkCompute','destination')['status']=='open'


def test_claims_cancellation_and_compile_budget_are_respected(same,monkeypatch):
    monkeypatch.setattr(named.source_preparation,'attempt',lambda *a,**kw:pytest.fail('judged busy work'))
    agent,_=same.local_agent('busy')
    with same.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(2,agent.id,time.time(),time.time()+60))
    assert named.run(same.twins,'destination',Job())==0
    with same.store.tx() as db:db.execute('DELETE FROM claims')
    job=Job();job.stop.set()
    assert named.run(same.twins,'destination',job)==0
    job=Job();named.free_candidates.budget(job,120,0)
    assert named.run(same.twins,'destination',job)==0


def test_compile_errors_cache_but_claim_races_remain_retryable(same,monkeypatch):
    calls=[]
    def error(*a,**kw):
        calls.append(1)
        raise NetworkError('Prepared C did not compile: unknown type')
    monkeypatch.setattr(named.source_preparation,'attempt',error)
    named.run(same.twins,'destination',Job());named.run(same.twins,'destination',Job())
    assert len(calls)==1
    with same.store.tx() as db:db.execute('DELETE FROM reuse_trials')
    def race(*a,**kw):
        calls.append(1)
        raise NetworkError('The function claim changed during preparation. Run the pass again.')
    monkeypatch.setattr(named.source_preparation,'attempt',race)
    named.run(same.twins,'destination',Job());named.run(same.twins,'destination',Job())
    assert len(calls)==3


def test_generated_names_and_wrong_architecture_are_not_identity_leads(same,monkeypatch):
    assert named.meaningful('guS2DInitBg')
    assert all(not named.meaningful(n) for n in ['func_80000000','FUN_80000000','sub_80000000','x','a::b'])
    with same.store.tx() as db:db.execute("UPDATE projects SET arch='arm' WHERE id=1")
    monkeypatch.setattr(named.source_preparation,'attempt',lambda *a,**kw:pytest.fail('wrong processor donor'))
    assert named.run(same.twins,'destination',Job())==0


def test_bounded_repairs_follow_the_donor_without_ai_calls(same,monkeypatch):
    monkeypatch.setattr(named.draft_repairs,'variants',lambda source,*a,labels,**kw:(labels.update({'fixed':'typed_temporary'}) or ['fixed']))
    calls=[]
    def attempt(worker,proj,fn,source,*args,**kwargs):
        return judge(same,calls,0 if source=='fixed' else 10)(worker,proj,fn,source,*args,**kwargs)
    monkeypatch.setattr(named.source_preparation,'attempt',attempt)
    assert named.run(same.twins,'destination',Job())==1
    assert len(calls)==2


def test_new_named_match_schedules_peer_even_when_compiled_sizes_differ(same):
    with same.store.tx() as db:
        db.execute('UPDATE functions SET size=400 WHERE id=1')
        db.execute('UPDATE functions SET size=32 WHERE id=2')
    cascade=same.cascades
    cascade.enabled=True;cascade.running=True
    cascade.notify(same.project_row('source'),1)
    assert cascade.pending=={'destination'}


def test_private_rom_header_promotion_does_not_repeat_unchanged_trial(same,monkeypatch):
    calls=[]
    attempt=judge(same,calls)
    def promote(*args,**kwargs):
        score=attempt(*args,**kwargs)
        with same.store.tx() as db:
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(2,2,'promoted private header','local_source_2')")
            db.execute("UPDATE functions SET unit='local_source_2' WHERE id=2")
        return score
    monkeypatch.setattr(named.source_preparation,'attempt',promote)
    named.run(same.twins,'destination',Job());named.run(same.twins,'destination',Job())
    assert len(calls)==1


def test_failed_first_donors_do_not_starve_a_third_project(same,monkeypatch):
    with same.store.tx() as db:
        for pid in (3,4):
            db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) SELECT ?,?,?,root,arch,big_endian,config FROM projects WHERE id=1",(pid,'donor'+str(pid),'donor'+str(pid)))
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')",(pid,))
            db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) SELECT ?,?,name,size,n_insns,target,relocs,vram,unit,status,replace(matched_source,'60',?) FROM functions WHERE id=1",(pid,pid,str(pid)))
    calls=[]
    monkeypatch.setattr(named.source_preparation,'attempt',judge(same,calls))
    named.run(same.twins,'destination',Job());named.run(same.twins,'destination',Job())
    assert any('x + 4' in c[0] for c in calls)
