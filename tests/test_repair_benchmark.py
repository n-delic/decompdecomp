import time

from decompnet.app import iterative_repairs, repair_benchmark


def test_collect_exact_keeps_intermediates_and_explores_sibling_combinations():
    state = dict(pending=[dict(source='seed', depth=0, ceiling=20)], next=[], seen=['seed'])
    calls = []
    def evaluate(source):
        calls.append(source)
        exact = source in ('exact', 'combined')
        return dict(score=0 if exact else 10, matched=exact)
    def variants(source, report):
        return {'seed':[('first','exact'),('second','bridge')],
                'bridge':[('first','combined')]}.get(source, [])
    assert iterative_repairs.advance(state, evaluate, variants, lambda: False, stop_on_match=False)
    assert calls == ['seed','exact','bridge','combined']
    assert len(state['exact']) == 2
    assert state['winning_path'] == ['first']
    assert state['exact'][1]['path'] == ['second','first']


def test_batch_equal_budgets_and_source_free_results():
    cases = [dict(source='seed', baseline_score=10, scope='frozen-a'),
             dict(source='seed', baseline_score=10, scope='frozen-b')]
    def evaluate(case, source):
        exact = source == 'hit'
        return dict(score=0 if exact else 10, matched=exact, outcome='exact' if exact else 'miss')
    result = repair_benchmark.batch(cases, evaluate,
        lambda c,s: [('one','hit'),('two','miss')] if s == 'seed' else [], trials=2, seconds=2)
    assert result['matched_functions'] == 2 and result['candidate_compiles'] == 4
    assert all(r['first_exact_trial'] == 1 and r['exact_alternatives'] == 1 for r in result['cases'])
    assert 'seed' not in repr(result) and 'hit' not in repr(result)


def test_default_stops_at_first_exact_with_nonstandard_outcome_label():
    result = repair_benchmark.run('seed', 10,
        lambda s: dict(score=10 if s=='seed' else 0, matched=s!='seed', outcome='control_output_recovered'),
        lambda s: [('repair','done'),('other','unused')], trials=8, deadline=time.monotonic()+2)
    assert result['candidate_compiles'] == result['first_exact_trial'] == 1


def test_plateau_bridge_gets_combination_probe_before_large_first_wave():
    state = dict(pending=[dict(source='seed',depth=0,ceiling=10)], next=[], seen=['seed'], explore_plateaus=True)
    calls = []
    def evaluate(source):
        calls.append(source)
        return dict(score=0 if source=='combo' else 10,matched=source=='combo')
    def variants(source, report):
        return ([('a','flat')]+[('b',str(i))for i in range(20)] if source=='seed'
                else [('b','combo')] if source=='flat' else [])
    assert iterative_repairs.advance(state,evaluate,variants,lambda: len(calls)>=3)
    assert calls == ['seed','flat','combo']
    assert state['pending'][0]['source'] == '0'


def test_exact_at_wave_end_does_not_drop_retained_combination_parents():
    state = dict(pending=[dict(source='seed',depth=0,ceiling=10)], next=[], seen=['seed'])
    calls = []
    def judge(source):
        calls.append(source)
        return dict(score=0 if source in ('exact','combo') else 10,matched=source in ('exact','combo'))
    def variants(source, report):
        return {'seed':[('a','bridge'),('b','exact')],'bridge':[('b','combo')]}.get(source,[])
    assert iterative_repairs.advance(state,judge,variants,lambda:False,stop_on_match=False)
    assert calls == ['seed','bridge','exact','combo']
    assert len(state['exact']) == 2


def test_comparison_reports_recoveries_and_compile_cost_without_counting_timing_as_a_win():
    cases=[dict(source='seed',baseline_score=10,scope='target-a')]
    def evaluate(case,s):return dict(score=0 if s=='done' else 10,matched=s=='done',outcome='matched' if s=='done' else 'miss')
    old=repair_benchmark.batch(cases,evaluate,lambda c,s:[('one','miss'),('two','done')] if s=='seed' else [],trials=2,seconds=2)
    new=repair_benchmark.batch(cases,evaluate,lambda c,s:[('two','done'),('one','miss')] if s=='seed' else [],trials=2,seconds=2)
    result=repair_benchmark.compare(old,new)
    assert result['before_recovered_cases']==result['after_recovered_cases']==1
    assert result['before_candidate_compiles']==result['after_candidate_compiles']==2
    assert result['shared_recovery_compile_saving']==1
    assert result['newly_recovered']==result['lost_recoveries']==0
