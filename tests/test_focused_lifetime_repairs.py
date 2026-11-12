from decomprepair.focused_lifetime_repairs import variants


def test_small_store_and_pointer_index_alternatives():
    source='void f(S *p, int i) { p->value = 125; p->table + i; }'
    proposals=variants(source,'f')
    assert any(f=='store_value_lifetime' and 'int repair_store_value = 125;' in s for f,s in proposals)
    assert any(f=='pointer_index_order' and '(i + p->table)' in s for f,s in proposals)


def test_chain_preserves_expression_value_and_same_type_conversions():
    source='int f(int *p) { int a; int b; if ((a = b = p[1] << 4) > 0) return a+b; return 0; }'
    assert any(f=='assignment_chain_lifetime' and '(b = p[1] << 4, a = b)' in s for f,s in variants(source,'f'))
    assert not any(f=='assignment_chain_lifetime' for f,s in variants(source.replace('int b;','short b;'),'f'))


def test_reject_volatile_oversized_literals_and_unbraced_store():
    assert not variants('void f(volatile S *p) { p->x = 1; }','f')
    assert not variants('void f(S *p) { p->x = 0xffffffff; }','f')
    assert not variants('void f(S *p,int a) { if (a) p->x = 1; }','f')


def test_pointer_addition_does_not_capture_part_of_a_larger_expression():
    assert not variants('void f(S *p, int i) { p->n + i * 2; }','f')
    assert not variants('void f(S *p, int i, int x) { x * p->n + i; }','f')


def test_shared_agent_and_autopilot_families_include_lifetime_candidates():
    from decompnet.app import draft_repairs, local_experiments
    from decomprepair.focused_lifetime_repairs import FAMILIES
    assert set(FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    assert set(FAMILIES) <= set(local_experiments.FAMILIES)
