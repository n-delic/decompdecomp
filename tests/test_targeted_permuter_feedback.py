import hashlib
import json

from decompnet.app import debug_boundary_repairs as boundaries
from decompnet.app import proposal_progress, region_repairs, repair_effects, repair_localisation, repair_selection
from decompnet.app.deep_repair_feedback import Feedback
from tests.test_permute import FakeNet


def tagged(count=30):
    return [(boundaries.FAMILY,f'int f(){{return {i};}}') for i in range(count)]


def test_boundary_pool_has_later_pages_and_shared_generator_exposes_them():
    from decompnet.app import draft_repairs
    source='void f(){'+''.join(f'x={i};' for i in range(36))+'}'
    first=boundaries.variants(source,'f',['-g3'])
    later=boundaries.variants(source,'f',['-g3'],offset=12)
    assert len(first)==len(later)==12 and not set(first)&set(later)
    labels={}
    choices=draft_repairs.variants(source,'f','mips',flags=['-g3'],labels=labels,limit=None)
    assert set(first+later)<={(labels[c],c) for c in choices}


def test_boundary_cursor_advances_only_after_scored_trial_and_survives_new_scheduler():
    net=FakeNet();proj=net.project_row('game');fn=net.function();pool=tagged()
    ordered,cursor=proposal_progress.order(net,proj,fn,'source',pool)
    assert ordered==pool
    assert proposal_progress.order(net,proj,fn,'source',pool)[0]==pool
    cursor.measured(boundaries.FAMILY,pool[0][1],None)
    assert proposal_progress.order(net,proj,fn,'source',pool)[0]==pool
    cursor.measured(boundaries.FAMILY,pool[11][1],10)
    net._proposal_progress_ready=False  # A fresh scheduling instance reads the persisted row.
    again,_=proposal_progress.order(net,proj,fn,'source',pool)
    assert again[0]==pool[12] and set(again)==set(pool)
    assert proposal_progress.order(net,proj,fn,'changed source',pool)[0]==pool
    net._context=lambda *_:{'header':'changed header','version':2}
    assert proposal_progress.order(net,proj,fn,'source',pool)[0]==pool


def test_cursor_scope_includes_ordered_pool_and_preserves_other_family_slots():
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    pool=tagged(3);mixed=[pool[0],('other','unchanged slot'),*pool[1:]]
    _,cursor=proposal_progress.order(net,proj,fn,'source',mixed)
    cursor.measured(boundaries.FAMILY,pool[0][1],10)
    rotated,_=proposal_progress.order(net,proj,fn,'source',mixed)
    assert rotated[1]==mixed[1] and rotated[0]==pool[1]
    assert proposal_progress.order(net,proj,fn,'source',tagged(4))[0]==tagged(4)
    assert proposal_progress.order(net,proj,fn,'source',[('other','only')])==([('other','only')],None)


def test_localisation_ranks_whole_nearby_copy_and_line_edits_without_discarding_broad_candidates():
    source='void f(){\n a=1;\n b=a;\n use(b);\n x=2;\n y=x;\n use(y);\n}'
    near=('dead_copy_source',source.replace('b=a;','b=a;a=0;'))
    far=('dead_copy_source',source.replace('y=x;','y=x;x=0;'))
    mapping=repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})
    pool=[far,('unrelated',source+' '),near]
    ordered=repair_localisation.order(source,pool,mapping)
    assert ordered[0]==near and set(ordered)==set(pool)
    assert repair_localisation.order(source,pool,None)==pool
    assert repair_localisation.order(source+'\n',pool,mapping)==pool


def test_localisation_reserves_broad_probes_and_does_not_split_scopes():
    source='int f(){\n int a;\n a=read();\n return a;\n}'
    mapping=repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})
    near=[('dead_copy_source',source.replace('a=read();',f'a=read();a={i};')) for i in range(8)]
    broad=('other','opaque whole-function hypothesis')
    result=repair_localisation.order(source,[broad]+near,mapping)
    assert result[3]==broad and len(result)==9


def test_successful_divisor_copy_pair_is_in_combination_planner():
    assert repair_selection.order(['shuffle','dead_copy_source'],['divisor_normalization_copy'])[0]=='dead_copy_source'
    state={'pending':[dict(source='shuffle',parent_source='root',parent_output='root',path=['shuffle']),
                      dict(source='pair',parent_source='root',parent_output='root',path=['divisor_normalization_copy','dead_copy_source'])],
           'counts':{'scored':2}}
    repair_effects.rotate(state)
    assert state['pending'][0]['source']=='pair'


def test_equal_scores_do_not_mean_identical_outputs_and_cached_results_do_not_teach():
    feedback=Feedback('root',30,[],output='root-bytes')
    feedback.record('root','same','family',[],30,[],'root-bytes')
    feedback.record('root','changed','family',[],30,[],'changed-bytes')
    feedback.record('root','improved','family',[],20,[],'improved-bytes')
    feedback.record('root','cached','family',[],30,[],'root-bytes',reused=True)
    counts=feedback.summary()
    assert counts['identical']==counts['changed_unimproved']==counts['changed_improved']==1
    assert repair_effects.key({'parent_source':'root','path':['family']}) in feedback.state['family_no_effect']
    assert json.loads(json.dumps(feedback.state))['output_feedback']==feedback.state['output_feedback']


