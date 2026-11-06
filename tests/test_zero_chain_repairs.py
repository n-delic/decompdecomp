"""Independent zero stores retain values and exclude observable assignment order."""
import ctypes
import shutil
import subprocess

import pytest

from decompnet.app import zero_chain_repairs as repair

BASE = 'int sample(int input){int a;int b;a=(b=0);a+=input;b+=3;return a+b;}'


def proposals(source=BASE,header=''):
    return repair.variants(source,'sample',header,language='c',int_bits=32)


def test_real_stores_split_in_both_chain_spellings():
    for source in (BASE,BASE.replace('a=(b=0)','a=b=0')):
        result=proposals(source)
        assert result and result[0][0]==repair.FAMILY
        assert 'a = 0;\nb = 0;' in result[0][1]


@pytest.mark.parametrize('replacement', ['a=(b=1)','a=(b=input)','a=(b=call())','a=(a=0)','a=(b=0U)'])
def test_nonzero_effects_or_self_chains_are_not_guessed(replacement):
    assert not proposals(BASE.replace('a=(b=0)',replacement))


@pytest.mark.parametrize('source', [
    BASE.replace('int b;','volatile int b;'),
    BASE.replace('int b;','unsigned int b;'),
    BASE.replace('int b;','int *b;'),
    BASE.replace('a+=input','observe(&a);a+=input'),
    BASE.replace('a+=input','{Other a;}a+=input'),
    BASE.replace('a+=input','{int *a;}a+=input'),
    BASE.replace('a=(b=0);','if(input) a=(b=0);'),
    BASE.replace('a=(b=0);','for(;a=(b=0);){}'),
    BASE.replace('a=(b=0);','a=(b=/*keep*/0);'),
])
def test_types_escape_shadow_control_and_comments_fail_closed(source):
    assert not proposals(source)


def test_macros_and_invocation_evidence_are_guarded():
    assert not repair.variants(BASE,'sample')
    assert not repair.variants(BASE,'sample',language='c++',int_bits=32)
    assert not proposals('#include "hidden.h"\n'+BASE)
    assert not proposals(BASE,'#define int volatile int')
    assert not proposals(BASE.replace('a+=input','USE(a);a+=input'),'#define USE(x) observe(x)')
    assert not proposals(BASE,'#define hidden observe(a)')
    assert not proposals(BASE+'\nint observer=__LINE__;')
    assert not proposals(BASE.replace('int a;int b;','s32 a;s32 b;'),'typedef unsigned short s32;')
    assert proposals(BASE.replace('int a;int b;','s32 a;s32 b;'),'typedef int s32;')
    assert proposals(BASE.replace('int a;int b;','s32 a;s32 b;'),'typedef signed int s32;')


def test_condition_reads_are_not_mistaken_for_shadow_declarations():
    assert proposals(BASE.replace('a+=input','if(a){input++;}a+=input'))


def test_native_outputs_agree_across_signed_inputs(tmp_path):
    cc=shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    for i,text in enumerate([BASE]+[text for _,text in proposals()]):
        source,lib=tmp_path/f'{i}.c',tmp_path/f'{i}.so'
        source.write_text(text)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(source),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        f=ctypes.CDLL(str(lib)).sample
        f.argtypes=[ctypes.c_int]
        for value in (-32768,-7,0,42,32767):
            assert f(value)==value+3


def test_shared_generator_uses_verified_invocation():
    from decompnet.app import draft_repairs
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert {s for _,s in proposals()} <= set(draft_repairs.variants(BASE,'sample','mips',limit=100,entry_scope={'language':'c','int_bits':32}))
