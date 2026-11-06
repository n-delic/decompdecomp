"""Actual byte loads justify the conversion; arbitrary range guesses do not."""
import shutil
import subprocess
import sys

import pytest

from decompnet.app import decoded_byte_repairs, draft_repairs

HEADER='typedef unsigned char u8; typedef unsigned int u32;'


def generate(*args):
    return decoded_byte_repairs.variants(*args,language='c',int_bits=32)


def source(gap='', load='v=*((u8 *)p);', chain='v=(n=v>>4);', decl='u32 v;int n;'):
    return 'int demo(u8 *p){'+decl+load+gap+chain+'return n+(v&12);}'


def test_real_byte_decode_keeps_assignments_and_evaluations():
    original=source('p++;')
    result=generate(original,'demo',HEADER,'mips')
    assert result==[('decoded_byte_normalization',source('p++;',chain='v=(n=(u8)(v>>4));'))]
    assert result[0][1].count('*((u8 *)p)')==1


@pytest.mark.parametrize('gap', ['v++;','++v;','v=999;','v<<=4;', 'if(n)v=99;',
                                  '{typedef signed char Other;Other v;v=1;}'])
def test_mutated_or_ambiguous_range_is_not_normalized(gap):
    assert not generate(source(gap),'demo',HEADER,'mips')


@pytest.mark.parametrize('load', ['v=*((signed char *)p);','v=*((u32 *)p);','v=get();','v=1000;'])
def test_requires_visible_unsigned_byte_load(load):
    assert not generate(source(load=load),'demo',HEADER,'mips')


@pytest.mark.parametrize('change', [
    {'gap':'escape(&(v));'}, {'gap':'escape(&n);'},
    {'chain':'v=(n=v>>8);'}, {'chain':'v=(n=v>>0);'},
    {'decl':'signed char v;int n;'},
])
def test_escaped_or_unsupported_carriers_and_shifts(change):
    assert not generate(source(**change),'demo',HEADER,'mips')


def test_qualified_alias_and_invoked_macro_remain_barriers():
    assert not generate(source(),'demo','typedef volatile unsigned char u8;typedef unsigned int u32;','mips')
    assert not generate(source('UPDATE;'),'demo',HEADER+'\n#define UPDATE v=999;\n','mips')


def test_all_byte_inputs_keep_both_widened_results_in_c(tmp_path):
    compiler=shutil.which('cc')
    if not compiler or sys.platform=='win32':
        pytest.skip('needs a host C compiler')
    functions=[]
    checks=[]
    for shift in range(1,8):
        original=source(chain=f'v=(n=v>>{shift});').replace('return n+(v&12);','return (n<<8)|v;')
        converted=generate(original,'demo',HEADER,'mips')[0][1]
        functions += [original.replace('demo(',f'original{shift}('), converted.replace('demo(',f'converted{shift}(')]
        checks.append(f'if(original{shift}(&byte)!=converted{shift}(&byte)) return {shift};')
    unit=tmp_path/'decode.c'
    unit.write_text(HEADER+'\n'+'\n'.join(functions)+'\nint main(void){int i;u8 byte;for(i=0;i<256;i++){byte=i;'+''.join(checks)+'}return 0;}')
    binary=tmp_path/'decode'
    subprocess.run([compiler,str(unit),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,timeout=10)


def test_shared_default_draft_budget_includes_family():
    labels={}
    candidates=draft_repairs.variants(source(),'demo','mips',header=HEADER,labels=labels,entry_scope={'language':'c','int_bits':32})
    assert decoded_byte_repairs.FAMILY in draft_repairs.AGENT_FAMILIES
    assert any(labels[c]==decoded_byte_repairs.FAMILY for c in candidates)


def test_nested_function_capture_is_not_treated_as_an_unescaped_local():
    original=source('mutate();').replace('v=*((u8 *)p);','void mutate(void){v=999;}v=*((u8 *)p);')
    assert not generate(original,'demo',HEADER,'mips')


def test_binary_and_is_not_confused_with_an_address_escape():
    original=source('n=n & v;')
    assert generate(original,'demo',HEADER,'mips')


def test_lambda_capture_is_not_treated_as_an_unescaped_local():
    original=source('mutate();').replace('v=*((u8 *)p);','auto mutate=[&v](){v=999;};v=*((u8 *)p);')
    assert not generate(original,'demo',HEADER,'mips')


@pytest.mark.parametrize('scope', [{}, {'language':'c++','int_bits':32}, {'language':'c','int_bits':16}])
def test_unknown_or_reference_capable_language_is_not_guessed(scope):
    original=source('touch(v);')
    assert not decoded_byte_repairs.variants(original,'demo',HEADER,'mips',**scope)


def test_extension_keyword_does_not_hide_an_address_escape():
    original=source('escape(__extension__ &v);')
    assert not generate(original,'demo',HEADER,'mips')
