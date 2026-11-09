"""Synthetic source only: real compiler evidence stays in the private experiment folder."""
from decompnet.app import draft_repairs
from decompnet.app import inline_scheduling_repairs as repairs


def family(source, label):
    return [s for f,s in repairs.variants(source,'work') if f == label]


def test_inline_helper_keeps_types_and_other_functions():
    source = 'int other(void){return 4;} int work(void){int x; x=3; return x > -2;}'
    candidates = family(source,'inline_scalar_shape')
    assert candidates and 'int a, int b' in candidates[0]
    assert '__repair_inline(x, -2)' in candidates[0]
    assert 'int other(void){return 4;}' in candidates[0]
    assert not family(source.replace('int x;', 'unsigned int x;'),'inline_scalar_shape') or 'unsigned int a' in family(source.replace('int x;', 'unsigned int x;'),'inline_scalar_shape')[0]


def test_float_helper_does_not_convert_integer_product():
    assert family('float work(void){float x; x=2; return x * 0.25f;}','inline_scalar_shape')
    assert not family('int work(void){int x; x=2; return x * 0.25f;}','inline_scalar_shape')


def test_pointer_temporary_retains_extent_and_argument_order():
    source = 'void work(void){int data[4]; consume(3, &data[0]);}'
    candidates = family(source,'stack_address_temporary')
    assert candidates and 'int data[4]' in candidates[0]
    assert 'consume(3, __repair_address)' in candidates[0]
    assert not family(source.replace('&data[0]','&external[0]'),'stack_address_temporary')


def test_loop_backedge_keeps_do_entry_semantics_and_rejects_cross_jumps():
    source = 'void work(void){int i; i=0; again: i+=1; if(i<3){goto again;}}'
    assert 'do {' in family(source,'structured_backedge')[0]
    assert '} while (i<3);' in family(source,'structured_backedge')[0]
    assert not family(source.replace('i+=1;', 'if(i) goto elsewhere; i+=1;'),'structured_backedge')
    assert not family(source.replace('i=0;', 'i=0; if(i) goto again;'),'structured_backedge')


def test_invariants_reject_changed_inputs_and_escaped_locals():
    source = 'void work(void){int x; int y; int z; y=2; z=3; do {x=y+z; use(x);}while(check());}'
    assert family(source,'loop_invariant_placement')
    assert not family(source.replace('use(x);','y++; use(x);'),'loop_invariant_placement')
    assert not family(source.replace('use(x);','use(&y);'),'loop_invariant_placement')


def test_order_only_independent_scalar_assignments():
    source = 'int work(void){int a; int b; int x; int y; x=2; y=3; a=x; b=y; return a+b;}'
    assert any('b = y; a = x;' in s for s in family(source,'scalar_schedule_order'))
    assert not any('b = a; a = x;' in s for s in family(source.replace('b=y','b=a'),'scalar_schedule_order'))


def test_combinations_are_bounded_and_reach_shared_draft_pipeline():
    source = 'int work(void){int x; int y; int z; y=1; z=2; x=y+z; return x<5;}'
    assert family(source,'expression_placement')
    assert 0 < len(family(source,'combined_scheduling')) <= 4
    labels = {}
    draft_repairs.variants(source,'work','mips',labels=labels,limit=None)
    assert 'inline_scalar_shape' in labels.values()
    assert set(repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    assert not repairs.variants(source.replace('int x;', 'volatile int x;'),'work')
    assert not repairs.variants(source.replace('int work','int Class::work'),'work')


def test_grouped_bounds_preserve_condition_parentheses():
    source = 'int work(void){int x; int y; x=2; y=3; if(x> -2){y++;} if(y > (-2)){x++;} return x+y;}'
    grouped = family(source,'inline_scalar_shape')[0]
    assert 'if(__repair_inline(-2, x))' in grouped
    assert 'if(__repair_inline((-2), y))' in grouped


def test_variants_compile_and_keep_synthetic_results(tmp_path):
    import shutil
    import subprocess

    import pytest

    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('A host C compiler is required for the synthetic behavior check.')
    source = 'int work(void){int x; int y; int z; y=2; z=3; x=y+z; return x> -2;}'
    for index, (_, candidate) in enumerate(repairs.variants(source,'work')):
        path = tmp_path / f'candidate{index}.c'
        binary = tmp_path / f'candidate{index}'
        path.write_text(candidate+'\nint main(void){return work()!=1;}')
        subprocess.run([compiler,'-std=c99',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=15)
        subprocess.run([str(binary)],check=True,timeout=5)


def test_comparison_helpers_do_not_change_integer_promotions():
    for typ, bound in [('unsigned short', '70000'), ('int', '0xFFFFFFFF'), ('int', '2u')]:
        source = f'int work(void){{{typ} x; x=2; return x > {bound};}}'
        assert not family(source,'inline_scalar_shape')
