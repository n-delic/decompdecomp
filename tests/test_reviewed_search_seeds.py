import hashlib
from types import SimpleNamespace

import pytest

from decompnet.app import (
    allocation_search,
    allocation_targeting,
    compiler_experiments,
    deep_permuter,
    draft_repairs,
    reviewed_seed_search,
)
from decompnet.app import reviewed_search_seeds as seeds
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT, HEADER
from tests.test_register_cycle_search import ROWS


def prepare(monkeypatch, changed=False):
    net = setup(monkeypatch)
    net.is_rom = lambda _:True
    proj, fn = net.project_row('game'), net.function()
    source = fn['best_source'].replace('a + 1','a + 2')
    if changed:source = source.replace('s32 f(', 'void f(')
    record = dict(agent=AGENT.id,source=source,header=HEADER,compiler='gcc-2.7.2-kmc',flags='-O2',
                  seed_scope=seeds.scope(net,proj,fn,source),result={'score':10,'exact':False})
    compiler_experiments.save_trial(net,fn,'trial',record)
    seeds.review(net,AGENT,'game','f','trial','Reviewed real operation and caller uses.')
    return net,record


def test_review_is_explicit_profile_and_input_scoped(monkeypatch):
    net, trial = prepare(monkeypatch)
    original = dict(net.function())
    assert len(seeds.selected(net,net.project_row('game'),net.function()))==1
    assert dict(net.function())==original
    with net.store.tx() as db:db.execute("UPDATE functions SET target=? WHERE id=1",(b'changed',))
    assert not seeds.selected(net,net.project_row('game'),net.function())
    with pytest.raises(NetworkError,match='changed'):
        seeds.review(net,AGENT,'game','f','trial','Reviewed source and callers.')
    seeds.review(net,AGENT,'game','f','trial',enabled=False)
    assert not net.store.one('SELECT enabled FROM reviewed_search_seeds')['enabled']


def test_caller_header_and_profile_changes_invalidate_review(monkeypatch):
    net, trial = prepare(monkeypatch)
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,best_source) VALUES(1,'caller',4,1,'void caller(){f(1);}')")
    assert not seeds.selected(net,net.project_row('game'),net.function())
    with pytest.raises(NetworkError):seeds.review(net,AGENT,'game','f','trial','Reviewed caller declaration.')
    with net.store.tx() as db:db.execute("DELETE FROM functions WHERE name='caller'")
    assert seeds.selected(net,net.project_row('game'),net.function())
    net._context=lambda *a:{'header':HEADER+'\n#define X 1','version':2}
    assert not seeds.selected(net,net.project_row('game'),net.function())


def test_historical_wrong_function_and_unscored_trials_cannot_be_reviewed(monkeypatch):
    net, trial = prepare(monkeypatch)
    fn=net.function()
    for key, changes in [('old',{'seed_scope':None}),('unscored',{'result':{'score':None}}),('nan',{'result':{'score':float('nan')}})]:
        compiler_experiments.save_trial(net,fn,key,dict(trial,**changes))
        with pytest.raises(NetworkError):seeds.review(net,AGENT,'game','f',key,'Review the caller and operation.')
    with pytest.raises(NetworkError):seeds.review(net,AGENT,'game','f','absent','Review the caller and operation.')
    with pytest.raises(NetworkError):seeds.review(net,AGENT,'game','f','trial','too short')
    with pytest.raises(NetworkError):seeds.review(net,AGENT,'game','f','trial',enabled=1)


@pytest.mark.parametrize('changed',[True,False])
def test_reviewed_exact_rechecks_only_unchanged_contract(monkeypatch,changed):
    net,trial=prepare(monkeypatch,changed)
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    before=dict(net.function());judged=[];promoted=[]
    net._judge_with=lambda *args:(judged.append(args[-1]) or (True,None,''))
    monkeypatch.setattr(allocation_search,'saved',lambda *args:{'skipped':'unsupported'})
    def ordinary(*args):
        promoted.append(args[-3]);search.tested+=1
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched',best_score=0")
        return 0
    monkeypatch.setattr(search,'_score_current',ordinary)
    outcome=reviewed_seed_search.run(search,AGENT,net.project_row('game'),net.function())
    assert outcome['diagnostic_exact'][0]['diagnostic_only']==changed
    assert len(judged)==1 and len(promoted)==(0 if changed else 1)
    assert outcome['promotion_compiles']==(0 if changed else 1)
    if changed:assert dict(net.function())==before
    assert outcome['model_tokens']==0


def test_bounded_diagnostic_misses_never_replace_best(monkeypatch):
    net,trial=prepare(monkeypatch,True);before=dict(net.function())
    net._judge_with=lambda *args:(False,SimpleNamespace(score=1,rows=[]),'')
    monkeypatch.setattr(allocation_search,'saved',lambda *args:{'skipped':'unsupported'})
    search=deep_permuter.Search(net,1,3,Log(),net.function())
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**kw:['one','two','three','four'])
    outcome=reviewed_seed_search.run(search,AGENT,net.project_row('game'),net.function())
    assert search.tested<=3 and outcome['candidate_compiles']==search.tested
    assert dict(net.function())==before


def test_interface_exact_stops_broad_deep_search(monkeypatch):
    net,trial=prepare(monkeypatch,True)
    monkeypatch.setattr('decompnet.app.boundary_repairs.blocked',lambda *a:set())
    net._judge_with=lambda *a:(True,None,'')
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{'skipped':'unsupported'})
    monkeypatch.setattr(deep_permuter.Search,'_compiler_preflight',lambda *a:pytest.fail('must stop at review boundary'))
    outcome=deep_permuter.run(net,'game','f',1,20,Log())
    assert not outcome['matched'] and outcome['reason']=='Exact diagnostic needs ordinary review'
    assert outcome['tested']==1 and outcome['reviewed_seeds']['diagnostic_exact']


