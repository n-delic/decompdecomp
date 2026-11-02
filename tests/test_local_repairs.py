"""Local scheduling repairs and routing safeguards use synthetic source only."""
import json
from types import SimpleNamespace

import pytest

from decompnet import claim_stalls
from decompnet.app import pattern_repairs, release_repairs, temporary_repairs
from decompnet.app.permute import Permuter
from tests.test_claims import _game


def test_else_line_join_requires_debug_flags_and_masks_comments():
    source='void f(){if(p){send();\n } else {other();}}'
    assert any('send(); } else' in v for _,v in pattern_repairs.variants(source,'f',True))
    assert not any('send(); } else' in v for _,v in pattern_repairs.variants(source,'f',False))
    assert not pattern_repairs.variants('void f(){/* send();\n } else */}', 'f',True)


def test_float_computation_hoisted_before_store_with_typed_declaration():
    source='void f(){f32 x; if(p){FIELD(p,f32*,4)=x; send((f32)((f64)x * scale));}} void other(){}'
    out=temporary_repairs.variants(source,'f')
    assert len(out)==2
    assert all('f64 __repair_value;' in s and '__repair_value = (f64)x * scale;' in s and '((__repair_value))' not in s for s in out)
    assert all(s.index('__repair_value =') < s.index('FIELD') and s.endswith('void other(){}') for s in out)
    for source in ('void f(){x=next(); send((f64)y * scale);}',
                   'void f(){y=x; send((f64)y * scale);}',
                   'void f(){store=x; if(p)send((f64)y * scale);}',
                   'void f(){volatile int p; store=x;send((f64)y * scale);}'): 
        assert not temporary_repairs.variants(source,'f')


def test_scalar_parameter_split_reuses_only_same_type_dead_local():
    source='void f(s32 a,s32 b){s32 t; u32 u; a=b*a; use(a);}'
    out=temporary_repairs.variants(source,'f')
    assert any('t = b*a; a=t;' in s for s in out)
    assert all('u = b*a' not in s for s in out)
    assert not temporary_repairs.variants('void f(s32 a){s32 t;a=a*2;use(t);}','f')
    assert not temporary_repairs.variants('void f(s32 a){s32 t;a=next()*2;}','f')


@pytest.mark.parametrize('mode',['rom','fork'])
def test_stalled_claims_wait_for_changed_inputs_but_explicit_selection_works(tmp_path,monkeypatch,mode):
    net,pid=_game(tmp_path,[('f',10,'open'),('g',20,'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10 WHERE name=?',('void f(){}','f'))
    # Imported signatures observe the real source/header and build files as well.
    if mode=='fork':
        src=tmp_path/'source.c';src.write_text('void f(){}')
        fp=SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source='source.c'))
        monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
        monkeypatch.setattr(net.forks,'get',lambda _: (None,fp,None))
    fn=net._function('f','game');proj=net._project('game')
    for _ in range(3):claim_stalls.released(net,fn)
    assert claim_stalls.blocked(net,proj)==[fn['id']]
    a,_=net.local_agent('a')
    assert net.claim_row(a,'game',strategy='hardest')['name']=='g'
    net.release(a,'g','','game')
    assert net.claim_row(a,'game','f')['name']=='f'
    # Explicit selection is allowed even though automatic routing is paused.
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    if mode=='fork':
        src.write_text('void f(){changed();}')
    else:
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps({'kind':'rom','gp':4096}),pid))
    assert not claim_stalls.blocked(net,net._project('game'))


