from decompnet.app import draft_repairs, local_experiments, register_repairs


def test_dead_comparison_temporary_reuse_is_bounded_and_scoped():
    source = '''int other(){return 0;}
void f(Thing *p) {
 int scratch; /* keep this comment */
 if (p->size < 17) { p->a = 2; }
 else if (p->size < 257) { p->a = 3; }
 scratch = p->other;
 use(scratch);
}'''
    variants = register_repairs.variants(source, 'f', 'mips')
    assert len(variants) == 2
    text = variants[0][1]
    assert 'scratch = p->size;' in text
    assert text.count('(unsigned int)scratch') == 2
    assert '/* keep this comment */' in text
    assert text.startswith('int other(){return 0;}')
    assert not register_repairs.variants(source.replace('scratch = p->other;', 'use(scratch);'), 'f', 'mips')
    assert not register_repairs.variants(source.replace('scratch = p->other;', 'scratch = scratch + 1;'), 'f', 'mips')
    assert not register_repairs.variants(source.replace('int scratch;', 'int scratch; use(scratch);'), 'f', 'mips')
    assert not register_repairs.variants(source.replace('p->size', 'read_size(p)'), 'f', 'mips')
    assert not register_repairs.variants(source.replace('Thing *p', 'volatile Thing *p'), 'f', 'mips')
    assert 'comparison_reuse' in local_experiments.FAMILIES
    assert any('scratch = p->size;' in s for s in draft_repairs.variants(source,'f','mips'))


def test_coordinated_integer_address_order_preserves_loads():
    source = '''void f(char *p, int off) {
 *(int *)((*((u8 **)(p + 4))) + off) = 1;
 *(int *)(((*((u8 **)(p + 8))) + off) + 16) = 2;
}'''
    variants = register_repairs.variants(source,'f','mips')
    assert len(variants) == 2
    text = variants[0][1]
    assert text.count('off + (unsigned int)') == 2
    assert text.count('*((u8 **)') == 2
    assert variants == register_repairs.variants(source,'f','arm')
    assert not register_repairs.variants(source,'f','aarch64')
    assert not register_repairs.variants(source.replace('+ off)', '+ off++)'), 'f','mips')
    assert not register_repairs.variants(source.replace('p + 4', 'get_pointer() + 4').replace('p + 8', 'get_pointer() + 8'), 'f','mips')
    assert 'integer_address_order' in local_experiments.FAMILIES
    assert any('off + (unsigned int)' in s for s in draft_repairs.variants(source,'f','mips'))
