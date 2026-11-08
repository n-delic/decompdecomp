
import pytest

from decompnet.app import narrow_expression_repairs as inline


def asm(*lines):
    return '\n'.join(f'/* {i*4:06X} {0x80001000+i*4:08X} 00000000 */ {line}' for i,line in enumerate(lines))


TARGET=asm('jal allocate','nop','move $s0, $v0','jal emit','nop','move $v0, $s0',
           'andi $a2, $v0, 31','jr $ra','nop')


CALLER=asm('jal f','nop','move $s1, $v0','jr $ra','nop')


SOURCE='void f(void) { int index; int other; index = allocate(); other = index & 31; emit(other); }\n'


INLINE='''int f(int n) {
 unsigned char b;
 int x;
 b = (n + 2) * 2;
 x = b | 123;
 x |= b << 8;
 return x;
}'''


FLOAT='''void f(int x) {
 volatile float a;
 volatile float b;
 volatile float c;
 a = x * 0.5f;
 c = x * 0.25f;
 b = x;
 emit(a, c);
}'''


DELAY=asm('jal allocate','swc1 $f0, 0x3c($sp)','jr $ra','nop')


@pytest.mark.parametrize('change',[
 lambda s:s.replace('(n + 2) * 2','side_effect(n)'),
 lambda s:s.replace('(n + 2) * 2','global_value + 2'),
 lambda s:s.replace('(n + 2) * 2','*(int*)n'),
 lambda s:s.replace('x = b | 123;','n++; x = b | 123;'),
 lambda s:s.replace('x = b | 123;','mutate(&(n)); x = b | 123;'),
 lambda s:s.replace('x = b | 123;','mutate(&(b)); x = b | 123;'),
 lambda s:s.replace('x = b | 123;','if(n) { x = b | 123; }'),
 lambda s:s.replace('int f(int n)','int f(volatile int n)'),
 lambda s:s.replace('unsigned char b;','volatile unsigned char b;'),
])
def test_side_effects_escapes_changed_inputs_and_control_flow_are_not_duplicated(change):
    assert inline.variants(change(INLINE),'f','','mips')==[]


def test_unknown_and_64_bit_abis_do_not_inherit_host_width_for_narrow_inlining():
    assert not inline.variants(INLINE,'f','','x86_64')
    assert not inline.variants(INLINE,'f','','unknown')


def test_real_m2c_accepts_caller_result_context_without_touching_shared_header():
    from decompnet import seeds, toolchains
    if not toolchains.installed('m2c'):pytest.skip('Needs installed pinned m2c')
    header='int allocate(void); void emit(void);'
    source=seeds.generate_from_asm('glabel f\n'+TARGET+'\nendlabel f\n','f','gcc-2.7.2-kmc',header+'\nint f();')
    assert source and 'return ' in source and 'void f(' not in source
    assert header=='int allocate(void); void emit(void);'