def test_source_mapped_priorities_target_existing_operation_not_dummy_uses():
    source='int f(int a,int b){\nint x;\nx=a/b;\nreturn x;\n}'
    evidence={'allocation':{'values':[
        dict(pseudo=70,aliases=['s4'],references=10,live_insns=91,legacy_weight=30/91,source_lines=[3]),
        dict(pseudo=71,aliases=['s5'],references=7,live_insns=41,legacy_weight=14/41,source_lines=[4])]},
        'value_trace':{'values':[dict(aliases=['s4'],source_lines=[3]),dict(aliases=['s5'],source_lines=[4])], 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'header_sha256':hashlib.sha256(b'').hexdigest()}}
    points=allocation_targeting.targets(source,'',ROWS,evidence)
    assert points[0]['additional_weighted_refs']==1
    proposals=allocation_targeting.proposals(source,'f','',ROWS,evidence)
    assert proposals and all(c.count('x=a/b;')==1 for _,c in proposals)
    assert 'L3: x=a/b;' in allocation_targeting.brief(source,'',ROWS,evidence)
    assert not allocation_targeting.targets(source+' ','',ROWS,evidence)
    assert not allocation_targeting.targets(source,'changed',ROWS,evidence)


def test_last_budget_exact_is_retained_without_unsafe_promotion(monkeypatch):
    net,trial=prepare(monkeypatch)
    net._judge_with=lambda *a:(True,None,'')
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{'skipped':'unsupported'})
    search=deep_permuter.Search(net,1,1,Log(),net.function())
    result=reviewed_seed_search.run(search,AGENT,net.project_row('game'),net.function())
    assert result['diagnostic_exact'] and not result['matched'] and search.tested==1
    assert net.function()['status']=='open'


def test_parallel_review_boundary_prevents_other_paths(monkeypatch):
    net,trial=prepare(monkeypatch,True)
    monkeypatch.setattr('decompnet.app.boundary_repairs.blocked',lambda *a:set())
    net._judge_with=lambda *a:(True,None,'')
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{'skipped':'unsupported'})
    monkeypatch.setattr(deep_permuter.Search,'_compiler_preflight',lambda *a:pytest.fail('must stop at review boundary'))
    result=deep_permuter.run(net,'game','f',1,20,Log(),threads_per_function=2)
    assert not result['matched'] and result['reason']=='Exact diagnostic needs ordinary review'
    assert result['tested']==1 and result['reviewed_seeds']['diagnostic_exact']


def test_compact_brief_is_bounded_and_never_compiles(monkeypatch):
    from decompnet.app import search_brief, search_evidence
    net,trial=prepare(monkeypatch)
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{})
    monkeypatch.setattr(search_evidence,'brief',lambda *a:'negative family trial: worse')
    net._judge_with=lambda *a:pytest.fail('read must not compile')
    text=search_brief.read(net,AGENT,'game','f')
    assert 'negative family trial' in text and 'trial' in text and 'not exhaustive' in text
    assert len(text)<8000 and net.function()['best_source'] not in text


def test_replay_refuses_live_or_unmarked_homes(monkeypatch,tmp_path):
    from decompnet.app import reviewed_seed_benchmark
    net,trial=prepare(monkeypatch)
    with pytest.raises(NetworkError,match='disposable'):
        reviewed_seed_benchmark.run(net,'game','f',net.function()['best_source'],trial['source'],
                                   'Reviewed the caller.',tmp_path,Log())


def test_allocation_cache_tracks_symbols_used_only_by_alternative(monkeypatch):
    net,trial=prepare(monkeypatch)
    proj,fn=net.project_row('game'),net.function()
    source=trial['source']+'\nextern int other;\n'
    with net.store.tx() as db:db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'other',4096,4,'data')")
    before=allocation_search.key(net,proj,fn,source)
    with net.store.tx() as db:db.execute("UPDATE symbols SET address=8192 WHERE name='other'")
    assert allocation_search.key(net,proj,fn,source)!=before


@pytest.mark.parametrize('mode',['fork','room'])
def test_unsupported_seed_modes_leave_ordinary_search_available(monkeypatch,mode):
    net,trial=prepare(monkeypatch)
    if mode=='fork':net.fork_slug=lambda slug:'fork'
    else:net.links={'game':object()}
    assert not seeds.selected(net,net.project_row('game'),net.function())
    with pytest.raises(NetworkError,match='local ROM'):
        seeds.review(net,AGENT,'game','f','trial','Review callers and declaration.')


def test_mapped_targeting_runs_before_broad_generators(monkeypatch):
    net,trial=prepare(monkeypatch,True)
    calls=[]
    def judge(*args):
        calls.append(args[-1]);return (True,None,'') if len(calls)>1 else (False,SimpleNamespace(score=10,rows=ROWS),'')
    net._judge_with=judge
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{'skipped':'test capture'})
    monkeypatch.setattr(allocation_targeting,'proposals',lambda *a:[('loop_weight_scope',trial['source']+'\n')])
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**kw:pytest.fail('mapped shortlist must precede broad generation'))
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    result=reviewed_seed_search.run(search,AGENT,net.project_row('game'),net.function())
    assert len(calls)==2 and result['diagnostic_exact']


def test_cancellation_does_not_compile_or_adopt(monkeypatch):
    net,trial=prepare(monkeypatch,True);log=Log();log.cancelled=True
    net._judge_with=lambda *a:pytest.fail('cancelled search compiled')
    with pytest.raises(RuntimeError,match='cancelled'):
        reviewed_seed_search.run(deep_permuter.Search(net,1,20,log,net.function()),AGENT,net.project_row('game'),net.function())
    assert net.function()['status']=='open'
