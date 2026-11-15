from decompnet.app import draft_repairs
from decomprepair import constant_pointer_repairs

SOURCE = '''typedef struct State { int count; } State;
extern State states[];
static inline State *state(void *input) {
    State *p;
    int zero = 0;
    if (input) p = states; else p = states + zero;
    return p;
}
void submit(int, int);
void update(void *entry) {
    State *saved;
    saved = state(entry);
    submit(5, (saved = state(entry))->count);
    saved->count = 7;
}
'''


def test_constant_pointer_capture_moves_to_after_call():
    variants=constant_pointer_repairs.variants(SOURCE,'update')
    assert len(variants)==1
    family,result=variants[0]
    assert family=='constant_pointer_capture_timing'
    assert 'submit(5, states[0].count);\n    saved = state(entry);' in result
    assert result.count('saved = state(entry);')==2
    assert 'saved->count = 7;' in result
    labels={}
    assert result in draft_repairs.variants(SOURCE,'update','mips','',[],labels,limit=None)
    assert labels[result]==family


def test_direct_helper_and_cast_arguments():
    source=SOURCE.replace('State *p;\n    int zero = 0;\n    if (input) p = states; else p = states + zero;\n    return p;', 'return states;')
    source=source.replace('submit(5,','submit(*((int *)entry),')
    assert constant_pointer_repairs.variants(source,'update')


def test_rejects_unproven_identity_and_side_effects():
    for old,new in [
        ('int zero = 0','int zero = 1'),
        ('states + zero','states + 1'),
        ('return p;','submit(1, 2); return p;'),
        ('extern State states[]','extern volatile State states[]'),
        ('submit(5,','submit(saved->count,'),
        ('submit(5,','submit(side_effect(),'),
        ('submit(5,','if (entry) submit(5,'),
        ('(saved = state(entry))->count','(saved = state(next()))->count'),
        ('    saved = state(entry);\n',''),
        ('    State *saved;','    volatile State *saved;'),
    ]:
        assert not constant_pointer_repairs.variants(SOURCE.replace(old,new),'update'), (old,new)


def test_rejects_aliases_macros_and_shadowed_or_escaped_locals():
    for source in [
        '#define states other\n'+SOURCE,
        '#define submit(a,b) consume(saved)\n'+SOURCE,
        '#define zero input\n'+SOURCE,
        '#define escaped (&saved)\n'+SOURCE.replace('submit(5,', 'escape(escaped); submit(5,'),
        SOURCE.replace('submit(5,', 'escape(&(saved)); submit(5,'),
        SOURCE.replace('State *saved;', 'State *saved; State states[4];'),
        SOURCE.replace('submit(5,', 'escape(&saved); submit(5,'),
        SOURCE.replace('typedef struct State { int count; } State;', 'typedef volatile struct State { int count; } State;'),
        SOURCE.replace('State *saved;', 'Other *saved;'),
        SOURCE.replace('extern State states[];', 'extern State states[];\ntypedef volatile State V;\ntypedef V Alias;').replace('State *saved;', 'Alias *saved;'),
    ]:
        assert not constant_pointer_repairs.variants(source,'update')


def test_rejects_helper_shadowing_and_unproven_argument_values():
    for source in [
        SOURCE.replace('void *input', 'void *states').replace('if (input)', 'if (states)'),
        SOURCE.replace('State *p;', 'Other *p;'),
        SOURCE.replace('int zero = 0;', 'int p = 0;').replace('states + zero', 'states + p'),
        SOURCE.replace('void *input', 'void *p').replace('if (input)', 'if (p)'),
        SOURCE.replace('void update(void *entry)', 'void update(int entry)'),
        SOURCE.replace('void update(void *entry)', 'extern void *entry;\nvoid update(void)'),
        SOURCE.replace('void update(void *entry)', 'typedef void *Pointer;\nextern volatile Pointer entry;\nvoid update(void)'),
        SOURCE.replace('submit(5,', 'escape(&entry); submit(5,'),
    ]:
        assert not constant_pointer_repairs.variants(source,'update')
    direct=SOURCE.replace('State *p;\n    int zero = 0;\n    if (input) p = states; else p = states + zero;\n    return p;', 'return input;').replace('void *input','void *states').replace('return input;', 'return states;')
    assert not constant_pointer_repairs.variants(direct,'update')


def test_rejects_shadowed_bindings_and_conditional_proofs():
    helper=SOURCE[SOURCE.index('static inline'):SOURCE.index('void submit')]
    for source in [
        SOURCE.replace('submit(5,', '{ State *saved; submit(5,').replace('saved->count = 7;', 'saved->count = 7; }'),
        SOURCE.replace('submit(5,', '{ int entry; submit(5,').replace('saved->count = 7;', 'saved->count = 7; }'),
        SOURCE.replace('submit(5,', '{ State *other, *saved; submit(5,').replace('saved->count = 7;', 'saved->count = 7; }'),
        SOURCE.replace(helper, '#if 0\n'+helper+'#else\n'+helper.replace('return p;', 'submit(1, 2); return p;')+'#endif\n'),
        SOURCE.replace('extern State states[];', '#if FLAG\nextern State states[];\n#endif'),
        SOURCE.replace('State *saved;', '#if FLAG\nState *saved;\n#endif'),
    ]:
        assert not constant_pointer_repairs.variants(source,'update')


def test_unrelated_conditional_region_does_not_block_repair():
    assert constant_pointer_repairs.variants('#if FLAG\nvoid unrelated(State *value);\n#endif\n'+SOURCE,'update')
    assert constant_pointer_repairs.variants(SOURCE.replace('    submit(5,', '#if FLAG\n    unrelated();\n#endif\n    submit(5,'),'update')
