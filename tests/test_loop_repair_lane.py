import json

from decompnet.app import iterative_repairs as search
from decompnet.app import loop_repair_lane as lane


def rows(fixed=0):
    return [(' ','addiu $sp, $sp, -88','addiu $sp, $sp, -88')]+[
        (' ' if i<fixed else 'r','move $s1, $a0','move $s1, $a0' if i<fixed else 'move $s2, $a0') for i in range(4)]


def test_loop_regression_gets_bounded_counter_and_constant_probes_before_large_wave():
    state=dict(pending=[dict(source='seed',depth=0,ceiling=565)],next=[],seen=['seed'])
    calls=[]
    def judge(source):
        calls.append(source)
        return dict(score={'seed':565,'backedge':1860,'counter':2095,'exact':0}.get(source,565),
                    matched=source=='exact',rows=rows(),
                    output_fingerprint=source)
    def variants(source,report):
        return {'seed':[('explicit_loop_backedge','backedge')]+[('noise','noise'+str(i)) for i in range(20)],
                'backedge':[('row_number_staging','counter')],
                'counter':[('branch_integer_constant','exact')]}.get(source,[])
    assert not search.advance(state,judge,variants,lambda:len(calls)>=3,arch='mips')
    state=json.loads(json.dumps(state))
    assert search.advance(state,judge,variants,lambda:len(calls)>=4,arch='mips')
    assert calls==['seed','backedge','counter','exact']
    assert state['loop_proposals']==state['counts']['loop_evaluations']==2


def test_lane_rejects_unmeasured_frame_growth_and_excessive_score_regressions():
    node=dict(path=['explicit_loop_backedge'],depth=1,ceiling=565,baseline_rows=rows())
    assert lane.eligible(node,dict(score=1860,rows=rows(1)),'mips')
    assert lane.eligible(node,dict(score=1860,rows=rows()+[('>','','nop')]*2),'mips')
    assert not lane.eligible(node,dict(score=3000,rows=rows(1)),'mips')
    bigger=rows(1);bigger[0]=('s','addiu $sp, $sp, -88','addiu $sp, $sp, -96')
    assert not lane.eligible(node,dict(score=1860,rows=bigger),'mips')
    assert not lane.eligible(node,dict(score=1860,rows=[]),'mips')
    small=rows(1)+[('>','','nop')]*2
    assert lane.eligible(node,dict(score=1860,rows=small),'mips')
    assert not lane.eligible(node,dict(score=1860,rows=rows(1)+[('>','','nop')]*5),'mips')


def test_reserved_proposals_are_capped_across_parents_and_keep_broad_queue():
    state=dict(pending=[{'source':'broad'}],seen=[])
    def variants(source,report):return [('row_number_staging',source+str(i)) for i in range(10)]
    for i in range(4):
        parent=dict(source='parent'+str(i),depth=1,path=['explicit_loop_backedge'],ceiling=565,
                    score=1860,report={'rows':rows(1),'output_fingerprint':str(i)})
        assert lane.offer(state,parent,variants)==(2 if i<3 else 0)
    assert state['loop_proposals']==6 and len(state['pending'])==7
    assert any(n['source']=='broad' for n in state['pending'])
