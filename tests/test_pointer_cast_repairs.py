import pytest

from decompnet.app import draft_repairs, local_experiments, pointer_cast_repairs

SOURCE = 'typedef struct { unsigned char *data; } Node; void f(Node *n) { int x; x=(int)n->data; use(x); x=(int)n->data; use(x); }'


def test_repeated_pointer_casts_can_share_an_inline_conversion():
    choices = pointer_cast_repairs.variants(SOURCE, 'f', 'mips')
    assert len(choices) == 1
    candidate = choices[0]
    assert candidate.startswith('static inline int __repair_pointer_bits(void *value)')
    assert candidate.count('__repair_pointer_bits(n->data)') == 2
    assert candidate.count('use(x);') == 2
    assert 'typedef struct { unsigned char *data; } Node;' in candidate


@pytest.mark.parametrize('source', [
    SOURCE.replace('unsigned char *data;', 'int data;'),
    SOURCE.replace('(int)n->data', '(int)next()'),
    SOURCE.replace('(int)n->data', '(int)n->data++'),
    SOURCE.replace('(int)n->data', '(int)n->data[0]'),
    SOURCE.replace('int x;', 'volatile int x;'),
    SOURCE.replace('int x;', 'int x;\n#define VALUE 1\n'),
    SOURCE.replace('int x;', 'int x; int __repair_pointer_bits;'),
])
def test_pointer_cast_helpers_require_unambiguous_pointer_operands(source):
    assert not pointer_cast_repairs.variants(source, 'f', 'mips')


def test_pointer_cast_helpers_preserve_neighbours_and_reach_both_searches():
    prefix = 'void other(void) { use(3); }\n'
    choices = pointer_cast_repairs.variants(prefix+SOURCE, 'f', 'mips')
    assert choices and prefix in choices[0]
    assert not pointer_cast_repairs.variants(SOURCE, 'f', 'aarch64')
    assert not pointer_cast_repairs.variants(SOURCE.replace('x=(int)n->data; use(x);', '', 1), 'f', 'mips')
    labels = {}
    choices = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels)
    assert any(labels[s] == 'pointer_cast_inline' for s in choices)
    assert 'pointer_cast_inline' in local_experiments.FAMILIES
