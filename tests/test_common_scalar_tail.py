"""Common successor placement uses real computations, never duplicate dead stores."""
import pytest

from decompnet.app import branch_layout_repairs, draft_repairs


def proposals(body, header=''):
    source = 'int demo(int flag){int a;int b;int x;int y;b=3;'+body+'return a+x+y;}'
    return branch_layout_repairs.scalar_tails(source, 'demo', header)


def test_product_sinks_once_with_independent_literal_placement():
    result = proposals('if(flag){x=1;a=b*8;}else{x=2;a=b*8;}y=255;')
    assert len(result)==2
    assert all(text.count('a=b*8;')==1 for _,text in result)
    assert '}\na=b*8;\ny=255;' in result[0][1]
    assert '}y=255;\na=b*8;' in result[1][1]
    assert {label for label,_ in result}=={'common_scalar_tail'}


@pytest.mark.parametrize('body', [
    'if(flag){x=1;a=b*8;}else{x=2;a=b*9;}',
    'if(flag){return 1;a=b*8;}else{x=2;a=b*8;}',
    'if(flag){int b;b=3;a=b*8;}else{x=2;a=b*8;}',
    'if(flag){x=get();a=b*8;}else{x=2;a=b*8;}',
    'if(flag){label:x=1;a=b*8;}else{x=2;a=b*8;}',
    'if(flag){x=1;p->a=b*8;}else{x=2;p->a=b*8;}',
    'if(flag){x=1;p[a=b*8;}else{x=2;p[a=b*8;}',
    'while(flag) if(flag){x=1;a=b*8;}else{x=2;a=b*8;}',
    'if(flag) if(flag){x=1;a=b*8;}else{x=2;a=b*8;}',
    'if(flag){escape(&a);a=b*8;}else{x=2;a=b*8;}',
])
def test_unknown_flow_lookup_or_storage_is_left_alone(body):
    assert not proposals(body)


@pytest.mark.parametrize('assignment', ['b=255;', 'a=255;', 'y=b;', 'y=get();'])
def test_does_not_delay_past_dependent_or_evaluating_statement(assignment):
    result = proposals('if(flag){x=1;a=b*8;}else{x=2;a=b*8;}'+assignment)
    assert len(result)==1


def test_macro_cannot_replace_a_product_operand():
    assert not proposals('if(flag){x=1;a=b*8;}else{x=2;a=b*8;}', '#define b global_value\n')


def test_shared_draft_family_is_reachable():
    source='int demo(int flag){int a;int b;b=3;if(flag){a=b*8;}else{a=b*8;}return a;}'
    labels={}
    draft_repairs.variants(source,'demo','mips',labels=labels,limit=None)
    assert 'common_scalar_tail' in draft_repairs.AGENT_FAMILIES
    assert 'common_scalar_tail' in labels.values()


def test_qualified_locals_are_not_sunk():
    source='int demo(int flag){s32 a;s32 b;b=3;if(flag){a=b*8;}else{a=b*8;}return a;}'
    assert not branch_layout_repairs.scalar_tails(source,'demo','typedef volatile int s32;')


def test_object_macro_cannot_hide_an_arm_transfer():
    assert not proposals('if(flag){EXIT;a=b*8;}else{x=2;a=b*8;}', '#define EXIT return 3;\n')
