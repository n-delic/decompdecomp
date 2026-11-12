import pytest

from decompnet.app import draft_repairs, narrow_load_subtraction

SOURCE='''short f(unsigned short *left, unsigned short *right, int offset) {
 short d; unsigned short *p; unsigned short *q; int index;
 p=left; d = *p;
 index=offset+1; q=right+index;
 d -= *q; return d;
}'''


def generated(source=SOURCE,header=''):
    return [s for _,s in narrow_load_subtraction.variants(source,'f',header,'mips')]


def test_combines_reads_across_only_local_address_work():
    result=generated()
    assert len(result)==1
    assert 'index=offset+1; q=right+index;' in result[0]
    assert 'd = *p - *q;' in result[0]
    assert result[0].count('*p;')==1  # only the pointer declaration remains
    assert narrow_load_subtraction.FAMILY in draft_repairs.AGENT_FAMILIES


@pytest.mark.parametrize('gap',[
    '*right=4;', 'touch();', 'p=right;', 'd=3;', 'index=*right;',
    'if(offset) index=1;', 'index=helper(offset);', 'index=global;', 'index=d;',
])
def test_loads_never_cross_effects_unknown_operands_or_changed_capture(gap):
    assert not generated(SOURCE.replace('index=offset+1; q=right+index;',gap))


def test_escaped_local_can_alias_first_loaded_cell():
    assert not generated(SOURCE.replace('p=left;', 'p=(unsigned short *)&index;'))
    assert not generated(SOURCE.replace('p=left;', 'keep(&d); p=left;'))
    assert not generated(SOURCE.replace('d = *p;', 'object->d = *p;'))
    assert not generated(SOURCE.replace('d -= *q;', 'object->d -= *q;'))


@pytest.mark.parametrize('source',[
    SOURCE.replace('short d;', 'int d;'),
    SOURCE.replace('unsigned short *p;', 'volatile unsigned short *p;'),
    SOURCE.replace('int offset', 'V offset'),
    SOURCE.replace('q=right+index;', 'q=FETCH();'),
])
def test_unknown_width_qualified_alias_or_hidden_call_has_no_candidate(source):
    assert not generated(source,'typedef volatile int V;\n#define FETCH() mutate()')


def test_existing_wrapper_removal_keeps_scope_and_new_family_is_shared():
    from decomprepair import compiler_shape_repairs
    text='void f(void) { int x; x=1; do { int x; x=2; use(x); } while(0); use(x); }'
    choices=compiler_shape_repairs.variants(text,'f','mips')
    assert any(family=='one_shot_scope' and '{ int x; x=2; use(x); }' in candidate for family,candidate in choices)