def test_stall_counter_resets_for_source_header_symbols_and_rule_version(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    fn=net._function('f','game');proj=net._project('game');stamp=claim_stalls.signature(net,proj,fn)
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',('void f(){x();}',))
    assert stamp != claim_stalls.signature(net,proj,net._function('f','game'))
    fn=net._function('f','game');stamp=claim_stalls.signature(net,proj,fn)
    monkeypatch.setattr(claim_stalls,'VERSION','new-rules')
    assert stamp != claim_stalls.signature(net,proj,fn)


@pytest.mark.parametrize('mode',['rom','fork'])
def test_release_repair_phase_is_targeted_bounded_and_skips_claimed_functions(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open'),('g',20,'open')])
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    monkeypatch.setattr(net,'fork_slug',lambda _: 'game' if mode=='fork' else None)
    seen=[]
    def round(self,agent,proj,state,**kwargs):
        seen.append(kwargs);state.repairs=2
    monkeypatch.setattr(Permuter,'_repair_round',round)
    fn=net._function('f','game')
    assert '2 trials' in release_repairs.run(net,'game',fn)
    assert seen==[{'function_id':fn['id'],'budget':6,'seconds':8}]
    net.links['game']=object()
    assert release_repairs.run(net,'game',fn)==''


def test_paused_queue_explains_how_to_resume(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    fn=net._function('f','game')
    for _ in range(3):claim_stalls.released(net,fn)
    assert net.claim_row(a,'game') is None
    assert 'unchanged candidates are paused' in net.nothing_to_claim(a,net._project('game'))


def test_targeted_phase_respects_lease_and_trial_budget(tmp_path,monkeypatch):
    import threading

    from decompnet.app import draft_repairs
    from decompnet.app.permute import State
    net,_=_game(tmp_path,[('f',10,'open'),('g',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    fn=net._function('f','game');net.claim_row(a,'game','f')
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'gcc','flags':[]}}),))
    machine,_=net.local_agent('permuter');p=Permuter(net);seen=[]
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**k:['candidate '+str(i) for i in range(12)])
    monkeypatch.setattr(p,'_score',lambda *a:seen.append(a[3]) or 10)
    monkeypatch.setattr(net,'_context',lambda *a:{'header':'','version':1})
    st=State(stop=threading.Event())
    p._repair_round(machine,net._project('game'),st,function_id=fn['id'],budget=6,seconds=8)
    assert not seen
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    p._repair_round(machine,net._project('game'),st,function_id=fn['id'],budget=6,seconds=8)
    assert len(seen)==6 and st.repairs==6
    assert seen[0] == fn['best_source']  # the verified baseline shares the six-compile allowance
    assert net.store.one('SELECT COUNT(*) AS n FROM reuse_trials WHERE function_id=?',fn['id'])['n']==5
    assert not net.store.one('SELECT * FROM claims')


@pytest.mark.parametrize('mode',['rom','fork'])
def test_agent_release_runs_phase_only_after_accepted_tried_release(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f')  # An accepted release requires the agent's actual lease.
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    net.claim_row(a,'game','f')
    monkeypatch.setattr(net,'claim_progress',lambda *a:{'compiles':1})
    monkeypatch.setattr(net,'_push_back',lambda *a:None)
    monkeypatch.setattr(net,'release',lambda *a:'Released')
    monkeypatch.setattr(net,'fork_slug',lambda _: 'game' if mode=='fork' else None)
    seen=[]
    monkeypatch.setattr(release_repairs,'run',lambda *a:seen.append(a[2]['name']) or ' automatic')
    assert 'automatic' in net.release_function(a,'f','tried','game') and seen==['f']
    monkeypatch.setattr(net,'_push_back',lambda *a:'Keep trying')
    assert net.release_function(a,'f','tried','game')=='Keep trying' and seen==['f']
    monkeypatch.setattr(net,'claim_progress',lambda *a:{'compiles':0})
    monkeypatch.setattr(net,'_push_back',lambda *a:None)
    net.release_function(a,'f','no compile','game')
    assert seen==['f']


@pytest.mark.parametrize('change',['header','symbols'])
def test_stall_retries_on_dependency_change(tmp_path,change):
    net,pid=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){use(data);}',))
    fn=net._function('f','game');proj=net._project('game')
    for _ in range(3):claim_stalls.released(net,fn)
    assert claim_stalls.blocked(net,proj)
    with net.store.tx() as db:
        if change=='header':
            db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',(pid,'a',1,'typedef int s32;'))
        else:
            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'data',4096,4,'data'))
    assert not claim_stalls.blocked(net,proj)


def test_exhausted_candidate_survives_rebuild_and_restart_but_new_rules_retry(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10',('void f(){}',))
    fn=net._function('f','game');proj=net._project('game')
    claim_stalls.released(net,fn,exhausted=True)
    assert claim_stalls.blocked(net,proj)==[fn['id']]
    net._generation={proj['id']:123}
    assert claim_stalls.blocked(net,proj)==[fn['id']]
    del net._generation
    assert claim_stalls.blocked(net,proj)==[fn['id']]
    agent,_=net.local_agent('manual')
    assert net.claim_row(agent,'game','f')['id']==fn['id']
    monkeypatch.setattr(claim_stalls,'repair_rules',lambda: [('new-rule','changed')])
    assert not claim_stalls.blocked(net,proj)
