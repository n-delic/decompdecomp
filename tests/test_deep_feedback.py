"""The random/local feedback loop and recovery of drafts invalidated by their current header."""
from types import SimpleNamespace

import pytest

from decompnet import scoring, source_lines
from decompnet.app import deep_permuter, draft_repairs, prototype_alignment
from decompnet.app.permute import Permuter, State
from decomprepair import register_reuse_repairs
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT, batches


@pytest.mark.parametrize('imported', [False, True])
def test_a_random_win_runs_local_repairs_with_its_new_source_and_diff(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    original = net.function()['best_source']
    generated, tested, evidence = [], [], []
    def variants(source, *args, **kwargs):
        generated.append(source)
        return ['exact'] if source == 'random win' else []
    monkeypatch.setattr(draft_repairs, 'variants', variants)
    monkeypatch.setattr(deep_permuter.stack_padding_repairs, 'grow', lambda s,n,a,rows: evidence.append(rows) or [])
    net._last_diffs = {}
    def judge(self, agent, proj, fn, source, best, fork):
        tested.append((source, fork))
        score = {'random win':20, 'exact':0}.get(source, 50)
        if score < best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                           (score,source,'matched' if score==0 else 'open',fn['id']))
        net._last_diffs[agent.id,fn['id']] = ([source], '')
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    batches(monkeypatch, ['random win','obsolete batch remainder'])
    log = Log()
    result = deep_permuter.run(net,'game','f',1,10,log)
    assert result['matched'] and result['tested']==2
    assert generated == [original,'random win']
    assert evidence[-1] == ['random win']
    assert [s for s,_ in tested] == ['random win','exact']
    assert all(fork == ('fork' if imported else None) for _,fork in tested)
    assert any(p['phase']=='local repairs after random improvement' for p in log.updates)
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==0


def test_feedback_keeps_one_compile_allowance_and_releases_the_claim(monkeypatch):
    net = setup(monkeypatch)
    original=net.function()['best_source']
    monkeypatch.setattr(draft_repairs,'variants',lambda source,*a,**k: [] if source==original else ['local win','exact'])
    def judge(self,agent,proj,fn,source,best,fork):
        score={'random win':30,'local win':20,'exact':0}[source]
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=?,best_score=?',(source,score))
        return score
    monkeypatch.setattr(Permuter,'_score',judge)
    batches(monkeypatch,['random win'])
    result=deep_permuter.run(net,'game','f',1,2,Log())
    assert result['reason']=='Variant limit reached' and result['tested']==2
    assert net.function()['best_source']=='local win'
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==0


@pytest.mark.parametrize('verdict', [True,False])
def test_a_zero_saved_score_is_marked_matched_only_after_the_real_exact_verdict(monkeypatch,verdict):
    net=setup(monkeypatch)
    with net.store.tx() as db: db.execute('UPDATE functions SET best_score=0')
    net._compile=lambda *a: SimpleNamespace(ok=True,obj=object())
    net._evaluate=lambda *a: (object(),verdict,[])
    net._target_obj=lambda *a: SimpleNamespace(cpu='mips')
    net.keep_diff=lambda *a: None
    monkeypatch.setattr(scoring,'diff',lambda *a: scoring.DiffResult(0,1000,[],True))
    # This verdict unit test supplies sentinel objects, not actual ELF compiler output.
    monkeypatch.setattr(source_lines,'mismatches',lambda *a,**k: [])
    monkeypatch.setattr(scoring,'disassemble',lambda *a: [])
    recorded=[]
    def record(fn,agent,score,source,version,**kwargs):
        recorded.append(kwargs['matched'])
        with net.store.tx() as db: db.execute("UPDATE functions SET status='matched'")
    net._record=record
    fn=net.function()
    score=Permuter(net)._score(AGENT,net.project_row('game'),fn,fn['best_source'],0,None)
    assert score==(0 if verdict else 1)
    assert recorded==([True] if verdict else [])


def test_return_alignment_keeps_body_and_parameters_and_does_not_change_the_header():
    source='int f(int x){return x*3;}'
    header='#define NULL ((void*)0)\nshort f(int);'
    assert prototype_alignment.variants(source,'f',header)==['short f(int x){return x*3;}']
    for bad in ['short f(short);','short f();','short f(int,...);','short f(int);int f(int);',
                '#if X\nshort f(int);\n#endif','#define short int\nshort f(int);','short f(int (*cb)(int));']:
        assert not prototype_alignment.variants(source,'f',bad)


