import pytest

from decompnet.app import draft_repairs, local_experiments, store_address_repairs

SOURCE = '''void f(void *node, int base) {
    *((short *)((char *)((*(int *)((char *)node + 8) * 20) + base) + 12)) = 0;
}'''


def test_store_address_lifetime_keeps_each_load_in_its_statement():
    choices = store_address_repairs.variants(SOURCE, 'f', 'mips')
    assert len(choices) == 1
    candidate = choices[0]
    assert 'char *__repair_store_address = (char *)base;' in candidate
    assert '__repair_store_address += (*(int *)((char *)node + 8) * 20);' in candidate
    assert '*(short *)(__repair_store_address + 12) = 0;' in candidate
    assert candidate.count('*(int *)') == 1
    assert candidate.startswith('void f(void *node, int base) {')


@pytest.mark.parametrize('replacement', ['next()', 'index++', 'index = 3', '(index, 3)'])
def test_store_address_lifetime_does_not_split_side_effecting_offsets(replacement):
    source = SOURCE.replace('*(int *)((char *)node + 8) * 20', replacement)
    assert not store_address_repairs.variants(source, 'f', 'mips')


def test_store_address_lifetime_leaves_calls_volatile_and_other_functions_alone():
    assert not store_address_repairs.variants(SOURCE.replace('= 0;', '= call();'), 'f', 'mips')
    assert not store_address_repairs.variants(SOURCE.replace('int base', 'volatile int base'), 'f', 'mips')
    assert not store_address_repairs.variants(SOURCE, 'f', 'aarch64')
    assert not store_address_repairs.variants(SOURCE.replace('base)', 'base); #define X 1\n'), 'f', 'mips')
    prefix = 'void other(void) { /* preserve this body */ }\n'
    assert store_address_repairs.variants(prefix + SOURCE, 'f', 'mips')[0].startswith(prefix)
    assert not store_address_repairs.variants(SOURCE.replace('base)', '__repair_store_address)'), 'f', 'mips')


def test_store_address_lifetime_reaches_autopilot_and_agent_local_trials():
    labels = {}
    choices = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels)
    assert any(labels[s] == 'store_address_lifetime' for s in choices)
    assert 'store_address_lifetime' in local_experiments.FAMILIES
