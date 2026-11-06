import json

import pytest

from decompnet.app import iterative_repairs as search
from decompnet.app import operand_focus, repair_benchmark, repair_selection


def test_pages_resume_after_json_roundtrip_and_reset_for_a_changed_pool():
    pool = [('family', str(i)) for i in range(27)]
    cursors = {}
    first = repair_selection.page(pool, 'source', cursors, 8)
    cursors = json.loads(json.dumps(cursors))
    pages = [first]
    while pages[-1].more:
        pages.append(repair_selection.page(pool, 'source', cursors, 8))
    assert [p for page in pages for p in page] == pool
    assert not repair_selection.page(pool, 'source', cursors, 8)
    assert repair_selection.page(pool, 'new source', cursors, 8) == first
    assert repair_selection.page(pool[::-1], 'source', cursors, 8) == pool[::-1][:8]


def test_frontier_visits_candidates_beyond_cutoff_and_survives_restart():
    cursors = {}
    def variants(source, report):
        return repair_selection.page([('family', str(i)) for i in range(25)] if source == 'seed' else [], source, cursors, 8)
    calls = []
    def judge(source):
        calls.append(source)
        return dict(score=0 if source=='24' else 20, matched=source=='24')
    state = dict(pending=[dict(source='seed', depth=0, ceiling=20)], next=[], seen=['seed'])
    assert not search.advance(state, judge, variants, lambda:len(calls)>=9)
    state = json.loads(json.dumps(state)); cursors = json.loads(json.dumps(cursors))
    assert search.advance(state, judge, variants, lambda:False)
    assert calls.count('seed') == 1 and len(calls) == len(set(calls))
    assert calls[-1] == '24'


def test_numeric_improvement_earns_bounded_immediate_followups():
    rows = [('r', 'move $v0, $a0', 'move $v1, $a0')]
    state = dict(pending=[dict(source='seed', depth=0, ceiling=100)], next=[], seen=['seed'])
    calls=[]
    def judge(source):
        calls.append(source)
        return dict(score={'seed':100,'better':60,'ordinary':90,'done':0}[source], matched=source=='done',rows=rows)
    def variants(source, report):
        return {'seed':[('paired_operand_reuse','better'),('other','ordinary')],
                'better':[('affine_counter_guard','done')], 'ordinary':[]}.get(source,[])
    assert search.advance(state, judge, variants, lambda:False)
    assert calls == ['seed','better','done']
    assert state['productive_bursts'] == 1


def test_offset_evidence_is_advisory_and_malformed_feedback_is_ignored():
    evidence=operand_focus.offsets([('r','lh $v0, 0x18($s1)','lh $v1, 0x18($s1)'),(' ','lh $v0, 20($s1)','')])
    assert evidence == {24}
    assert operand_focus.weight('*((short *)(p + 24))', evidence) == 1
    assert operand_focus.weight('a[0]', evidence) == 0
    assert not operand_focus.offsets([['bad']])


def test_comparison_requires_identical_frozen_inputs_and_budgets():
    row=dict(case='scope',source_sha256='source',baseline_score=20,best_score=20,first_exact_trial=None,
             candidate_compiles=8,elapsed_seconds=1)
    before=dict(cases=[row],trial_budget=8,seconds_budget=10)
    after=dict(before,cases=[dict(row,best_score=0,first_exact_trial=2)])
    assert repair_benchmark.compare(before,after)['newly_recovered'] == 1
    for bad in (dict(after,trial_budget=9),dict(after,cases=[dict(row,source_sha256='changed')]),
                dict(after,cases=[row,row])):
        with pytest.raises(ValueError):repair_benchmark.compare(before,bad)


def test_complementary_families_precede_unrelated_trials():
    families=['other','affine_counter','paired_operand_reuse','affine_counter_guard']
    assert repair_selection.order(families,['paired_operand_reuse'])[:2] == ['affine_counter_guard','affine_counter']
    assert repair_selection.order(families,[]) == families
    with pytest.raises(ValueError):repair_selection.page([], 'source', {}, 0)


def test_focused_operand_clusters_keep_whole_function_alternatives():
    from decompnet.app import affine_operand_repairs
    source='''void f(char *p, char *a, char *b) {
 int scratch; int old;
 *((short *)(p+24)) = *((short *)(a+24)) - *((short *)(b+24));
 *((short *)(p+26)) = *((short *)(a+24)) - *((short *)(b+24));
 *((short *)(p+100)) = *((short *)(a+100)) - *((short *)(b+100));
 }'''
    rows=[('r','lh $v0, 24($a0)','lh $v1, 24($a0)')]
    variants=affine_operand_repairs.operand_variants(source,'f',rows)
    assert variants
    assert '*((short *)(p+100)) = *((short *)(a+100)) - *((short *)(b+100));' in variants[0][1]
    assert any('*((short *)(p+100)) = scratch - old;' in text for _,text in variants)