def test_a_broken_prototype_can_recover_with_a_higher_honest_baseline_and_old_source_history(monkeypatch):
    baseline=deep_permuter.Search._baseline
    net=setup(monkeypatch)
    monkeypatch.setattr(deep_permuter.Search,'_baseline',baseline)
    net._project=net.project_row
    original='int f(int x){return x*3;}'
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',(original,))
    net._context=lambda *a: {'header':'short f(int);','version':1}
    monkeypatch.setattr(Permuter,'_baseline',lambda *a: (None,1))
    history=[]
    net._record=lambda fn,agent,score,source,*a,**k: history.append((score,source))
    def judge(self,agent,proj,fn,source,best,fork):
        net._record(fn,agent,215,source,1)
        return 215
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,10,Log(),net.function())
    measured=search._baseline(AGENT,net.project_row('game'),net.function(),State(),None)
    assert measured[0]==215 and net.function()['best_score']==215
    assert net.function()['best_source']=='short f(int x){return x*3;}'
    assert history==[(215,'short f(int x){return x*3;}'),(None,original)]
    assert search.starting_score==215


def test_register_candidates_target_constant_copy_and_last_scaled_use_without_touching_other_functions():
    constant='int f(int x){int missing;int k=13;if(x!=missing)x++;missing=k;return x;}'
    choices=register_reuse_repairs.variants(constant,'f')
    assert any(family=='constant_register_reuse' and 'if(x!=k)' in source for family,source in choices)
    copy='int f(int x){int a;int b;a=x+3;b=a;return b+7;}'
    assert any(family=='copy_register_reuse' and 'return a+7' in source for family,source in register_reuse_repairs.variants(copy,'f'))
    shift='int f(int x){int index;index=x;return index*4;}'
    assert any(family=='last_shift_reuse' and '(index *= 4)' in source for family,source in register_reuse_repairs.variants(shift,'f'))
    for source in [shift.replace('int index;','volatile int index;'),shift.replace('return index*4;','escape(&index);return index*4;')]:
        assert not register_reuse_repairs.variants(source,'f')
    assert not any(family=='last_shift_reuse' for family,_ in register_reuse_repairs.variants(shift.replace('int index;','short index;'),'f'))
    prefix='void untouched(void){int y;y=4;use(y);}\n'
    assert all(s.startswith(prefix) for _,s in register_reuse_repairs.variants(prefix+copy,'f'))


def test_a_refinement_burst_cannot_consume_the_whole_random_lane(monkeypatch):
    net=setup(monkeypatch)
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**k: [f'candidate {i}' for i in range(100)])
    monkeypatch.setattr(Permuter,'_score',lambda *a: 99)
    search=deep_permuter.Search(net,1,1000,Log(),net.function())
    best,source=search._after_improvement(AGENT,net.project_row('game'),net.function(),State(),None,40,net.function()['best_source'])
    assert best==40 and search.tested==deep_permuter.AFTER_RANDOM_TRIALS
    assert search.phase=='local repairs' and search.refinement_rounds==1


def test_recovery_does_not_overwrite_a_concurrent_header_change(monkeypatch):
    baseline=deep_permuter.Search._baseline
    net=setup(monkeypatch)
    monkeypatch.setattr(deep_permuter.Search,'_baseline',baseline)
    net._project=net.project_row
    original='int f(int x){return x*3;}'
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',(original,))
    header=['short f(int);']
    net._context=lambda *a: {'header':header[0],'version':1}
    net._record=lambda *a,**k: None
    monkeypatch.setattr(Permuter,'_baseline',lambda *a: (None,1))
    def judge(*args):
        header[0]='unsigned short f(int);'
        return 215
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,10,Log(),net.function())
    assert search._baseline(AGENT,net.project_row('game'),net.function(),State(),None)[0] is None
    assert net.function()['best_source']==original and net.function()['best_score']==40


def test_constant_register_reuse_can_recover_a_real_exact_object(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned gcc-2.7.2-kmc')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc'}})
    source='int f(int x){int missing;int k=13;if(x!=missing)x++;missing=k;return x;}'
    target=compile_source(cfg,'','int f(int x){if(x!=13)x++;return x;}')
    before=compile_source(cfg,'',source)
    assert target.ok and before.ok
    expected=extract_function(target.obj,'f')
    assert scoring.diff(expected,extract_function(before.obj,'f')).score>0
    candidates=[s for family,s in register_reuse_repairs.variants(source,'f') if family=='constant_register_reuse']
    assert candidates
    results=[compile_source(cfg,'',s) for s in candidates]
    assert all(result.ok for result in results)
    assert any(scoring.diff(expected,extract_function(result.obj,'f')).score==0 for result in results)


def test_losing_local_trials_do_not_replace_evidence_for_the_next_random_batch(monkeypatch):
    net=setup(monkeypatch)
    net._last_diffs={}
    net._agent_outcomes={}
    monkeypatch.setattr(draft_repairs,'variants',lambda *a,**k: ['losing local'])
    def judge(self,agent,proj,fn,source,best,fork):
        net._last_diffs[agent.id,fn['id']]=([source],'')
        return 20 if source=='winner' else 50
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,100,Log(),net.function())
    search._score(AGENT,net.project_row('game'),net.function(),'winner',40,None)
    search._after_improvement(AGENT,net.project_row('game'),net.function(),State(),None,20,'winner')
    assert net._last_diffs[AGENT.id,1][0]==['winner']
    assert net._agent_outcomes[AGENT.id,1]['source']=='winner'
