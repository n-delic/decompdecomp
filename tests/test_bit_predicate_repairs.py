"""Small invented scalar functions, including defined negative integer operands."""
import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs
from decomprepair import bit_predicate_repairs as repairs
from decomprepair import scope_topology_repairs

SCOPE=dict(language='c',int_bits=32)
SOURCE='int f(unsigned short word,int index){if((word & (1 << index)) == (1 << index)) return 1;return 0;}'
STAGE='int f(unsigned int word,unsigned int mask){int result;result=(word & mask)==mask;return result;}'


def test_pure_predicate_has_bounded_return_scopes_and_fresh_helper():
    choices=repairs.variants(SOURCE,'f',arch='mips',**SCOPE)
    assert len(choices)==7
    assert 'do{do{do{do{do{return ' in choices[3][1]
    assert all(text.count('if(__repair_bit_predicate(')==1 for _,text in choices)
    retuned=repairs.variants(choices[0][1],'f',arch='mips',**SCOPE)
    assert retuned and all(s!=choices[0][1]for _,s in retuned)
    assert not repairs.variants(SOURCE,'f',arch='mips')
    assert not repairs.variants(SOURCE,'f',arch='mips',language='c++',int_bits=32)


@pytest.mark.parametrize('change',[
    lambda s:s.replace('unsigned short word','volatile unsigned short word'),
    lambda s:s.replace('unsigned short word','unsigned long long word'),
    lambda s:s.replace('(1 << index)','read_mask()'),
    lambda s:s.replace('word &','word++ &'),
    lambda s:s.replace('return 0;','observe(&word);return 0;'),
    lambda s:'#define word value\n'+s,
    lambda s:s.replace('if(', '{int index;if(').replace('return 0;}', 'return 0;}}'),
])
def test_predicates_reject_ambiguous_bindings_effects_and_widths(change):
    assert not repairs.variants(change(SOURCE),'f',arch='mips',**SCOPE)


def test_staging_uses_a_real_intermediate_and_retains_evaluations():
    choices=repairs.stages(STAGE,'f',arch='mips',**SCOPE)
    assert choices and '__repair_masked_bits' in choices[0][1]
    assert 'result=(__repair_masked_bits==0)' in choices[0][1]
    following=STAGE.replace('return result;','mask=word+mask;return result;')
    # The next statement overwrites an operand, so it cannot cross the predicate.
    assert len(repairs.stages(following,'f',arch='mips',**SCOPE))==1
    address=STAGE.replace('int result;','unsigned int address;int result;').replace('return result;','address=word+mask;return result+address;')
    choices=repairs.stages(address,'f',arch='mips',**SCOPE)
    assert len(choices)==2
    assert choices[1][1].index('address=word+mask;')<choices[1][1].index('result=(__repair_masked_bits==0)')


def test_scope_merge_preserves_lexical_blocks_and_statement_order():
    source='void f(void){int a;int b;do{int x;a=3;}while(0);do{int x;b=a+2;}while(0);}'
    choices=scope_topology_repairs.variants(source,'f',arch='mips',**SCOPE)
    merged=next(s for family,s in choices if family=='operation_scope_merge')
    assert 'do{{int x;a=3;}{int x;b=a+2;}}while(0);' in merged
    for control in ['break;','continue;','return;','goto done;','case 1:']:
        unsafe=source.replace('a=3;',control+'a=3;')
        assert not any(family=='operation_scope_merge'for family,_ in scope_topology_repairs.variants(unsafe,'f',arch='mips',**SCOPE))


def test_shift_scopes_are_existing_complete_statements_only():
    source='void f(void){int a;int b;a=1;b=a<<2;use(b);}'
    choices=scope_topology_repairs.variants(source,'f',arch='mips',**SCOPE)
    assert len(choices)==3 and all('b=a<<2;' in s for _,s in choices)
    for bad in ['b=read()<<2;','b=a++<<2;']:
        assert not scope_topology_repairs.variants(source.replace('b=a<<2;',bad),'f',arch='mips',**SCOPE)