def test_region_progress_reports_both_recovery_and_regression_for_same_target_only():
    before=[('r' if i in {1,2} else ' ',f'op {i}',f'other {i}') for i in range(48)]
    after=[('r' if i==20 else ' ',f'op {i}',f'other {i}') for i in range(48)]
    result=region_repairs.progress(before,after)
    assert 'improved 0 (2 differences)' in result and 'regressed 1 (1 differences)' in result
    assert 'not C source regions' in result
    different=after.copy();different[0]=('r','different target','other')
    assert not region_repairs.progress(before,different)
    assert not region_repairs.progress(None,after)


def test_fingerprinted_inert_family_demotes_sooner_than_changed_regressions():
    from decompnet.app import experiment_evidence as evidence
    net=FakeNet();fn=net.function();proj=net.project_row('game');scope=evidence.scope(net,proj,fn)
    for _ in range(4):evidence.record(net,fn,scope,'inert','a','b',30,30,'bytes','bytes',1)
    for _ in range(8):evidence.record(net,fn,scope,'regressed','a','b',30,40,'old','different',1)
    assert evidence.factors(net,proj,fn)=={'inert':.25,'regressed':.65}


def test_regional_advice_is_bound_to_explicit_parent_and_build():
    from decompnet.app import search_evidence
    from decompnet.repair_feedback import trial
    from tests.test_permute import AGENT
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    before=[('r' if i==1 else ' ',f'op {i}',f'other {i}') for i in range(32)]
    after=[(' ',f'op {i}',f'other {i}') for i in range(32)]
    net._last_diffs={(AGENT.id,fn['id']):(before,'legend')}
    search_evidence.remember(net,AGENT,proj,fn,'parent',30)
    net._last_diffs={(AGENT.id,fn['id']):(after,'legend')}
    with trial({'base_source_sha256':hashlib.sha256(b'parent').hexdigest()}):
        search_evidence.remember(net,AGENT,proj,fn,'child',10)
    key=(fn['id'],search_evidence.experiment_evidence.build_scope(net,proj,fn))
    child=net._search_evidence[key][hashlib.sha256(b'child').hexdigest()]
    assert 'improved' in child['regional_progress']


def test_agent_feedback_includes_region_change_only_under_unchanged_build():
    from decompnet.app import agent_tokens
    from tests.test_permute import AGENT
    net=FakeNet();fn=net.function()
    before=[('r' if i==1 else ' ',f'op {i}',f'other {i}') for i in range(32)]
    after=[(' ',f'op {i}',f'other {i}') for i in range(32)]
    net._last_diffs={(AGENT.id,fn['id']):(before,'legend')}
    agent_tokens.outcome(net,AGENT,fn,'first','score 30')
    net._last_diffs={(AGENT.id,fn['id']):(after,'legend')}
    result=agent_tokens.outcome(net,AGENT,fn,'second','score 10')
    assert 'preceding scored trial' in result['regional_progress'] and 'improved' in result['regional_progress']
    net._context=lambda *_:{'header':'new context','version':2}
    assert 'regional_progress' not in agent_tokens.outcome(net,AGENT,fn,'third','score 10')


def test_deep_search_resumes_boundary_trials_across_new_jobs(monkeypatch):
    from decompnet.app import deep_permuter, draft_repairs
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT
    net=setup(monkeypatch)
    pool=tagged(14)
    def variants(source,*args,**kwargs):
        kwargs['labels'].update({s:f for f,s in pool})
        return [s for _,s in pool]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda *a:40)
    tried=[]
    monkeypatch.setattr(Permuter,'_score',lambda self,a,p,f,s,*rest:tried.append(s) or 40)
    for _ in range(14):
        search=deep_permuter.Search(net,30,100,Log())
        search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None,max_trials=1)
    assert tried==[s for _,s in pool]


def test_deep_search_targets_mapped_copy_without_splitting_candidate(monkeypatch):
    from decompnet.app import deep_permuter, draft_repairs
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT
    net=setup(monkeypatch)
    source='void f(){\n a=1;\n b=a;\n use(b);\n x=2;\n y=x;\n use(y);\n}'
    near=source.replace('b=a;','b=a;a=0;');far=source.replace('y=x;','y=x;x=0;')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    def variants(source,*args,**kwargs):
        kwargs['labels'].update({near:'dead_copy_source',far:'dead_copy_source'})
        return [far,near]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda *a:40)
    tried=[]
    monkeypatch.setattr(Permuter,'_score',lambda self,a,p,f,s,*rest:tried.append(s) or 40)
    search=deep_permuter.Search(net,30,100,Log())
    search.winner_locations=repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})
    search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None,max_trials=1)
    assert tried==[near]


def test_boundary_progress_deduplicates_sources_before_rotating():
    net=FakeNet();proj=net.project_row('game');fn=net.function();pool=tagged(3)
    ordered,cursor=proposal_progress.order(net,proj,fn,'source',pool+pool)
    assert ordered==pool
    cursor.measured(boundaries.FAMILY,pool[0][1],10)
    assert proposal_progress.order(net,proj,fn,'source',pool+pool)[0]==pool[1:]+pool[:1]
