"""Model-local exhaustion and explicit bounded retry without provider calls or live state."""
import pytest

from decompnet import claim_stalls, pause_scope
from decompnet.app import retry_paused
from decompnet.network import NetworkError
from tests.test_claims import _game


def setup(tmp_path,n=1):
    net,_=_game(tmp_path,[(f'f{i}',10,'open') for i in range(n)])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){}',best_score=10")
        db.execute('CREATE TABLE helpers(project TEXT,name TEXT,model TEXT)')
        db.execute('CREATE TABLE helper_sessions(id INTEGER PRIMARY KEY,project TEXT,agent TEXT,model TEXT,ended_at REAL,started_at REAL DEFAULT 0)')
        db.executemany('INSERT INTO helpers VALUES(?,?,?)',[('game','a','alpha#low'),('game','b','beta'),('game','c','alpha#low')])
    return net,[net.local_agent(x)[0] for x in ('a','b','c')]


@pytest.mark.parametrize('mode',['rom','fork'])
def test_exhaustion_is_shared_only_by_recorded_model(tmp_path,monkeypatch,mode):
    net,(a,b,c)=setup(tmp_path)
    if mode=='fork':
        from types import SimpleNamespace
        source=tmp_path/'source.c';source.write_text('void f(){}')
        fp=SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source='source.c'))
        monkeypatch.setattr(net,'fork_slug',lambda _:'game')
        monkeypatch.setattr(net.forks,'get',lambda _:(None,fp,None))
    fn=net._function('f0','game');proj=net._project('game')
    claim_stalls.released(net,fn,exhausted=True)  # old unattributed state must not poison new models
    assert not claim_stalls.blocked(net,proj,a)
    for _ in range(3):claim_stalls.released(net,fn,agent=a)
    assert claim_stalls.blocked(net,proj,a)==claim_stalls.blocked(net,proj,c)==[fn['id']]
    assert not claim_stalls.blocked(net,proj,b)
    assert net.claim_row(a,'game') is None
    assert net.claim_row(b,'game')['id']==fn['id']


def test_unknown_workers_remain_independent_and_active_session_model_wins(tmp_path):
    net,(a,b,c)=setup(tmp_path)
    fn=net._function('f0','game');proj=net._project('game')
    with net.store.tx() as db:
        db.execute('INSERT INTO helper_sessions(id,project,agent,model,ended_at) VALUES(1,?,?,?,NULL)',('game','a','beta'))
    claim_stalls.released(net,fn,agent=a,exhausted=True)
    assert claim_stalls.blocked(net,proj,b)==[fn['id']]
    assert not claim_stalls.blocked(net,proj,c)
    with net.store.tx() as db:db.execute('UPDATE helper_sessions SET ended_at=1')
    assert not claim_stalls.blocked(net,proj,a)
    x,_=net.local_agent('unknown-x');y,_=net.local_agent('unknown-y')
    claim_stalls.released(net,fn,agent=x,exhausted=True)
    assert claim_stalls.blocked(net,proj,x)==[fn['id']] and not claim_stalls.blocked(net,proj,y)
    assert pause_scope.identity(net,proj,a)==pause_scope.identity(net,proj,c)
    with net.store.tx() as db:db.execute("UPDATE helpers SET model='auto'")
    assert pause_scope.identity(net,proj,a)!=pause_scope.identity(net,proj,c)


def test_retry_respects_limit_claims_other_models_and_repauses_after_one_turn(tmp_path):
    net,(a,b,c)=setup(tmp_path,8);proj=net._project('game')
    for fn in net.store.all('SELECT * FROM functions'):
        for worker in (a,b):claim_stalls.released(net,fn,agent=worker,exhausted=True)
    net.claim_row(b,'game','f0')
    result=retry_paused.retry(net,'game',a,5)
    assert result['resumed']==5 and result['functions']==['f1','f2','f3','f4','f5']
    assert len(claim_stalls.blocked(net,proj,b))==8
    assert len(claim_stalls.blocked(net,proj,c))==3
    fn=net._function('f1','game');claim_stalls.released(net,fn,agent=c)
    assert fn['id'] in claim_stalls.blocked(net,proj,a)
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


