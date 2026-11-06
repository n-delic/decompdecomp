"""Conservative reverse phases and pure local capture; no private source fixtures."""
from decompnet.app import draft_repairs, local_flow, phase_union_repairs
from decompnet.app.integer_repair_support import locals_for, view

FLAGS = ('-O2', '-g3', '-mgp32')
SOURCE = '''
struct Point { int x, y, z; };
void observe(volatile int *);
void consume(int *, int *);
void work(struct Point *points, int input)
{
    int first;
    int second;
    int temporary;
    int output[3];
    volatile int decision;
    first = input;
    second = input + 1;
    observe(&decision);
    temporary = first;
    if (decision < 0) {
        first = second;
        second = temporary;
    }
    output[0] = points[first].x;
    output[1] = points[first].y;
    output[2] = points[first].z;
    consume(output, output);
    consume(output, output);
}
'''


def choices(source=SOURCE, flags=FLAGS, header=''):
    return phase_union_repairs.array_variants(source, 'work', 'mips', flags, header)


def test_assigns_each_active_member_and_captures_unescaped_copy_before_call():
    variants = choices()
    assert len(variants) == 2
    plain, captured = [s for _, s in variants]
    assert 'union { int value; int *pointer; } first;' in plain
    assert 'output[0] = points[first.value].x;' in plain
    assert 'first.pointer = output;' in plain
    assert plain.count('consume(first.pointer,first.pointer);') == 2
    assert 'first.value' not in plain.split('first.pointer = output;', 1)[1]
    assert 'observe(&decision);\n    temporary = first.value;' in plain
    assert 'temporary = first.value;\n    observe(&decision);' in captured
    assert captured.count('observe(&decision);') == 1
    assert captured.count('decision < 0') == 1


def test_first_page_exposes_new_family_with_unrelated_volatile_unchanged():
    labels = {}
    variants = draft_repairs.variants(SOURCE, 'work', 'mips', '', FLAGS, labels, limit=4)
    assert any(labels[s] == 'integer_array_pointer_phase' for s in variants)
    assert all('volatile int decision;' in s for s in variants)


def test_requires_explicit_pointer_width():
    assert not choices(flags=('-O2', '-g3'))
    assert not choices(flags=FLAGS + ('-mgp64',))


def test_rejects_escaped_or_not_definitely_initialized_index():
    assert not choices(SOURCE.replace('observe(&decision);', 'observe(&decision);\n    consume(&first, &first);'))
    assert not choices(SOURCE.replace('observe(&decision);', 'observe(&decision);\n    consume(&(first), &(first));'))
    assert not choices(SOURCE.replace('    first = input;\n', ''))


def test_rejects_qualified_and_shadowed_arrays_and_indices():
    assert not choices(SOURCE.replace('int output[3];', 'volatile int output[3];'))
    assert not choices(SOURCE.replace('int first;', 'volatile int first;'))
    assert not choices(SOURCE.replace('    observe(&decision);', '    { int output[3]; consume(output, output); }\n    observe(&decision);'))
    assert not choices(SOURCE.replace('int output[3];', 'Word output[3];'), header='typedef volatile int Word;')


def test_rejects_branch_entry_and_index_reuse_after_pointer_phase():
    assert not choices(SOURCE.replace('    consume(output, output);', '    if (input) { consume(output, output); }'))
    assert not choices(SOURCE.replace('\n}', '\n    first = input;\n}'))


def test_ignores_comments_but_rejects_macros_and_nonlocal_transfers():
    assert choices(SOURCE.replace('int output[3];', 'int output[3]; /* int output[99]; */'))
    assert not choices(header='#define consume(a,b) hidden(a,b)')
    assert not choices(header='#define hidden(output) #output')
    assert not choices(header='#define hidden(first) first ## suffix')
    assert not choices(header='#define pointer unexpected')
    assert not choices(header='#define value(x) unexpected(x)')
    assert not choices(SOURCE.replace('    first = input;', '    goto later; later: first = input;'))


def test_copy_capture_rejects_call_reading_destination():
    source = SOURCE.replace('observe(&decision);', 'observe(temporary, &decision);')
    variants = choices(source)
    assert len(variants) == 1
    assert 'observe(temporary, &decision);\n    temporary = first.value;' in variants[0][1]


def test_copy_capture_rejects_argument_writes_and_control_keywords():
    for call in ('observe(first++, &decision);', 'observe(--first, &decision);',
                 'observe(first = input, &decision);', 'observe(first += input, &decision);',
                 'observe(first >>= 1, &decision);', 'observe(first <<= 1, &decision);',
                 'while (first);'):
        variants = choices(SOURCE.replace('observe(&decision);', call))
        assert len(variants) == 1
        assert 'temporary = first.value;' in variants[0][1]


def test_volatile_cfg_opt_in_requires_explicit_ordinary_locals():
    assert local_flow.analyze(SOURCE, 'work') is None
    assert local_flow.analyze(SOURCE, 'work', allow_unrelated_volatile=True) is None
    declarations = locals_for(view(SOURCE, 'work'))
    assert local_flow.analyze(SOURCE, 'work', declarations=declarations, allow_unrelated_volatile=True)
    qualified = SOURCE.replace('int first;', 'volatile int first;')
    # An old declaration span is not permission to track a qualified object.
    assert local_flow.analyze(qualified, 'work', declarations=declarations, allow_unrelated_volatile=True) is None
