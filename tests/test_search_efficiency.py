import time

import pytest

from decompnet.app import frozen_repair_cases, repair_benchmark, search_efficiency


def test_distinguishes_cached_work_unknown_outputs_and_scope_compatible_duplicates():
    m=search_efficiency.Measurements()
    def add(**kw):m.observe('family',score=5,best=10,output='a',scope='build',granularity='function',**kw)
    add();add();add(cached=True)
    m.observe('other',output='a',scope='new-build',granularity='function')
    m.observe('other',output='a',scope='build',granularity='whole-object')
    m.observe('other',exact=True,path=['first','second'])
    result=m.snapshot()
    assert result['candidate_checks']==5 and result['cached_skips']==1
    assert result['duplicate_outputs']==1 and result['unknown_output_checks']==1
    assert result['first_exact_check']==5 and result['winning_path']==['first','second']
    assert result['model_tokens']==0
    assert 'a' not in result and 'outputs' not in result


def test_memory_and_families_are_bounded(monkeypatch):
    monkeypatch.setattr(search_efficiency,'LIMIT',3)
    m=search_efficiency.Measurements()
    for i in range(200):m.observe(str(i),output=str(i),scope='s',granularity='g')
    assert len(m.outputs)==len(m.order)==3 and len(m.families)<=129
    m.observe('0',output='0',scope='s',granularity='g')
    assert m.duplicates==0  # Evicted outputs are unknown, not claimed duplicates.
    m.observe('unknown',output={'unrecognized':'format'},scope='s',granularity='g')
    assert m.unknown==1


def test_parallel_summary_does_not_invent_a_global_first_exact_order():
    a=search_efficiency.Measurements();a.observe('a',exact=True,path=['a'])
    b=search_efficiency.Measurements();b.observe('b')
    result=search_efficiency.combine([a.snapshot(),None,b.snapshot()])
    assert result['candidate_checks']==2 and result['first_exact_check'] is None
    assert result['winning_lane_checks']==1 and result['winning_path']==['a']


def test_compiler_counts_separate_requests_and_first_exact_including_speculation():
    from decompnet.app.deep_throughput import Metrics
    m=Metrics();m.add('requests',8);m.add('invocations',5);m.exact()
    m.add('invocations',2);m.exact()
    result=m.snapshot()
    assert result['pool_compile_requests']==8 and result['pool_compiler_invocations']==7
    assert result['pool_compiler_invocations_at_first_exact']==5


def test_replay_reports_duplicate_outputs_and_verified_path():
    def evaluate(source):return dict(score=0 if source=='exact' else 10,matched=source=='exact',
                                    outcome='exact' if source=='exact' else 'scored',output_fingerprint='same')
    result=repair_benchmark.run('start',10,evaluate,lambda s:[('first','miss'),('second','exact')],
                                trials=2,deadline=time.monotonic()+2)
    assert result['efficiency']['duplicate_outputs']==1
    assert result['efficiency']['first_exact_check']==2
    assert result['efficiency']['winning_path']==['second']


def test_holdouts_exclude_development_examples_and_do_not_rank_by_results():
    items=[dict(name=str(i),baseline_score=100-i) for i in range(9)]
    chosen=frozen_repair_cases.select_holdouts(items,{'0','1'},count=6)
    assert len(chosen)==6 and not {'0','1'} & {i['name'] for i in chosen}
    assert chosen==frozen_repair_cases.select_holdouts(list(reversed(items)),{'0','1'},count=6)
    with pytest.raises(ValueError,match='distinct'):
        frozen_repair_cases.select_holdouts(items+[items[0]],set())
    with pytest.raises(ValueError,match='Not enough'):
        frozen_repair_cases.select_holdouts(items,set(map(str,range(9))))


def test_ablation_filters_before_paging_without_mutating_cached_pool(monkeypatch):
    from decompnet.app import iterative_repairs
    monkeypatch.setattr(iterative_repairs,'targeted_pool',lambda *a,**k:[('omit','a'),('keep','b'),('keep','c')])
    pool={}
    args=('seed',{},dict(name='f',asm=''),'mips',[])
    first=iterative_repairs.targeted_variants(*args,tagged=True,limit=1,selection={},pool_cache=pool,omit_families=('omit',))
    assert first==[('keep','b')] and first.more
    control=iterative_repairs.targeted_variants(*args,tagged=True,limit=1,selection={},pool_cache=pool)
    assert control==[('omit','a')]


def test_safety_and_new_repair_changes_invalidate_saved_search_inputs():
    from decompnet.claim_stalls import repair_rules
    rules=dict(repair_rules())
    for name in ('parameter_carriers.py','narrow_load_subtraction.py','permuter_dependencies.py','permuter_gen.py','permuter_capture.py'):
        assert len(rules[name])==64


def test_proposals_invocations_parent_equality_and_cached_work_are_distinct(monkeypatch):
    monkeypatch.setattr(search_efficiency,'LIMIT',2)
    m=search_efficiency.Measurements()
    m.generated('f','private source');m.generated('f','private source')
    m.generated('f','other');m.generated('f','third')
    assert len(m.proposals)==len(m.proposal_order)==2
    m.observe('f',score=9,best=10,output='a',scope='s',granularity='function',
              parent_output='a',parent_granularity='function',compiler_invocations=2)
    m.observe('f',output='a',scope='s',granularity='object',parent_output='a',parent_granularity='function')
    m.observe('f',cached=True,compiler_invocations=100)
    c=m.snapshot()['families']['f']
    assert c['generated_candidates']==3 and c['candidate_checks']==2
    assert c['compiler_invocations']==2 and c['invocation_unknown_checks']==1
    assert c['unchanged_parent_outputs']==c['parent_compared_checks']==1
    assert c['cached_skips']==c['improvements']==1
    assert 'private source' not in repr(m.snapshot()) and 'private source' not in repr(m.proposals)


def test_nested_timing_collectors_keep_outer_counts_after_exception():
    from decompnet import compile_timing
    outer=compile_timing.Counts();inner=compile_timing.Counts()
    with compile_timing.collect(outer):
        with pytest.raises(ValueError),compile_timing.collect(inner,append=True):
            compile_timing.add('invocations',2)
            raise ValueError('stop')
        compile_timing.add('invocations',1)
    assert outer.values['invocations']==3 and inner.values['invocations']==2


def test_replay_compares_child_to_its_real_parent_and_counts_offers():
    result=repair_benchmark.run('seed',10,
        lambda s:dict(score={'seed':10,'first':5,'exact':0}[s],matched=s=='exact',outcome='scored',
                      output_fingerprint='a' if s=='seed' else 'b',output_granularity='function',compiler_invocations=2),
        lambda s:{'seed':[('one','first')],'first':[('two','exact')]}.get(s,[]),
        trials=2,deadline=time.monotonic()+2)
    c=result['efficiency']['families']
    assert c['one']['generated_candidates']==c['two']['generated_candidates']==1
    assert c['two']['unchanged_parent_outputs']==1 and c['two']['compiler_invocations']==2
    assert c['one']['unchanged_parent_outputs']==0
