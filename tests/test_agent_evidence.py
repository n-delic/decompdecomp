"""Dependency and quality evidence, availability counts and conservative loop liveness."""






import pytest

from decompnet.app import register_reuse_repairs


@pytest.mark.parametrize('loop',[
    'for(i=0;i<n;i++){value=input[i];out[i]=value << 2;}',
    'while(i<n){value=input[i];out[i++]=value * 4;}',
    'do{value=input[i];out[i++]=value << 2;}while(i<n);',
    'for(i=0;i<n;i++){int value=2;out[i]=value << 2;}',
])
def test_final_reuse_inside_a_loop_requires_an_unconditional_reset(loop):
    declaration='' if 'int value=2' in loop else 'int value;'
    source='void f(int n,int *input,int *out){int i=0;'+declaration+loop+'}'
    assert any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


@pytest.mark.parametrize('loop',[
    'while(i<n){out[i++]=value << 2;}',
    'while(i<n){value=value+1;out[i++]=value << 2;}',
    'while(i<n){out[i]=value;value=input[i];out[i++]=value << 2;}',
    'while(i<n){if(input[i]){value=input[i];}out[i++]=value << 2;}',
    'while(value<n){value=input[i];out[i++]=value << 2;}',
    'while(i<n){value=input[i];while(i<n){out[i++]=value << 2;}}',
    'while(value << 2){out[i++]=3;}',
    'for(i=0;value << 2;i++){out[i]=3;}',
    'do{out[i++]=3;}while(value << 2);',
    'while(i<n)out[i++]=value << 2;',
])
def test_loop_carried_reads_conditions_conditional_resets_and_unknown_scopes_stay_blocked(loop):
    source='void f(int n,int *input,int *out){int i=0;int value=2;'+loop+'}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_parenthesized_address_escape_prevents_loop_register_reuse():
    source='void f(int n,int *out){int i;int value;int *alias=&((value));for(i=0;i<n;i++){value=i;out[i]=value << 2;}use(alias);}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))
