"""Safe donor selection, shared output feedback and bounded measured sequence continuations."""




import pytest

from decompnet.app import narrow_call_repairs as narrow

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


ROWS=[[' ','addiu $sp, $sp, -0x20','addiu $sp, $sp, -0x20'],
      ['r','move $s0, $a0','move $s1, $a0'],[' ','jr $ra','jr $ra']]


def parent(**changes):
    result=dict(source='measured source',depth=4,productive_steps=3,score=5,ceiling=10,
                path=['typed_pointer_snapshot','narrow_call_destination','branch_scalar_lifetime','inline_call_count'],
                unchanged_steps=0,parent_output='before',output='after',baseline_rows=ROWS,report=dict(rows=ROWS))
    return result|changes


def test_narrow_argument_is_not_moved_across_another_argument_using_that_scalar():
    source='void callee(int,short);void f(int y){int base;callee(y++,y);}'
    assert not narrow.variants(source,'f',arch='mips')


def test_donor_ranking_prefers_an_established_other_phase_not_declaration_order():
    source=SOURCE.replace('int base=x+6;', 'int scratch; int base=x+6;')
    proposals=narrow.variants(source,'f',arch='mips')
    assert 'base = (short)y;' in proposals[0][1]


def test_already_narrow_value_can_still_use_a_different_destination_lifetime():
    source='void callee(short);void f(int x){int old;int y;y=(short)x;callee(y);}'
    assert narrow.variants(source,'f',arch='mips')


def test_single_call_donors_remain_available_without_displacing_grouped_branch_repairs():
    source='void callee(short);void f(int y){int base;callee(y);}'
    assert narrow.variants(source,'f',arch='mips')[0][0]==narrow.FAMILY