@pytest.mark.parametrize('limit',[0,51,True,2.5,'5'])
def test_retry_rejects_invalid_allowances(tmp_path,limit):
    net,(a,_,_)=setup(tmp_path)
    with pytest.raises(NetworkError):retry_paused.retry(net,'game',a,limit)


def test_retry_keeps_work_filters_and_input_invalidation(tmp_path):
    net,(a,_,_)=setup(tmp_path)
    fn=net._function('f0','game');proj=net._project('game')
    claim_stalls.released(net,fn,agent=a,exhausted=True)
    net.set_selection('game',a.name,{'match_below':1})
    assert retry_paused.retry(net,'game',a)['resumed']==0
    net.set_selection('game',a.name,{})
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='void f(){other();}'")
    assert not claim_stalls.blocked(net,proj,a)
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='void f(){}'")
    assert claim_stalls.blocked(net,proj,a)==[fn['id']]
    claim_stalls.resume(net,fn)
    assert not claim_stalls.blocked(net,proj,a)


def test_retry_bypasses_only_selected_cooldown_and_consumes_on_claim(tmp_path):
    from decompnet.network import Network
    net,(a,_,c)=setup(tmp_path)
    fn=net.claim_row(a,'game','f0');Network.release(net,a,'f0','','game')
    claim_stalls.released(net,fn,agent=a,exhausted=True)
    assert net.claim_row(a,'game') is None
    assert retry_paused.retry(net,'game',a,1)['resumed']==1
    assert net.claim_row(a,'game')['id']==fn['id']
    assert claim_stalls.blocked(net,net._project('game'),c)==[fn['id']]
    # Simulate an interrupted worker whose lease was removed: its grant cannot run twice.
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    assert net.claim_row(c,'game') is None


def test_scoped_exhaustion_and_retry_grants_survive_reopening(tmp_path):
    from decompnet.app.engine import RomNetwork
    net,(a,b,_)=setup(tmp_path)
    fn=net._function('f0','game');claim_stalls.released(net,fn,agent=a,exhausted=True)
    fresh=RomNetwork(tmp_path/'app.db',tmp_path)
    assert claim_stalls.blocked(fresh,fresh._project('game'),a)==[fn['id']]
    assert not claim_stalls.blocked(fresh,fresh._project('game'),b)
    retry_paused.retry(fresh,'game',a,1)
    again=RomNetwork(tmp_path/'app.db',tmp_path)
    assert again.claim_row(a,'game')['id']==fn['id']
    assert claim_stalls.blocked(again,again._project('game'),a)==[fn['id']]


def test_retry_does_not_reset_attempt_cap(tmp_path):
    net,(a,_,_)=setup(tmp_path);fn=net._function('f0','game')
    claim_stalls.released(net,fn,agent=a,exhausted=True)
    net.set_selection('game',a.name,{'max_tries':1})
    with net.store.tx() as db:db.execute('INSERT INTO attempts(function_id,agent_id,compiled) VALUES(?,?,0)',(fn['id'],a.id))
    assert retry_paused.retry(net,'game',a,1)['resumed']==0


def test_claim_rechecks_a_grant_consumed_by_another_worker_before_transaction(tmp_path,monkeypatch):
    import json
    net,(a,_,_)=setup(tmp_path);fn=net._function('f0','game')
    claim_stalls.released(net,fn,agent=a,exhausted=True);retry_paused.retry(net,'game',a,1)
    expire=net._expire_claims
    def interleave(db):
        expire(db)
        for row in db.execute("SELECT key,value FROM settings WHERE key LIKE 'agent-stall:%'").fetchall():
            record=json.loads(row['value']);record.update(retry_grant=False,count=3)
            db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(record),row['key']))
    monkeypatch.setattr(net,'_expire_claims',interleave)
    assert net.claim_row(a,'game') is None
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==0


def test_same_worker_switching_models_does_not_inherit_old_release_cooldown(tmp_path):
    import time

    from decompnet.network import Network
    net,(a,_,_)=setup(tmp_path)
    net.claim_row(a,'game','f0');Network.release(net,a,'f0','','game')
    assert net.claim_row(a,'game') is None
    with net.store.tx() as db:
        db.execute('INSERT INTO helper_sessions(id,project,agent,model,ended_at) VALUES(1,?,?,?,?)',('game','a','alpha#low',time.time()))
        db.execute("UPDATE helpers SET model='beta' WHERE name='a'")
    assert net.claim_row(a,'game')['name']=='f0'