def test_normal_proposal_pipeline_exposes_new_family():
    labels={}
    choices=draft_repairs.variants(SOURCE,'f','mips',labels=labels,entry_scope=SCOPE)
    assert any(labels[s]=='inline_bit_predicate' for s in choices)


def test_generated_predicates_keep_signed_and_unsigned_bit_semantics(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('host C compiler unavailable')
    source='int f(int word,int mask){int result;result=(word & mask)==mask;return result;}'
    candidates=repairs.variants(source,'f',arch='mips',**SCOPE)[3:4]+repairs.stages(source,'f',arch='mips',**SCOPE)
    assert len(candidates)==2
    for i,(_,candidate) in enumerate(candidates):
        text=source+'\n'+candidate.replace('int f(', 'int g(')+'\n'+'''int main(void){unsigned int seed=7;int i;for(i=0;i<100000;i++){int a,b;seed=seed*1664525U+1013904223U;a=(int)seed;seed=seed*1664525U+1013904223U;b=(int)seed;if(f(a,b)!=g(a,b))return 1;}return 0;}'''
        path=tmp_path/f'{i}.c';path.write_text(text);binary=tmp_path/f'{i}.out'
        subprocess.run([cc,'-std=gnu89','-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,timeout=10)


def test_staged_declaration_keeps_later_declarations_legal_in_gnu89():
    source='int f(unsigned int word,unsigned int mask){int a=(word & mask)==mask;int b=3;return a+b;}'
    choices=repairs.stages(source,'f',arch='mips',**SCOPE)
    assert choices and 'int a=({' in choices[0][1] and 'int b=3;' in choices[0][1]


def test_only_our_pure_known_helper_body_is_retuned():
    helper=repairs.variants(SOURCE,'f',arch='mips',**SCOPE)[2][1]
    assert repairs.variants(helper,'f',arch='mips',**SCOPE)
    assert not repairs.variants(helper.replace('return (word_&mask)==mask;','observe();return (word_&mask)==mask;'),'f',arch='mips',**SCOPE)


def test_masked_sequence_reuses_only_disjoint_existing_storage():
    from decomprepair import bit_role_repairs
    source='int f(unsigned int word,unsigned int mask){unsigned int old;unsigned int value;int result;old=word+1;use(old);value=mask+2;use(value);result=(word & mask)==mask;return result;}'
    choices=bit_role_repairs.variants(source,'f',arch='mips',**SCOPE)
    assert 0<len(choices)<=bit_role_repairs.LIMIT
    assert all(family=='masked_role_sequence' and '__repair_masked_bits' in text for family,text in choices)
    assert any('old=mask+2;use(old);' in text for _,text in choices)
    assert not bit_role_repairs.variants(source,'f',arch='mips')
    escaped=source.replace('use(old);','use(&old);')
    assert not any('old=mask+2;use(old);' in text for _,text in bit_role_repairs.variants(escaped,'f',arch='mips',**SCOPE))


def test_generated_helper_outside_focal_function_is_not_retuned():
    source=repairs.variants(SOURCE,'f',arch='mips',**SCOPE)[0][1]+'\nint unrelated(void){return 0;}'
    assert not repairs.variants(source,'unrelated',arch='mips',**SCOPE)


def test_short_allocation_lane_includes_register_only_and_scheduling_shapes():
    from decompnet.app import allocation_search
    shape=dict(register_only=True,target_instructions=12,candidate_instructions=12,
               target_frame=16,candidate_frame=16,register_cycles=[])
    assert allocation_search.allowance(shape)==allocation_search.CHAIN_LIMIT
    assert allocation_search.allowance(dict(shape,register_cycles=[['a','b']]))==allocation_search.LIMIT
    assert allocation_search.allowance(dict(shape,register_only=False,scheduling=True))==allocation_search.CHAIN_LIMIT
    assert allocation_search.allowance(dict(shape,candidate_frame=32))==0
