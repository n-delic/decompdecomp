"""Generic hypotheses isolated from successful matching edits, never accepted without the judge."""

import pytest

from decompnet.app import draft_repairs, expression_repairs


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


def test_saved_m2c_refinement_strips_typedef_block_before_type_replacement():
    from decompnet import seeds
    src = seeds.M2C_MACROS + 'extern M2C_UNK D_80001000;\nvoid f(void) { func_80002000(&D_80001000 + 4); }'
    out = draft_repairs.variants(src, 'f', 'mips', 'glabel f\njal func_80002000\n nop')[0]
    assert 'typedef s32 s32;' not in out
    assert 'extern char D_80001000[];' in out
    assert 'func_80002000(D_80001000 + 4)' in out


def test_identifier_argument_drop_is_individual_and_keeps_effects():
    src = 'void f(int x, int y) { send(x, y); send(x, y); }'
    labels = {}
    candidates = [c for c in draft_repairs.variants(src, 'f', 'mips', labels=labels)
                  if labels[c] == 'expression_or_near_repair']
    assert len(candidates) == 2
    assert all(c.count('send(x, y)') == 1 for c in candidates)
    labels = {}
    unsafe = draft_repairs.variants('void f(int x) { send(x, x++); }', 'f', 'mips', labels=labels)
    assert not [c for c in unsafe if labels[c] == 'expression_or_near_repair']


def test_call_argument_repairs_skip_function_like_macros():
    src = '#define FIELD(p, t, off) (*(t*)((char*)(p)+(off)))\nint f(int *p) { return FIELD(p, int, 4); }'
    assert not draft_repairs.variants(src, 'f', 'mips')


def test_saved_m2c_refinement_receives_real_assembly_for_forwarded_arguments():
    from decompnet import seeds
    src = seeds.M2C_MACROS + 'M2C_UNK func_80002000(s32);\nvoid f(void) { func_80002000(7); }'
    out = draft_repairs.variants(src, 'f', 'mips', 'glabel f\njal func_80002000\n addiu $a1, $zero, 7')[0]
    assert 'f(s32 arg0)' in out and 'func_80002000(arg0, 7)' in out
    assert not any('f(s32 arg0)' in c for c in draft_repairs.variants(src, 'f', 'arm', ''))


@pytest.mark.parametrize('typ,scale', [('u8',1),('s16',2),('u32',4)])
@pytest.mark.parametrize('arch', ['mips','ppc','x86','arm','thumb'])
def test_scaled_pointer_addition_keeps_operands_once_and_other_definitions(typ,scale,arch):
    source=f'void g(){{q=({typ} *)a+i;}} void f(){{q=({typ} *)a+i;}}'
    candidates=expression_repairs.variants(source,'f',arch)
    assert any(f'void f(){{q=({typ} *)(i * {scale} + (int)a);}}' in c for c in candidates)
    assert all(c.startswith(f'void g(){{q=({typ} *)a+i;}}') for c in candidates)
    assert any(f'i * {scale}' in c for c in draft_repairs.variants(source,'f',arch))


@pytest.mark.parametrize('rhs', ['(u16 *)a+i++','(u16 *)a+next()', '(u16 *)next()+i',
                                '(u16 *)a+i*2', '(u16 *)a+(i+1)', '(u64 *)a+i'])
def test_scaled_pointer_addition_skips_complex_or_side_effecting_operands(rhs):
    assert not expression_repairs.variants(f'void f(){{q={rhs};}}','f','mips')


def test_scaled_pointer_addition_skips_unknown_and_64_bit_targets_and_opaque_text():
    source='void f(){q=(u16 *)a+i;}'
    for arch in ('x86_64','aarch64','unknown'):
        assert not expression_repairs.variants(source,'f',arch)
    assert not expression_repairs.variants('void f(){/* q=(u16 *)a+i; */say("(u16 *)a+i;");}','f','mips')


def test_inline_sum_candidate_keeps_single_evaluation_and_definition_boundaries():
    source='void g(){int x;int a;int b;x=a+b;} void f(){int x;int a;int b;x=a+b;}'
    candidates=expression_repairs.variants(source,'f','mips')
    wrapped=[c for c in candidates if '__repair_inline_sum' in c]
    assert len(wrapped)==1
    assert wrapped[0].startswith('static inline int __repair_inline_sum(int left, int right)')
    assert 'void g(){int x;int a;int b;x=a+b;} void f(){int x;int a;int b;x = __repair_inline_sum(a, b);}' in wrapped[0]
    assert wrapped[0] in draft_repairs.variants(source,'f','mips')


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
