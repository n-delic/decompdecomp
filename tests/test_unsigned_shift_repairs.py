"""Unsigned packing staging: modular arithmetic and conservative binding scope."""


import ctypes
import shutil
import subprocess

import pytest

from decompnet.app import unsigned_shift_repairs as repair

BASE = 'typedef unsigned int u32;u32 sample(u32 input){u32 c;u32 lo,hi;c=input;lo=c<<8;hi=(c<<16)+2;return lo+hi;}'


def proposals(source=BASE, header=''):
    return repair.variants(source,'sample',header,language='c',int_bits=32)


def test_combined_sites_preserve_low_first_order_and_are_bounded():
    source = BASE.replace('return lo+hi;', 'hi=(c<<8)+4;return lo+hi;')
    result = proposals(source)
    assert len(result) == 3
    assert 'lo=c<<8;\nhi = c << 16; hi += 2;' in result[0][1]
    assert 'hi = c << 8; hi += 4;' in result[0][1]
    assert all(family == repair.FAMILY for family,_ in result)
    assert not proposals(BASE.replace('c<<16','c<<0'))
    assert not proposals(BASE.replace('+2;', '+0;'))


@pytest.mark.parametrize('declaration', [
    'volatile u32 hi;', 'u32 hi[2];', 'u32 *hi;', 'static u32 hi;',
    'QUAL u32 hi;', 'unsigned short hi;', 'int hi;',
])
def test_nonordinary_or_unknown_bindings_are_not_changed(declaration):
    assert not proposals(BASE.replace('u32 lo,hi;', 'u32 lo;'+declaration))


@pytest.mark.parametrize('extra', [
    '{volatile u32 x,hi;}', '{T hi;}', '{u32 hi;}',
    '{volatile u32 (hi);}', '{T (*hi);}',
    '{u32*hi;}', '{u32 x,*hi;}', '{u32(hi);}', '{void other(int(hi));}',
    'sink(&hi);', 'sink(&(hi));', 'DECLARE(hi);',
])
def test_shadows_and_escaped_objects_fail_closed(extra):
    assert not proposals('#define DECLARE(x) volatile unsigned int x\n'+BASE.replace('c=input;', extra+'c=input;'))


@pytest.mark.parametrize('prefix', [
    '#include "hidden.h"\n', '#define int volatile int\n',
    '#define u32 volatile unsigned int\n', '#define hidden hi\n',
    '#define DECL(x) unsigned x;\n', '#define JOIN(a,b) a##b\n',
])
def test_hidden_preprocessor_bindings_fail_closed(prefix):
    assert not proposals(prefix+BASE)


def test_unrelated_expression_macros_and_local_record_are_allowed():
    source = '#define G (*(unsigned int*)0x1000)\n'+BASE.replace('u32 c;', 'typedef struct {int a;} Record;Record record;u32 c;').replace('c=input;', 'record.a=1;c=input+G;')
    assert proposals(source)


def test_c_octal_literals_and_modular_tag_bounds():
    assert proposals(BASE.replace('c<<16','c<<020').replace('+2;', '+037777777777U;'))
    assert not proposals(BASE.replace('c<<16','c<<040'))
    assert not proposals(BASE.replace('+2;', '+0x100000000ULL;'))
    assert not proposals(BASE.replace('c<<16','c<<08'))


def test_for_headers_and_unbraced_conditionals_are_not_split():
    assert not proposals(BASE.replace('hi=(c<<16)+2;', 'if(input) hi=(c<<16)+2;'))
    assert not proposals(BASE.replace('hi=(c<<16)+2;', 'for(hi=(c<<16)+2;hi;hi--){}'))


def test_unknown_invocation_stays_disabled():
    assert not repair.variants(BASE,'sample')
    assert not repair.variants(BASE,'sample',language='c++',int_bits=32)


def test_native_modular_arithmetic_at_unsigned_boundaries(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    for index,source in enumerate([BASE]+[s for _,s in proposals()]):
        path,lib = tmp_path/f'{index}.c',tmp_path/f'{index}.so'
        path.write_text(source)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(path),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        function = ctypes.CDLL(str(lib)).sample
        function.argtypes, function.restype = [ctypes.c_uint],ctypes.c_uint
        for value in [0,1,127,128,255,65535,0x7fffffff,0x80000000,0xffffffff]:
            assert function(value) == ((value<<8)+(value<<16)+2)&0xffffffff
