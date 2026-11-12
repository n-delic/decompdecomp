"""Safe donor selection, shared output feedback and bounded measured sequence continuations."""
import json

import pytest

from decompnet.app import (
    draft_repairs,
    iterative_repairs,
)
from decompnet.app import (
    repair_effects as effects,
)
from decompnet.app import (
    repair_sequence_lane as lane,
)
from decomprepair import narrow_call_repairs as narrow

SOURCE='''void callee(int, short); void use(int);
void f(int mode,int x) { int base=x+6; int y=x;
 switch(mode) {
 case 1: if(x) { callee(mode,y); } else { use(base); } break;
 case 2: if(x) { y=x+3; callee(mode,y); } break;
 }
}'''


def test_stage_real_narrow_conversion_through_dead_donor_in_both_arms():
    proposals=narrow.variants(SOURCE,'f',arch='mips')
    assert len(proposals)==1
    text=proposals[0][1]
    assert text.count('base = (short)y;')==2
    assert text.count('callee(mode, base);')==2
    assert 'else { use(base); }' in text


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_narrow_repair_uses_target_abi_and_shared_pipeline(arch):
    assert narrow.variants(SOURCE,'f',arch=arch)
    assert narrow.FAMILY in draft_repairs.AGENT_FAMILIES
    labels={}
    draft_repairs.variants(SOURCE,'f',arch,labels=labels,header='',limit=None)
    assert narrow.FAMILY in labels.values()


@pytest.mark.parametrize('change',[
    lambda s:s.replace('callee(int, short)','callee()'),
    lambda s:s.replace('callee(int, short)','callee(int, int)'),
    lambda s:s.replace('int base=x+6;', 'volatile int base=x+6;'),
    lambda s:s.replace('int base=x+6;', 'unsigned int base=x+6;'),
    lambda s:s.replace('switch(mode)', 'escape(&base); switch(mode)'),
    lambda s:s.replace('callee(mode,y);','callee(mode,y); use(base);'),
    lambda s:s.replace('callee(mode,y);','if(x) callee(mode,y);'),
    lambda s:s.replace('callee(mode,y);','callee(base,y);'),
])
def test_donor_repair_rejects_missing_width_escaped_live_qualified_or_unbraced_storage(change):
    assert not narrow.variants(change(SOURCE),'f',arch='mips')


def test_signed_and_unsigned_conversions_are_not_invented_from_alias_names():
    text=SOURCE.replace('short);','small);')
    assert not narrow.variants(text,'f',arch='mips')
    assert narrow.variants(text,'f','typedef signed short small;','mips')
    assert not narrow.variants(text,'f','typedef int small;','mips')
    assert not narrow.variants(SOURCE,'f',arch='unknown')


def test_equivalent_machine_outputs_share_feedback_across_parent_spellings():
    def node(parent,output='same'):
        return dict(source=parent+' candidate',parent_source=parent,parent_output=output,path=['family'])
    first,second,later=node('first'),node('second'),node('later')
    fresh=dict(parent_source='first',parent_output='same',path=['different'],source='fresh')
    changed=node('changed','new-output')
    state=dict(pending=[later,fresh,changed])
    effects.observe(state,first,'same');effects.observe(state,second,'same')
    state=json.loads(json.dumps(state));effects.rotate(state)
    assert [n['source'] for n in state['pending']]==['fresh','changed candidate','later candidate']
    # Occasional retries remain queued even if source changes did not change machine code.
    state['counts']={'scored':effects.DEFER_INTERVAL-1};effects.rotate(state)
    assert state['pending'][0]['source']=='later candidate'
    effects.observe(state,later,'different-output')
    assert state['output_no_effect'][effects.output_scope(later)]==0


ROWS=[[' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20'],
      ['r','move $s0, $a0','move $s1, $a0'],[' ','jr $ra','jr $ra']]


def parent(**changes):
    result=dict(source='measured source',depth=4,productive_steps=3,score=5,ceiling=10,
                path=['typed_pointer_snapshot','narrow_call_destination','branch_scalar_lifetime','inline_call_count'],
                unchanged_steps=0,parent_output='before',output='after',baseline_rows=ROWS,report=dict(rows=ROWS))
    return result|changes


def test_only_productive_measured_complementary_sequences_earn_extra_depth():
    assert lane.eligible(parent(),iterative_repairs.DEPTH,'mips')
    for changes in [dict(productive_steps=1),dict(score=10),dict(unchanged_steps=1),
                    dict(output=None),dict(output='before'),dict(depth=6),dict(path=['other']*4),
                    dict(report={'rows':ROWS+[ROWS[1]]})]:
        assert not lane.eligible(parent(**changes),iterative_repairs.DEPTH,'mips')


def test_extra_sequence_proposals_keep_normal_limits_and_reject_unrelated_or_repeated_families():
    p=parent();state=dict(pending=[],seen=[p['source']],next=[])
    choices=[('other','unrelated'),('inline_call_count','repeat'),
             ('embedded_call_assignment','first'),('embedded_call_assignment','second'),('embedded_call_assignment','third')]
    assert iterative_repairs.children(state,p,lambda *a:choices,arch='mips')==2
    assert len(state['pending'])==2 and all(n['sequence_loan'] and n['depth']==5 for n in state['pending'])
    assert state['sequence_proposals']==2
    assert iterative_repairs.children(state,p,lambda *a:choices,arch='mips')==0
    restored=json.loads(json.dumps(state))
    # A second independently measured parent can spend the remaining two, never grow the cap.
    q=parent(source='second measured source')
    assert iterative_repairs.children(restored,q,lambda *a:[('embedded_call_assignment','fourth'),('embedded_call_assignment','fifth')],arch='mips')==2
    assert restored['sequence_proposals']==lane.MAX_PROPOSALS
    assert iterative_repairs.children(restored,parent(source='third'),lambda *a:choices,arch='mips')==0


