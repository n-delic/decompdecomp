import pytest

from decompnet.app import argument_mask_repairs as repairs

SOURCE = '''void work(int key) {
    int count; void *node;
    node = head; count = 0;
    if (node) {
again:
        if ((count & 255) == (key & 255)) { use(node); return; }
        node = next(node); count++;
        if (node) goto again;
    }
}'''


def test_capture_retains_entry_guard_backedge_and_integer_type():
    [(family, source)] = repairs.variants(SOURCE, 'work')
    assert family == 'loop_argument_mask'
    assert 'int __repair_argument_mask;' in source
    assert source.index('if (node)') < source.index('__repair_argument_mask = key & 255;') < source.index('again:')
    assert 'if ((count & 255) == __repair_argument_mask)' in source
    assert 'if (node) goto again;' in source
    assert source.count('next(node)') == SOURCE.count('next(node)')
    unsigned = repairs.variants(SOURCE.replace('int key', 'u32 key'), 'work', 'typedef unsigned int u32;')[0][1]
    assert 'unsigned int __repair_argument_mask;' in unsigned


@pytest.mark.parametrize('change', [
    ('int key', 'volatile int key'),
    ('int key', 'unsigned short key'),
    ('int key', 'long long key'),
    ('count = 0;', 'count = 0; key++;'),
    ('count = 0;', 'count = 0; (key) += 2;'),
    ('count = 0;', 'count = 0; key <<= 2;'),
    ('count = 0;', 'count = 0; ++(key);'),
    ('count = 0;', 'count = 0; fill(& ( (key)));'),
    ('count = 0;', 'count = 0; { Custom key; key=3; }'),
    ('count = 0;', 'count = 0; { Custom (key); }'),
    ('count = 0;', 'count = 0; if(count) goto again;'),
    ('count = 0;', 'count = 0; goto other;'),
    ('again:', 'other: again:'),
    ('(key & 255)', '(key & 255U)'),
    ('(key & 255)', '(key & 0x80000000)'),
    ('(key & 255)', '(key & 0377)'),
    ('(key & 255)', '(key & read_mask())'),
    ('(key & 255)', '~key & 255'),
])
def test_uncertain_lifetime_type_or_expression_has_no_proposal(change):
    assert not repairs.variants(SOURCE.replace(*change), 'work')


def test_source_macros_and_existing_capture_are_not_rewritten():
    assert not repairs.variants('#define key hidden\n'+SOURCE, 'work')
    assert not repairs.variants(SOURCE.replace('count;', '__repair_argument_mask; int count;'), 'work')


def test_multiple_mask_uses_share_capture_and_other_units_stay_identical():
    source = 'int other(void) { return 7; }\n'+SOURCE.replace('use(node);', 'use(node, (key & 255));')
    [(_, candidate)] = repairs.variants(source, 'work')
    assert candidate.startswith('int other(void) { return 7; }')
    assert candidate.count('__repair_argument_mask') == 4
