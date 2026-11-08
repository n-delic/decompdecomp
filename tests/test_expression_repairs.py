"""Generic hypotheses isolated from successful matching edits, never accepted without the judge."""


import pytest

from decompnet.app import expression_repairs


def test_mask_cast_and_address_constant_candidates():
    source = 'extern char D_A1000000[]; int f(int x) { x = (x + 3) & 0xFFFF; return x | (unsigned int)D_A1000000; }'
    candidates = expression_repairs.variants(source, 'f', 'mips')
    assert any('(unsigned short)(x + 3)' in c for c in candidates)
    assert any('x | 0xA1000000' in c for c in candidates)
    assert all('0xA1000000' not in c for c in expression_repairs.variants(source, 'f', 'x86_64'))


@pytest.mark.parametrize('declaration', ['extern int D_A1000000;', 'extern char *D_A1000000;'])
def test_numeric_name_is_not_proof_that_a_global_load_is_an_address(declaration):
    source = declaration + ' int f(int x) { return x | (unsigned int)D_A1000000; }'
    assert not expression_repairs.variants(source, 'f', 'mips')
    address = source.replace('(unsigned int)D_A1000000', '(unsigned int)(&D_A1000000)')
    assert 'return x | 0xA1000000;' in expression_repairs.variants(address, 'f', 'mips')[0]


def test_cache_is_block_local_preserves_global_loads_elsewhere_and_avoids_collisions():
    source = 'extern void *state; void f(void *p) { if(p) { p = get(); store(p); send(state, p); } send(state, p); }'
    out, = expression_repairs.variants(source, 'f', 'mips')
    assert 'if(p) {\nvoid *__repair_cached_pointer = state;' in out
    assert 'send(__repair_cached_pointer, p); } send(state, p);' in out
    assert not expression_repairs.variants(source.replace('void *p)', 'void *__repair_cached_pointer)'), 'f', 'mips')


def test_opaque_source_and_other_functions_are_not_repaired():
    src = 'int g(int x) { return (x + 1) & 0xFFFF; }\nint f(void) { return 0; }'
    assert not expression_repairs.variants(src, 'f', 'mips')
    assert not expression_repairs.variants('int f(void) { /* (x + 1) & 0xFFFF */ return 0; }', 'f', 'mips')


@pytest.mark.parametrize('rhs', ['(u16 *)a+i++','(u16 *)a+next()', '(u16 *)next()+i',
                                '(u16 *)a+i*2', '(u16 *)a+(i+1)', '(u64 *)a+i'])
def test_scaled_pointer_addition_skips_complex_or_side_effecting_operands(rhs):
    assert not expression_repairs.variants(f'void f(){{q={rhs};}}','f','mips')


def test_scaled_pointer_addition_skips_unknown_and_64_bit_targets_and_opaque_text():
    source='void f(){q=(u16 *)a+i;}'
    for arch in ('x86_64','aarch64','unknown'):
        assert not expression_repairs.variants(source,'f',arch)
    assert not expression_repairs.variants('void f(){/* q=(u16 *)a+i; */say("(u16 *)a+i;");}','f','mips')


@pytest.mark.parametrize('source',[
    'void f(){int x;volatile int a;int b;x=a+b;}',
    'void f(){int x;int *a;int b;x=a+b;}',
    'void f(){float x;float a;float b;x=a+b;}',
    'void f(){unsigned int x;unsigned int a;unsigned int b;x=a+b;}',
    'void f(){int x;int a;int b;x=next()+b;}',
    'void f(){int x;int a;int b;x=a+++b;}',
    'void f(){int x;int a;int b;x=*a+b;}',
    'int __repair_inline_sum(int,int);void f(){int x;int a;int b;x=a+b;}'])
def test_inline_sum_skips_unsupported_operands_types_and_helper_collisions(source):
    assert not any(c.startswith('static inline int __repair_inline_sum') for c in expression_repairs.variants(source,'f','mips'))


def test_inline_sum_skips_unknown_and_64_bit_architectures():
    source='void f(){int x;int a;int b;x=a+b;}'
    for arch in ('unknown','x86_64','aarch64'):
        assert not any('__repair_inline_sum' in c for c in expression_repairs.variants(source,'f',arch))
