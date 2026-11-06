"""Scheduler and durable evidence regressions use synthetic candidates, never provider calls."""
import json

import pytest

from decompnet import candidate_quality, candidate_scope, claim_stalls
from decompnet.app import agent_tokens, local_experiments
from decompnet.app.engine import RomNetwork
from tests.test_claims import _game


@pytest.mark.parametrize('mode',['rom','fork'])
def test_batch_honors_explicit_order_filters_leases_and_model_history(tmp_path,monkeypatch,mode):
    net,pid=_game(tmp_path,[(n,i,'open') for n,i in [('first',10),('second',20),('third',30),('filtered',40),('tried',15),('held',12)]])
    a,_=net.local_agent('worker');other,_=net.local_agent('other')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
        db.execute("UPDATE functions SET best_score=0 WHERE name='filtered'")
    net.claim_row(other,'game','held')
    tried=net._function('tried','game')['id']
    monkeypatch.setattr('decompnet.model_checks.tried',lambda *args:{tried})
    monkeypatch.setattr(net,'_brief',lambda fn,*args,**kwargs:fn['name'])
    monkeypatch.setattr(net.forks,'brief',lambda fn,*args,**kwargs:fn['name'])
    net.set_selection('game',a.name,{'strategy':'easiest','match_below':99,'max_tries':1,'stall_compiles':5,'model_untried':True})
    text=net.claim_many(a,'game',4)
    held=net.store.all('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?',a.id)
    assert {r['name'] for r in held}=={'first','second','third'}
    assert 'CLAIMED 3 small functions' in text


def test_unrelated_dependencies_do_not_reopen_but_used_symbols_and_headers_do(tmp_path):
    net,pid=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',('void f(){use(data);}',))
    fn=net._function('f','game');proj=net._project('game')
    claim_stalls.released(net,fn,exhausted=True)
    before=agent_tokens.target_scope(net,fn,'revision')
    with net.store.tx() as db:
        db.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?)',(pid,'unrelated',123,4,'data',None))
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',(pid,'other',1,'int unrelated;'))
    assert claim_stalls.blocked(net,proj)==[fn['id']]
    assert before==agent_tokens.target_scope(net,fn,'revision')
    with net.store.tx() as db:
        db.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?)',(pid,'data',4096,4,'data',None))
    assert not claim_stalls.blocked(net,proj)
    assert before!=agent_tokens.target_scope(net,fn,'revision')
    claim_stalls.released(net,fn,exhausted=True)
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',(pid,'a',2,'extern int data;'))
    assert not claim_stalls.blocked(net,proj)


def test_budget_exhausted_release_pauses_immediately(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f');fn=net._function('f','game')
    net.set_selection('game',a.name,{'stall_compiles':2})
    for _ in range(3):net._record(fn,a,10,'void f(){}',1)
    net.release(a,'f','No progress.','game')
    assert claim_stalls.blocked(net,net._project('game'))==[fn['id']]
    assert net.claim_row(a,'game','f')['id']==fn['id']  # explicit override remains available


def test_quality_report_and_alternative_survive_restart_without_replacing_best(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f');fn=net._function('f','game')
    original='int f(){int x;return x;}'
    alternative='int f(){return 1;}'
    net._record(fn,a,10,original,1);net._record(fn,a,20,alternative,1)
    net.release(a,'f','Historical candidate uses an uninitialized value; a defined alternative was compiled.','game')
    fresh=RomNetwork(tmp_path/'app.db',tmp_path);fn=fresh._function('f','game')
    assert fn['best_source']==original and fn['best_score']==10
    assert 'agent report, not proven' in '\n'.join(candidate_quality.brief(fresh,fn))
    view=agent_tokens.function_view(fresh,a,'game','f','quality')
    assert alternative in view and 'score 20' in view and 'quality unverified' in view


@pytest.mark.parametrize('mode',['rom','fork'])
def test_local_trial_misses_survive_new_claims_and_unrelated_edits(tmp_path,monkeypatch,mode):
    net,pid=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\nint a;\nreturn a;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?,best_score=10',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game');seen=[]
    net._record(fn,a,10,source,1)
    monkeypatch.setattr('decompnet.app.agent_evidence.baseline',lambda *args:None)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    def compile(agent,function,text,slug):
        seen.append(text);net._record(fn,agent,20,text,1);return 'score 20'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    def run():
        _,rev=agent_tokens.candidate(net,a,'game','f')
        return local_experiments.run(net,a,'game','f',rev,['signedness'],6)
    run();assert len(seen)==1
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    monkeypatch.setattr(net,'compile_and_diff',compile)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?)',(pid,'unrelated',900,4,'data',None))
    net.claim_row(a,'game','f')
    assert 'No untried' in run() and len(seen)==1
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',(pid,'a',2,'typedef int changed;'))
    run();assert len(seen)==2


