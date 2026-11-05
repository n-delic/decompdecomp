import shutil
import subprocess

import pytest

from decompnet.app import constant_store_scope, draft_repairs

SOURCE = '''typedef unsigned int u32;
typedef struct { u32 first, second, third; } Record;
void update(Record *out, u32 value) {
    u32 consumed;
    u32 current;
    consumed = value;
    out->first = consumed;
    current = value + 1;
    out->second = current;
    current = 48;
    out->third = current;
}
'''


def test_consumed_same_type_scalar_reaches_first_page():
    choices = constant_store_scope.variants(SOURCE, 'update')
    assert len(choices) == 1
    assert 'do {consumed=48;out->third=consumed;}while(0);' in choices[0][1]
    labels = {}
    page = draft_repairs.variants(SOURCE, 'update', 'mips', labels=labels, limit=96)
    assert any(labels[c] == constant_store_scope.FAMILY for c in page)


@pytest.mark.parametrize('before,after', [
    ('out->third = current;', 'out->third = current; out->first = current;'),
    ('out->third = current;', 'out->third = current; out->first = consumed;'),
    ('u32 consumed;', 'unsigned char consumed;'),
    ('u32 consumed;', 'volatile u32 consumed;'),
    ('consumed = value;', 'escape(&(consumed)); consumed = value;'),
    ('current = value + 1;', 'escape(&(current)); current = value + 1;'),
    ('current = 48;', 'if (value) current = 48;'),
    ('current = 48;', 'label: current = 48;'),
    ('out->third = current;', 'out->third = current; if (value) goto again;'),
])
def test_live_escaped_qualified_or_unbraced_rewrites_are_rejected(before, after):
    source = SOURCE.replace(before, after)
    if 'goto again' in source:
        source = source.replace('out->first = consumed;', 'again: out->first = consumed;')
    assert not constant_store_scope.variants(source, 'update')


@pytest.mark.parametrize('header', [
    '#define consumed changed', '#define current changed', '#define third changed',
    '#define u32 unsigned int', 'typedef volatile unsigned int u32;',
    '#define unrelated(x) \\\n (x)', '#define SITE __LINE__',
])
def test_effective_header_aliases_and_directives_are_guarded(header):
    assert not constant_store_scope.variants(SOURCE, 'update', header)


def test_compatible_local_must_be_in_scope_and_previously_used():
    source = SOURCE.replace('u32 consumed;', '{ u32 consumed;').replace(
        'out->first = consumed;', 'out->first = consumed; }')
    assert not constant_store_scope.variants(source, 'update')
    source = SOURCE.replace('consumed = value;', '').replace('out->first = consumed;', '')
    assert not constant_store_scope.variants(source, 'update')


def test_c90_boundary_values_preserve_stores(tmp_path):
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('no host C compiler')
    candidate = constant_store_scope.variants(SOURCE, 'update')[0][1].replace('void update(', 'void repaired(')
    driver = '''
int main(void) {
    u32 values[] = {0, 1, 47, 48, 0x7fffffffU, 0x80000000U, 0xffffffffU};
    unsigned int i;
    for (i=0; i<sizeof(values)/sizeof(values[0]); ++i) {
        Record a, b;
        update(&a, values[i]); repaired(&b, values[i]);
        if(a.first!=b.first || a.second!=b.second || a.third!=b.third) return 1;
    }
    return 0;
}
'''
    path = tmp_path/'check.c'
    # Share declarations rather than defining the record twice.
    candidate = candidate[candidate.index('void repaired('):]
    path.write_text(SOURCE+candidate+driver)
    binary = tmp_path/'check'
    subprocess.run([compiler, '-std=c90', '-pedantic-errors', '-O2', str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
