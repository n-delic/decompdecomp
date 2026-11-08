import pytest

from decompnet.app import known_bit_repairs as bits
from decompnet.app import post_call_capture_repairs as captures

ROWS=[('i','beq $t0, $zero, 0x18','beq $t0, $zero, 0x14'),(' ','nop','nop'),('i','j f+0x18','j f+0x14')]


MASK='unsigned int f(void){int x; unsigned int t=(unsigned short)x; int b=(t+2)*2; return b << 8;}'


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_known_even_mask_is_range_proven_and_shared(arch):
    result=bits.variants(MASK,'f',arch=arch)
    assert any('(b & 0xfffffffeu) << 8' in s for _,s in result)
    masked=MASK.replace('b << 8','(b & 0xfffffffeu) << 8')
    assert any('((unsigned int)b)' in s for _,s in bits.variants(masked,'f',arch=arch))


@pytest.mark.parametrize('old,new',[
    ('(t+2)*2','(t+2)*3'),('unsigned short','short'),('b << 8','++b; return b << 8'),
    ('b << 8','sink(&b); return b << 8'),('b << 8','b=1; return b << 8'),
    ('b << 8','b << 32'),('int x;','volatile int x;'),
])
def test_known_mask_declines_unsupported_values_and_mutations(old,new):
    assert not bits.variants(MASK.replace(old,new),'f',arch='mips')


CAPTURE='void f(void){int x; unsigned int t=(unsigned short)x; emit(t, 2); t &= 31; use(t);}'


def test_capture_moves_conversion_after_call_and_converts_original_argument():
    found=captures.variants(CAPTURE,'f',arch='mips')
    assert any('unsigned int t; emit((unsigned short)x, 2);\nt = (unsigned short)x;' in s for _,s in found)


@pytest.mark.parametrize('old,new',[
    ('emit(t, 2)','emit(t, ++x)'),('emit(t, 2)','emit(t, change())'),
    ('emit(t, 2)','emit(t, &x)'),('emit(t, 2)','emit(t, t)'),
    ('emit(t, 2)','if(x) emit(t, 2)'),('emit(t, 2)','x=3; emit(t, 2)'),
    ('int x;','volatile int x;'),('emit(t, 2)','sink(&t); emit(t, 2)'),
])
def test_capture_does_not_cross_mutations_control_aliases_or_opaque_args(old,new):
    assert not captures.variants(CAPTURE.replace(old,new),'f',arch='mips')


def test_capture_and_mask_decline_opaque_macros():
    assert not captures.variants(CAPTURE,'f','#define emit(a,b) (++x)',arch='mips')
    assert not bits.variants(MASK.replace('return b','HIDDEN; return b'),'f','#define HIDDEN ++b',arch='mips')


def test_conditional_mask_does_not_establish_a_dominating_bound():
    s=MASK.replace('int b=', 'if(x) t &= 31; int b=')
    assert not bits.variants(s,'f',arch='mips')
    s=MASK.replace('int b=', 't &= 31; int b=')
    assert bits.variants(s,'f',arch='mips')