def test_imported_quoted_includes_track_used_headers(tmp_path):
    (tmp_path/'include').mkdir();(tmp_path/'source.c').write_text('#include "used.h"\n')
    (tmp_path/'include'/'used.h').write_text('int used;')
    (tmp_path/'include'/'other.h').write_text('int other;')
    before=candidate_scope.dependencies(tmp_path,'source.c')
    (tmp_path/'include'/'other.h').write_text('int other_changed;')
    assert candidate_scope.dependencies(tmp_path,'source.c')==before
    (tmp_path/'include'/'used.h').write_text('long used;')
    assert candidate_scope.dependencies(tmp_path,'source.c')!=before


def test_legacy_quality_notes_expose_unanchored_compiled_alternatives(tmp_path):
    import time
    net,pid=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net._record(fn,a,10,'int f(){int x;return x;}',1)
    net._record(fn,a,20,'int f(){return 2;}',1)
    with net.store.tx() as db:
        db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                   (pid,fn['id'],a.id,'Historical candidate uses an uninitialized value.',time.time()))
    fn=net._function('f','game')
    assert 'unanchored' in '\n'.join(candidate_quality.brief(net,fn))
    assert 'return 2' in candidate_quality.view(net,fn)
    assert not candidate_quality.records(net,fn)  # read-only legacy view never fabricates an anchored finding


def test_compiler_range_changes_only_invalidate_affected_functions(tmp_path):
    net,pid=_game(tmp_path,[('f',10,'open')])
    cfg={'kind':'rom','compiler':{'toolchain':'example','flags':['-O2']},
         'compiler_ranges':[{'start':4096,'end':8192,'toolchain':'other','flags':['-O1']}]}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        db.execute('UPDATE functions SET vram=100')
    fn=net._function('f','game');before=agent_tokens.target_scope(net,fn,'r')
    cfg['compiler_ranges'][0]['flags']=['-O3']
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    assert agent_tokens.target_scope(net,fn,'r')==before
    cfg['compiler']['flags']=['-O0']
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    assert agent_tokens.target_scope(net,fn,'r')!=before


def test_imported_original_object_changes_invalidate_trial_history(tmp_path,monkeypatch):
    from types import SimpleNamespace
    net,_=_game(tmp_path,[('f',10,'open')]);target=tmp_path/'original.o';target.write_bytes(b'original')
    fp=SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source=None,target=target))
    monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
    monkeypatch.setattr(net.forks,'get',lambda _: (None,fp,None))
    fn=net._function('f','game');before=agent_tokens.target_scope(net,fn,'r')
    target.write_bytes(b'changed-original')
    assert agent_tokens.target_scope(net,fn,'r')!=before


def test_called_matched_helpers_refresh_repair_evidence_only_for_their_callers(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open'),('fill',20,'open'),('other',30,'open')])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){fill(0);}' WHERE name='f'")
    fn=net._function('f','game');proj=net._project('game')
    before=candidate_scope.key(net,proj,fn)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='void other(){}' WHERE name='other'")
    assert candidate_scope.key(net,proj,fn)==before
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='void fill(short *p){p[0]=1;p[1]=2;}' WHERE name='fill'")
    assert candidate_scope.key(net,proj,fn)!=before
    evidence=local_experiments.draft_repairs.capture_followup_repairs.callee_evidence(net,proj,fn,fn['best_source'])
    assert 'void fill(' in evidence and 'other' not in evidence