def test_no_sequence_extension_bypasses_cancellation_or_supplies_an_exact_verdict():
    p=parent();state=dict(pending=[],seen=[p['source']],next=[])
    iterative_repairs.children(state,p,lambda *a:[('embedded_call_assignment','candidate')],arch='mips')
    calls=[]
    judge=lambda s:calls.append(s) or dict(score=0,matched=False,rows=ROWS)
    assert not iterative_repairs.advance(state,judge,lambda *a:[],lambda:True,arch='mips')
    assert not calls
    assert not iterative_repairs.advance(state,judge,lambda *a:[],lambda:False,arch='mips')
    assert calls==['candidate'] and not state.get('exact')
    assert state['counts']['sequence_evaluations']==1


def test_narrow_argument_is_not_moved_across_another_argument_using_that_scalar():
    source='void callee(int,short);void f(int y){int base;callee(y++,y);}'
    assert not narrow.variants(source,'f',arch='mips')


def test_new_rules_invalidate_old_frontiers_and_register_complementary_followups():
    from decompnet import claim_stalls
    from decompnet.app import repair_selection
    assert {'narrow_call_repairs.py','repair_sequence_lane.py','repair_effects.py'}<={p for p,_ in claim_stalls.repair_rules()}
    assert 'narrow_call_destination' in repair_selection.COMPLEMENTS['typed_pointer_snapshot']


def test_donor_ranking_prefers_an_established_other_phase_not_declaration_order():
    source=SOURCE.replace('int base=x+6;', 'int scratch; int base=x+6;')
    proposals=narrow.variants(source,'f',arch='mips')
    assert 'base = (short)y;' in proposals[0][1]


def test_real_measured_chain_earns_an_extra_complementary_step_without_global_depth_change():
    sources=['seed','snapshot','donor','branch','count','exact']
    families=['typed_pointer_snapshot','narrow_call_destination','branch_scalar_lifetime','inline_call_count','embedded_call_assignment']
    scores=[100,80,60,40,20,0]
    calls=[]
    def judge(source):
        i=sources.index(source);calls.append(source)
        return dict(score=scores[i],matched=i==5,rows=ROWS,candidate_target=source,candidate_relocs='[]')
    def variants(source,report):
        i=sources.index(source)
        return [(families[i],sources[i+1])] if i<5 else []
    state=dict(pending=[dict(source='seed',depth=0,ceiling=100)],next=[],seen=['seed'])
    assert iterative_repairs.advance(state,judge,variants,lambda:False,arch='mips')
    assert calls==sources and state['sequence_proposals']==state['counts']['sequence_evaluations']==1
    assert iterative_repairs.DEPTH==4


def test_more_than_two_extra_levels_cannot_be_earned_by_a_productive_chain():
    assert not lane.eligible(parent(depth=iterative_repairs.DEPTH+lane.EXTRA_DEPTH),iterative_repairs.DEPTH,'mips')
    p=parent();state=dict(pending=[],seen=[p['source']],next=[],sequence_proposals=lane.MAX_PROPOSALS)
    assert not lane.select(state,p,[('embedded_call_assignment','new')])


def test_unknown_output_does_not_teach_cross_parent_no_effects_or_earn_depth():
    state=dict(pending=[])
    node=dict(parent_source='source',path=['family'],parent_output=None)
    effects.observe(state,node,'observed')
    assert not state.get('output_no_effect')
    assert not lane.eligible(parent(output=None),iterative_repairs.DEPTH,'mips')


def test_already_narrow_value_can_still_use_a_different_destination_lifetime():
    source='void callee(short);void f(int x){int old;int y;y=(short)x;callee(y);}'
    assert narrow.variants(source,'f',arch='mips')


def test_single_call_donors_remain_available_without_displacing_grouped_branch_repairs():
    source='void callee(short);void f(int y){int base;callee(y);}'
    assert narrow.variants(source,'f',arch='mips')[0][0]==narrow.FAMILY


def test_deep_output_feedback_does_not_learn_from_cached_or_unidentified_builds():
    from decompnet.app.deep_repair_feedback import Feedback
    feedback=Feedback('seed',100,ROWS,'same')
    feedback.record('seed','first','family',[],100,ROWS,'same',reused=True)
    assert not feedback.state.get('output_no_effect')
    feedback.record('seed','second','family',[],100,ROWS,None)
    assert not feedback.state.get('output_no_effect')
    feedback.record('seed','third','family',[],100,ROWS,'same')
    feedback.record('seed','fourth','family',[],100,ROWS,'same')
    feedback.record('seed','extra','family',[],100,ROWS,'same')
    queue=['fifth','fresh'];feedback.order(queue,{'fifth':'family','fresh':'other'},'seed',{})
    assert queue==['fresh','fifth']


def test_third_direct_sibling_keeps_its_short_probe_before_deferred_rotation():
    node=dict(parent_source='parent',parent_output='same',path=['embedded_call_assignment'],source='third')
    state=dict(pending=[node,dict(parent_source='parent',parent_output='same',path=['other'],source='fresh')])
    effects.observe(state,node,'same');effects.observe(state,node,'same')
    effects.rotate(state)
    assert state['pending'][0]['source']=='third'
    effects.observe(state,node,'same');effects.rotate(state)
    assert state['pending'][0]['source']=='fresh'