def test_deferred_empty_seen_pages_do_not_report_false_exhaustion():
    cursors={}
    def variants(source,report):
        return repair_selection.page([('f','seen'),('f','last')] if source=='seed' else [], source,cursors,1)
    state=dict(pending=[],next=[],seen=['seed','seen'],deferred=[dict(source='seed',score=20,ceiling=20,depth=0,report={'score':20})])
    judge=lambda source:dict(score=0,matched=True)
    assert not search.advance(state,judge,variants,lambda:False)
    assert state['reason']=='pending_proposal_pages' and state['deferred']
    assert search.advance(state,judge,variants,lambda:False)


def test_successful_family_window_keeps_other_families_and_all_later_pages(monkeypatch):
    from decompnet.app import draft_repairs
    def generate(source,name,arch,asm,flags,labels,**kwargs):
        result=[f'pair{i}' for i in range(30)]+['ordinary','other']
        labels.update({s:'paired_operand_reuse' for s in result[:-2]})
        labels.update(ordinary='ordinary',other='other')
        return result
    monkeypatch.setattr(draft_repairs,'variants',generate)
    report={'rows':[],'repair_path':['affine_counter_guard']}; state={}
    def page():
        return search.targeted_variants('void f(void) {}',report,{'name':'f','asm':''},'mips',[],
                                        tagged=True,selection=state)
    first=page()
    assert [s for _,s in first[:16]] == [f'pair{i}' for i in range(16)]
    assert ('ordinary','ordinary') in first and ('other','other') in first
    result=list(first)
    while first.more:
        first=page();result.extend(first)
    assert len(result)==32 and len(result)==len(set(result))


def test_unscored_comparisons_are_reported_without_numeric_claims():
    row=dict(case='scope',source_sha256='source',baseline_score=None,best_score=None,first_exact_trial=None,
             candidate_compiles=1,elapsed_seconds=1)
    report=dict(cases=[row],trial_budget=8,seconds_budget=10)
    compared=repair_benchmark.compare(report,report)
    assert compared['unscored_cases']==1 and compared['score_improvements']==0


def test_proposal_cache_reuses_generation_but_not_changed_inputs(monkeypatch):
    calls=[]
    def pool(source,report,fn,arch,flags,header,tables,callees,*,focused=True):
        calls.append((source,header,flags))
        return [('f',str(i)) for i in range(25)]
    monkeypatch.setattr(search,'targeted_pool',pool)
    cache={};selection={}
    def choose(header='',flags=()):
        return search.targeted_variants('source',{},dict(name='f',asm=''),'mips',flags,header,
                                        selection=selection,pool_cache=cache,tagged=True)
    assert choose().more
    assert not choose().more
    assert len(calls)==1
    choose('changed');choose('changed',('-O2',))
    assert len(calls)==3 and len(cache)==2


def test_benchmark_preserves_live_page_order_instead_of_interleaving_again():
    import time
    def generate(source):
        return repair_selection.Page([('followup',str(i)) for i in range(13)]+[('other','unrelated')]) if source=='seed' else []
    def evaluate(source):
        return dict(score=0 if source=='12' else 20,matched=source=='12',outcome='matched' if source=='12' else 'miss')
    result=repair_benchmark.run('seed',20,evaluate,generate,trials=13,deadline=time.monotonic()+5)
    assert result['first_exact_trial']==13


def test_live_eight_item_pages_reserve_other_available_families(monkeypatch):
    monkeypatch.setattr(search,'targeted_pool',lambda *args,**kwargs:[('paired_operand_reuse',str(i)) for i in range(30)]+[('ordinary','a'),('other','b')])
    result=search.targeted_variants('source',{'repair_path':['affine_counter_guard']},dict(name='f',asm=''),
                                    'mips',[],selection={},limit=8,tagged=True)
    assert ('ordinary','a') in result and ('other','b') in result


def test_evidence_order_is_retained_when_candidates_already_exist(monkeypatch):
    from decompnet.app import draft_repairs
    def generic(source,name,arch,asm,flags,labels,**kwargs):
        labels.update(a='paired_operand_reuse',b='paired_operand_reuse')
        return ['a','b']
    monkeypatch.setattr(draft_repairs,'variants',generic)
    monkeypatch.setattr(draft_repairs.affine_operand_repairs,'operand_variants',lambda *args,**kwargs:[('paired_operand_reuse','b'),('paired_operand_reuse','a')])
    assert search.targeted_variants('void f(void) {}',{'repair_path':['affine_counter_guard']},dict(name='f',asm=''),'mips',[]) == ['b','a']
