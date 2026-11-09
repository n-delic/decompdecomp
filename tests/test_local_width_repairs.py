import pytest

from decompnet.app import draft_repairs, local_experiments, local_width_repairs

SOURCE = 'void f(unsigned int bits) { do { unsigned int value; value = bits & 15; store((signed char)value); } while (0); }'


def test_local_width_uses_an_existing_narrow_cast_as_its_hint():
    choices = local_width_repairs.variants(SOURCE, 'f')
    assert len(choices) == 3
    assert any('char value;' in s for s in choices)
    assert all('value = bits & 15;' in s and 'store((signed char)value);' in s for s in choices)
    short = local_width_repairs.variants(SOURCE.replace('signed char', 'short'), 'f')
    assert len(short) == 2 and any('short value;' in s for s in short)


@pytest.mark.parametrize('source', [
    SOURCE.replace('store((signed char)value)', 'store(value)'),
    SOURCE.replace('unsigned int value;', 'volatile unsigned int value;'),
    SOURCE.replace('value = bits', 'use(&value); value = bits'),
    SOURCE.replace('value = bits', '{int value;store((signed char)value);} value = bits'),
    SOURCE.replace('value = bits', '#define X 1\nvalue = bits'),
])
def test_local_width_skips_unhinted_volatile_escaped_shadowed_or_macro_candidates(source):
    assert not local_width_repairs.variants(source, 'f')


def test_local_width_preserves_other_definitions_and_reaches_both_searches():
    prefix = 'void other(void) { unsigned int value; }\n'
    choices = local_width_repairs.variants(prefix+SOURCE, 'f')
    assert choices and all(s.startswith(prefix) for s in choices)
    labels = {}
    choices = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels)
    assert any(labels[s] == 'local_width' for s in choices)
    assert 'local_width' in local_experiments.FAMILIES
