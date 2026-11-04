import json

import pytest

from decompnet.app import source_preparation as prep
from decompnet.app.source_queue import Queue
from decompnet.network import Agent, NetworkError
from tests.test_prepared_evidence import prepared_net


class Jobs:
    def __init__(self):self.work={}
    def start(self,kind,work):
        key=str(len(self.work));self.work[key]=work;return key


class Log:
    def __init__(self):self.lines=[];self.values=[]
    def __call__(self,line):self.lines.append(line)
    def progress(self,value):self.values.append(value)


def setup():
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None;net.links={}
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE slug=?',slug)
    net.local_agent=lambda name:(Agent(2,name,None),'')
    with net.store.tx() as db:
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'source-queue-1','queue')")
        db.execute('INSERT INTO settings VALUES(?,?)',('source-preparation:1',json.dumps({'enabled':True,'mappings':'f = original @ game.c'})))
    return net,proj,Queue(net,Jobs())


def test_durable_worker_resumes_only_its_claim_and_keeps_assembly_approval(monkeypatch):
    net,proj,q=setup()
    data=q.start('game',True);assert data['job']=='0'
    with q.lock:
        state=q._load(1);state['items'][0]['state']='running';q._save(1,state)
    with net.store.tx() as db:
        db.execute('UPDATE claims SET agent_id=2')
        db.execute("INSERT INTO functions(id,project_id,name,n_insns) VALUES(2,1,'other',1)")
        db.execute('INSERT INTO claims VALUES(2,1,1,9999999999)')
    restored=Queue(net,Jobs());restored.resume()
    assert not net.store.one('SELECT * FROM claims WHERE function_id=1')
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=2')['agent_id']==1
    assert restored.status('game')['include_assembly'] is True
    assert restored.status('game')['items'][0]['state']=='pending'
    calls=[]
    def one(slug,name,agent,assembly,log):
        calls.append((name,assembly));return {'matched':True,'starting_score':40,'new_score':0}
    monkeypatch.setattr(restored,'_one',one);log=Log()
    restored.jobs.work['0'](log)
    assert calls==[('f',True)] and restored.status('game')['state']=='done'
    assert log.values[-1]['finished']==1


def test_pause_preserves_unfinished_item_and_resume_does_not_repeat_finished(monkeypatch):
    net,proj,q=setup();q.start('game')
    def one(*args):q.stop('game');return {'matched':False,'paused':True}
    monkeypatch.setattr(q,'_one',one);q.jobs.work['0'](Log())
    assert q.status('game')['state']=='paused' and q.status('game')['items'][0]['state']=='pending'
    q.start('game',True)
    assert q.status('game')['include_assembly'] is False
    monkeypatch.setattr(q,'_one',lambda *args:{'matched':True})
    q.jobs.work['1'](Log())
    assert q.status('game')['items'][0]['state']=='matched'


def test_queue_continues_after_function_error_and_rejects_imported_builds(monkeypatch):
    net,proj,q=setup();q.start('game')
    monkeypatch.setattr(q,'_one',lambda *args:(_ for _ in ()).throw(NetworkError('compile failed')))
    q.jobs.work['0'](Log())
    assert q.status('game')['state']=='done' and q.status('game')['items'][0]['error']=='compile failed'
    net.fork_slug=lambda s:'fork'
    with pytest.raises(NetworkError,match='Imported builds'):q.start('game')


def test_assembly_is_opt_in_and_only_mapped_or_prepared_candidates_are_selected():
    net,proj,q=setup()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm'")
        db.execute("INSERT INTO functions(project_id,name,n_insns,status) VALUES(1,'unmapped',1,'asm')")
    assert q.start('game')['items']==[]
    # Finish empty queue before starting a new approved one.
    q.jobs.work['0'](Log())
    assert [r['function'] for r in q.start('game',True)['items']]==['f']


def test_prepared_assembly_publication_is_atomic_and_requires_exact_verdict():
    net,proj,fn=prepared_net()
    with net.store.tx() as db:db.execute("UPDATE functions SET status='asm',meta=?",(json.dumps({'not_c':True,'asm_code':True}),))
    fn=net.store.one('SELECT * FROM functions WHERE id=1');expected=prep.inputs(net,proj,fn)
    with pytest.raises(NetworkError,match='exact prepared bundle'):
        net._record(fn,Agent(1,'autopilot',None),10,'wrong',0,prepared_header='types',prepared_inputs=expected,prepared_assembly=True)
    assert net.store.one('SELECT status FROM functions')['status']=='asm'
    net._record(fn,Agent(1,'autopilot',None),0,'exact',0,matched=True,prepared_header='types',prepared_inputs=expected,prepared_assembly=True,restoring_exact=True)
    current=net.store.one('SELECT * FROM functions WHERE id=1')
    assert current['status']=='matched' and not json.loads(current['meta']).get('asm_code')


def test_stale_claim_rolls_back_assembly_reopening_and_source():
    net,proj,fn=prepared_net()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm'");db.execute('DELETE FROM claims')
    fn=net.store.one('SELECT * FROM functions WHERE id=1')
    with pytest.raises(NetworkError,match='claim changed'):
        net._record(fn,Agent(1,'autopilot',None),0,'exact',0,matched=True,prepared_header='types',prepared_inputs=prep.inputs(net,proj,fn),prepared_assembly=True,restoring_exact=True)
    assert net.store.one('SELECT status,best_source FROM functions')['status']=='asm'
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


def test_exact_bundle_is_freshly_applied_with_queue_identity_and_explicit_assembly(monkeypatch):
    net,proj,q=setup()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm'")
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(1,?,?,?,?)',('local-source-profile-v2:exact','0','test',json.dumps({'exact':True,'inputs':'historical','source':'real C','header':'types','toolchain':'ido-5.3','flags':['-O3']})))
    from decompnet.app import prepared_evidence
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    calls=[]
    def apply(n,p,key,log,**options):calls.append((key,options));return {'matched':True}
    monkeypatch.setattr(prepared_evidence,'apply',apply)
    assert q._one('game','f',Agent(2,'source-queue-1',None),True,Log())['matched']
    assert calls[0][1]['refresh'] and calls[0][1]['include_assembly'] and calls[0][1]['agent'].id==2
    assert not q._one('game','f',Agent(2,'source-queue-1',None),False,Log())['matched']
    assert len(calls)==1


def test_start_failure_does_not_leave_a_phantom_active_job(monkeypatch):
    net,proj,q=setup()
    monkeypatch.setattr(q.jobs,'start',lambda *args:(_ for _ in ()).throw(OSError('thread unavailable')))
    with pytest.raises(OSError):q.start('game')
    assert not q.active and q.status('game')['state']=='interrupted'


def test_source_assembly_queue_records_verification_without_a_c_match(monkeypatch):
    net,proj,q=setup()
    with net.store.tx() as db:
        db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps({'enabled':True,'assembly_mappings':'f = original @ game.S'}),'source-preparation:1'))
    q.start('game')
    monkeypatch.setattr(q,'_one',lambda *args:{'matched':False,'assembly_verified':True})
    q.jobs.work['0'](Log())
    item=q.status('game')['items'][0]
    assert item['state']=='verified_assembly' and item['matched'] is False
