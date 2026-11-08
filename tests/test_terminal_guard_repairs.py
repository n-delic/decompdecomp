"""Homemade branch-layout controls; no game source/data."""


import ctypes
import shutil
import subprocess

import pytest

from decompnet.app import terminal_guard_repairs as repair


def test_void_nested_actions_become_terminal_guard():
    source = 'void sample(int *p){int v=*p;if(v&8){*p=v|32;}else{if(v&4){*p=v|2;}else{*p=v|4;}*p|=8;}}'
    result = repair.variants(source,'sample')
    assert len(result) == 2
    assert all(f == repair.FAMILY for f, _ in result)
    assert any('if (!(v&8))' in s and 'return;}*p=v|32;' in s for _, s in result)


def test_value_return_guard_can_become_positive_branch():
    source = 'int sample(int *p){if(p==0){return 0;}*p=5;return 1;}'
    result = repair.variants(source,'sample')
    assert len(result) == 1
    assert 'if (!(p==0))' in result[0][1]
    assert '*p=5;return 1;' in result[0][1]
    assert '} else {return 0;}' in result[0][1]


def test_only_direct_candidate_owned_inline_helper_is_changed():
    helper = 'static __inline__ void task(int*p){if(*p){*p=7;}else{*p=8;}}'
    other = 'static __inline__ void unused(int*p){if(*p){*p=1;}else{*p=2;}}'
    target = 'void sample(int *p){task(p);}'
    result = repair.variants(helper+'\n'+other+'\n'+target,'sample')
    assert len(result) == 2
    assert all(other+'\n'+target in s for _, s in result)
    assert repair.variants(other+target,'sample') == []
    assert repair.variants(target,'sample',helper) == []


@pytest.mark.parametrize('source', [
    'int sample(int*p){if(p){return 0;}else{*p=2;return 1;}}',
    'void sample(int*p){if(*p){int x=1;*p=x;}else{*p=2;}}',
    'void sample(int*p){if(*p){unsigned long x=1;*p=x;}else{*p=2;}}',
    'void sample(int*p){if(*p){_Alignas(8) int x=1;*p=x;}else{*p=2;}}',
    'int sample(int*p){if(p){return 0;}int x=2;*p=x;return 1;}',
    'int sample(int*p){if(p)if(*p){return 0;}*p=2;return 1;}',
    'void sample(int*p){if(*p){*p=1;}else{*p=2;}*p=3;}',
    'void sample(int*p){if(*p){goto end;}else{*p=2;}end:;}',
    'void sample(int*p){if(*p){*p=__LINE__;}else{*p=2;}}',
    '#define return side_effect();\nvoid sample(int*p){if(*p){*p=1;}else{*p=2;}}',
    '#define void int\nvoid sample(int*p){if(*p){*p=1;}else{*p=2;}}',
    '#include "hidden.h"\nvoid sample(int*p){if(*p){*p=1;}else{*p=2;}}',
    '%:define FOO *p=3\nvoid sample(int*p){if(*p){FOO;}else{*p=2;}}',
])
def test_unsupported_control_scope_or_macros_stay_untouched(source):
    assert repair.variants(source,'sample') == []


def test_standard_null_constant_is_not_an_unknown_control_macro():
    source = 'int sample(int*p){if(p==NULL){return 0;}*p=2;return 1;}'
    assert repair.variants(source,'sample','#define NULL ((void *)0)')
    assert not repair.variants(source,'sample','#define NULL side_effect()')


def test_native_actions_and_condition_evaluate_once_on_both_paths(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    prefix = 'int count;int probe(int x){count++;return x;}int observed(void){return count;}void reset(void){count=0;}'
    source = 'int sample(int*p,int x){if(probe(x)){return 11;}*p+=3;return 7;}'
    proposals = repair.variants(source,'sample')
    assert proposals
    for i, text in enumerate([source]+[s for _, s in proposals]):
        file = tmp_path/f'source{i}.c'
        lib = tmp_path/f'lib{i}.so'
        file.write_text(prefix+text)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(file),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        native = ctypes.CDLL(str(lib))
        native.sample.argtypes = [ctypes.POINTER(ctypes.c_int),ctypes.c_int]
        for x in [-3,0,8]:
            value = ctypes.c_int(20)
            native.reset()
            assert native.sample(ctypes.byref(value),x) == (11 if x else 7)
            assert value.value == (20 if x else 23)
            assert native.observed() == 1


def test_native_nested_void_store_and_timer_order(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    source = 'void sample(unsigned*p,unsigned*t){unsigned v=*p;if(v&8){*p=v|32;}else{if(v&4){*p=(v|2)&~4U;}else{*p=(v|4)&~2U;}*t=50;*p|=8;}}'
    proposals = repair.variants(source,'sample')
    assert len(proposals) == 2
    for i, text in enumerate([source]+[s for _, s in proposals]):
        file = tmp_path/f'void{i}.c'
        lib = tmp_path/f'void{i}.so'
        file.write_text(text)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(file),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        native = ctypes.CDLL(str(lib))
        native.sample.argtypes = [ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
        for initial in range(256):
            value, timer = ctypes.c_uint(initial), ctypes.c_uint(17)
            native.sample(ctypes.byref(value),ctypes.byref(timer))
            expected = initial|32 if initial&8 else (((initial|2)&~4) if initial&4 else ((initial|4)&~2))|8
            assert (value.value,timer.value) == (expected,17 if initial&8 else 50)


def test_overloaded_logical_negation_is_not_rewritten():
    source = 'void sample(T p){if(p){done();}else{other();}}'
    assert not repair.variants(source,'sample','struct T { bool operator!(); };')
