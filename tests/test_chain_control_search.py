"""Advisory processor hints route real source repairs without altering acceptance."""
import pytest

from decompnet.app import allocation_evidence, allocation_search, deep_permuter, permuter_focus, repair_ranking
from decompnet.arch import get
from tests.test_deep_permuter import Log
from tests.test_permute import FakeNet

CHAIN=[('r','lw $v0, 4($a0)','lw $v1, 4($a1)'),('r','addu $s0, $v0, $a0','addu $s0, $v1, $a1')]
CONTROL=[('r','move $a0, $s0','move $v0, $s0'),('i','j .L10','j .L20'),(' ','nop','nop')]


def test_chain_is_local_acyclic_and_does_not_need_a_global_register_cycle():
    d=allocation_evidence.diagnose(CHAIN,'mips')
    assert not d['register_cycles'] and d['register_chains']==[['$a0','$a1'],['$v0','$v1']]
    assert allocation_search.allowance(d)==8
    cycle=[('r','move $a0, $v0','move $a1, $v1'),('r','move $a1, $v1','move $a0, $v0')]
    assert allocation_search.allowance(allocation_evidence.diagnose(cycle,'mips'))==24
    assert 'acyclic register chains' in allocation_evidence.text(CHAIN,'mips')
    assert repair_ranking.order(['constant_local','pointer_local_reuse'],CHAIN)[0]=='pointer_local_reuse'
    # The same physical register may describe unrelated values in another local run.
    other=CHAIN+[(' ','nop','nop'),('r','move $t0, $a0','move $t0, $a2')]
    assert not allocation_evidence.diagnose(other,'mips')['register_map']
    assert allocation_search.allowance(allocation_evidence.diagnose(other,'mips'))==8


@pytest.mark.parametrize('rows', [
    [('r','move $a0, $v0','move $a1, $v1')],
    [('r','move $a0, $v0','move $a1, $v1'),('r','move $a0, $v0','move $a2, $v1')],
    [('r','move $sp, $v0','move $a1, $v1')]*2,
    [('r','lw $a0, 4($v0)','lw $a1, 8($v1)')]*2,
    ['unknown'],
])
def test_uncertain_or_non_chain_evidence_does_not_open_the_lane(rows):
    assert not get('mips').repair_search_hints(rows)['register_chains']
    assert not allocation_search.allowance(allocation_evidence.diagnose(rows,'mips'))


def test_mixed_frame_or_instruction_changes_and_unsupported_processors_do_not_open_lane():
    for extra in [[('s','addiu $sp, $sp, -32','addiu $sp, $sp, -40')],[('>','','nop')]]:
        assert not allocation_search.allowance(allocation_evidence.diagnose(CHAIN+extra,'mips'))
    assert not allocation_evidence.diagnose(CHAIN,'ppc')['register_chains']


@pytest.mark.parametrize('fork',[None,'fork'])
def test_deep_chain_lane_has_no_diagnostic_capture_and_respects_existing_allowance(monkeypatch,fork):
    net=FakeNet();net.is_rom=lambda _:True
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    monkeypatch.setattr(allocation_search,'saved',lambda *a:None)
    def forbidden(*a,**k):pytest.fail('A chain must not spend compiler calls on optional cycle diagnostics.')
    monkeypatch.setattr(allocation_search,'capture',forbidden)
    monkeypatch.setattr(allocation_search,'proposals',lambda *a:[('pointer_local_reuse','focused')])
    assert search.allocation_choices(net.project_row('game'),net.function(),'source',CHAIN,['broad'],{},'',fork)==(['focused','broad'],{'focused'})
    assert search.tested==0
    search.allocation_trials=8
    assert search.allocation_choices(net.project_row('game'),net.function(),'source',CHAIN,['broad'],{},'',fork)==(['broad'],set())


def test_jump_targets_and_delay_slots_are_control_not_literal_constants():
    for rows in [CONTROL,[(' ','j .L10','j .L10'),('|','move $s0, $a0','nop')],
                 [(' ','jr $ra','jr $ra'),('|','move $v0, $a0','nop')],
                 [('i','beq $a0, $zero, .L1','beq $a0, $zero, .L2')]]:
        assert permuter_focus.classify(rows)=='control'
        assert allocation_evidence.plan(rows,'mips')['category']=='control'
        assert repair_ranking.order(['constant_local','structured_backedge'],rows)[0]=='structured_backedge'
    assert permuter_focus.classify([(' ','beq label','beq label'),('i','mov r0, #1','mov r0, #2')])=='constant'
    literal_slot=[(' ','j .L1','j .L1'),('i','addiu $v0, $v0, 1','addiu $v0, $v0, 2')]
    assert permuter_focus.classify(literal_slot)=='constant'
    scheduled=[(' ','j .L1','j .L1'),('o','move $v0, $a0','nop'),('o','nop','move $v0, $a0')]
    assert allocation_evidence.plan(scheduled,'mips')['category']=='scheduling'
    assert repair_ranking.order(['constant_local','scalar_schedule_order','structured_backedge'],scheduled)[0]=='scalar_schedule_order'
    assert permuter_focus.classify([('i','andi $a0, $a0, 255','andi $a0, $a0, 127')])=='constant'
    assert permuter_focus.classify([('i','jal one','jal two')])=='call'
    assert permuter_focus.classify([('r','beq $a0, $zero, .L1','beq $a1, $zero, .L1')])=='register'
    assert not allocation_evidence.diagnose([(' ','j .L1','j .L1'),(' ','nop','nop')],'mips')['delay_slot']


def test_chain_proposals_keep_only_existing_safe_families_and_cap_eight():
    choices=[str(i) for i in range(20)]
    labels={c:'pointer_local_reuse' for c in choices}
    lane=allocation_search.proposals('void f(void){}','f','mips',CHAIN,choices,labels)
    assert len(lane)==8 and [s for _,s in lane]==choices[:8]
    assert allocation_search.proposals('void f(void){}','f','mips',CHAIN,['unsafe'],{'unsafe':'constant_local'})==[]


def test_chain_lane_rotates_families_instead_of_filling_all_slots_with_one_family():
    choices=[f'p{i}' for i in range(12)]+['declarations']
    labels={s:'pointer_local_reuse' for s in choices};labels['declarations']='spill_declaration_order'
    lane=allocation_search.proposals('void f(void){}','f','mips',CHAIN,choices,labels)
    assert len(lane)==8 and lane[:2]==[('pointer_local_reuse','p0'),('spill_declaration_order','declarations')]