def test_retry_stops_fingerprinting_at_limit_and_reports_progress(tmp_path,monkeypatch):
    net,(a,_,_)=setup(tmp_path,70)
    for fn in net.store.all('SELECT * FROM functions'):
        claim_stalls.released(net,fn,agent=a,exhausted=True)
    original=claim_stalls.signature
    checked=[]
    def signature(net,proj,fn,common=None):
        checked.append(fn['id'])
        return original(net,proj,fn,common)
    monkeypatch.setattr(claim_stalls,'signature',signature)
    logs=[]
    result=retry_paused.retry(net,'game',a,5,log=logs.append)
    assert result['functions']==['f0','f1','f2','f3','f4']
    assert len(checked)==5  # Selected stamps are reused when recording the grants.
    assert logs[-1]=='Checked 5 candidates · 5 / 5 ready'


def test_retry_rechecks_candidate_changes_between_grants(tmp_path):
    net,(a,_,_)=setup(tmp_path,3)
    for fn in net.store.all('SELECT * FROM functions'):
        claim_stalls.released(net,fn,agent=a,exhausted=True)
    def progress(line):
        if line=='Checked 1 candidates · 1 / 2 ready':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET status='matched' WHERE name='f1'")
    result=retry_paused.retry(net,'game',a,2,log=progress)
    assert result['functions']==['f0','f2']


@pytest.mark.parametrize('source',[None,''])
def test_source_free_releases_pause_only_repeated_model_and_invalidate_new_inputs(tmp_path,source):
    net,(a,b,c)=setup(tmp_path);proj=net._project('game')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,seed=NULL,best_score=NULL',(source,))
    fn=net._function('f0','game')
    for i in range(3):
        assert net.claim_row(a,'game','f0')['id']==fn['id']
        net.release(a,'f0','Raw jump-word entry requires independent review.','game',not_c=True)
        assert bool(claim_stalls.blocked(net,proj,a))==(i==2)
    assert claim_stalls.blocked(net,proj,c)==[fn['id']]
    assert not claim_stalls.blocked(net,proj,b)
    assert net._function('f0','game')['status']=='open'
    assert not net.store.one('SELECT 1 FROM attempts')
    assert net.claim_row(a,'game') is None
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET target=?',(b'new synthetic target',))
            assert not claim_stalls.blocked(net,proj,a)
            raise ValueError('rollback')
    assert claim_stalls.blocked(net,proj,a)==[fn['id']]
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'typedef int new_type;','')",(proj['id'],))
            assert not claim_stalls.blocked(net,proj,a)
            raise ValueError('rollback')
    assert claim_stalls.blocked(net,proj,a)==[fn['id']]
    assert net.claim_row(b,'game')['id']==fn['id']
    # A new draft is an actual new opportunity, without erasing prior evidence.
    with net.store.tx() as db:db.execute("UPDATE functions SET seed='void f0(void){return;}'")
    assert not claim_stalls.blocked(net,proj,a)
    with net.store.tx() as db:db.execute('UPDATE functions SET seed=NULL')
    assert claim_stalls.blocked(net,proj,a)==[fn['id']]
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='void f0(void){}'")
    assert not claim_stalls.blocked(net,proj,a)


def test_source_free_explicit_retry_grants_one_turn_without_any_match_credit(tmp_path):
    net,(a,b,_)=setup(tmp_path);proj=net._project('game')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=NULL,seed=NULL,best_score=NULL')
    fn=net._function('f0','game')
    for _ in range(3):claim_stalls.released(net,fn,agent=a)
    assert retry_paused.retry(net,'game',a,1)['resumed']==1
    assert net.claim_row(a,'game')['id']==fn['id']
    net.release(a,'f0','Still needs independent investigation.','game')
    assert claim_stalls.blocked(net,proj,a)==[fn['id']]
    assert not claim_stalls.blocked(net,proj,b)
    assert net._function('f0','game')['status']=='open'
    assert not net.store.one('SELECT 1 FROM attempts')
