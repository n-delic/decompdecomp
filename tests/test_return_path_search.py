




from decompnet.app import loop_weight_repairs
from tests.test_return_contract_repairs import asm

SOURCE='''void f(int a){
 int value;
 int other;
 value=a+1;
 if(value<=0)
 {
   emit(value);
 }
}'''


TARGET=asm('addiu $v0, $a0, 1','bgtz $v0, 0x80001014','move $a1, $v0',
           'li $v0, 3','sw $v0, 0($a0)','jr $ra','nop')


def test_all_mapped_quotient_sites_are_visited_before_deeper_scopes():
    source='int f(int a,int b){\nint x;\nx=a/b;\nx=(a+1)/b;\nx=(a+2)/b;\nreturn x;\n}'
    choices=loop_weight_repairs.variants(source,'f',focus_lines=[3,4,5],depths=(2,1,3))
    assert len(choices)==9
    assert all(c.count('do {')==2 for _,c in choices[:3])
    assert all(c.count('do {')==1 for _,c in choices[3:6])
    assert 'x=(a+2)/b;' in choices[2][1]
