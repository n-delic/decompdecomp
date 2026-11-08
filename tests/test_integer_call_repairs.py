"""Synthetic counterexamples and real compiler checks for bounded integer/call layout hypotheses."""




import pytest

from decompnet.app import branch_layout_repairs, integer_lifetime_repairs, local_widening_repairs

WIDE = 'int f(int offset) { signed char coordinate; coordinate = 320-offset; return coordinate; }'


PROTO = 'int draw(int,short);int f(int y){return draw(7,y-2);}'


MERGE = 'int f(int x){int value;int value_2;value=x+3;use(value);value_2=x+5;return value_2;}'


BRANCH = 'int draw(int);void f(int flag){int x;if(flag){x=3;}else{x=7;}draw(x);}'


def test_widening_requires_arithmetic_or_a_wider_conversion_and_keeps_evaluations():
    choices = local_widening_repairs.variants(WIDE, 'f')
    assert len(choices) == 3 and any('int coordinate;' in text for text in choices)
    assert all('coordinate = 320-offset;' in text and 'return coordinate;' in text for text in choices)
    assert local_widening_repairs.variants('int f(int x){u8 index;index=x/10;return (s16)index;}', 'f')
    assert local_widening_repairs.variants('int f(int x){u8 index;index=x/10;return (index<<16)>>15;}', 'f')


@pytest.mark.parametrize('source', [
    WIDE.replace('320-offset', '7-offset'),
    WIDE.replace('320-offset', 'read8000()'),
    WIDE.replace('320-offset', '*(unsigned char *)(base+320)'),
    WIDE.replace('signed char coordinate;', 'volatile signed char coordinate;'),
    WIDE.replace('coordinate =', 'escape(&coordinate); coordinate ='),
    WIDE.replace('return coordinate;', '{int coordinate;use(coordinate);}return coordinate;'),
    WIDE.replace('coordinate =', '#define VALUE 320\ncoordinate ='),
])
def test_widening_skips_offsets_qualified_objects_escapes_shadows_and_macros(source):
    assert not local_widening_repairs.variants(source, 'f')


def test_integer_lifetimes_split_independent_segments_and_merge_only_disjoint_same_types():
    source = 'int f(int x){int value;value=x+3;use(value);value=x+7;use(value);value=x+9;return value;}'
    splits = [text for family, text in integer_lifetime_repairs.variants(source, 'f') if family == 'split_integer_lifetime']
    assert splits and 'value=x+3;use(value)' in splits[0]
    assert '__repair_value_1=x+7;use(__repair_value_1)' in splits[0]
    assert 'value=x+9;return value' in splits[0]
    merges = [text for family, text in integer_lifetime_repairs.variants(MERGE, 'f') if family == 'merge_integer_lifetimes']
    assert merges and 'int value_2;' not in merges[0] and 'value=x+5;return value;' in merges[0]
    assert not any(family == 'merge_integer_lifetimes' for family, _ in integer_lifetime_repairs.variants(MERGE.replace('int value_2;', 'short value_2;'), 'f'))


@pytest.mark.parametrize('source', [
    MERGE.replace('return value_2;', 'return value_2+value;'),
    MERGE.replace('use(value);', 'escape(&value);'),
    MERGE.replace('value=x+3;', 'do{value=x+3;').replace('return value_2;', 'use(value_2);}while(x--);return 0;'),
    MERGE.replace('value=x+3;', 'again:value=x+3;').replace('return value_2;', 'if(x--)goto again;return value_2;'),
    MERGE.replace('return value_2;', '{int value;use(value);}return value_2;'),
])
def test_integer_merge_rejects_overlapping_escaped_looped_jump_crossed_and_shadowed_values(source):
    assert not any(family == 'merge_integer_lifetimes' for family, _ in integer_lifetime_repairs.variants(source, 'f'))


def test_integer_split_rejects_a_jump_that_can_bypass_the_new_definition():
    source = 'int f(int x){int value;value=x;use(value);if(x)goto later;value=x+3;later:return value;}'
    assert not any(family == 'split_integer_lifetime' for family, _ in integer_lifetime_repairs.variants(source, 'f'))


def test_lifetime_edits_do_not_rename_fields_or_labels_sharing_a_local_name():
    source = MERGE.replace('return value_2;', 'obj.value_2=9;return value_2;')
    choices = [text for family, text in integer_lifetime_repairs.variants(source, 'f') if family == 'merge_integer_lifetimes']
    assert choices and all('obj.value_2=9;' in text for text in choices)


def test_branch_call_keeps_assignments_and_executes_one_copy_per_path():
    candidates = [text for family, text in branch_layout_repairs.variants(BRANCH, 'f') if family == 'branch_call']
    assert candidates and candidates[0].count('draw(x);') == 2
    assert 'x=3;' in candidates[0] and 'x=7;' in candidates[0]
    assert not any(family == 'branch_call' for family, _ in branch_layout_repairs.variants(BRANCH.replace('x=3;', 'int x=3;'), 'f'))
    assert not any(family == 'branch_call' for family, _ in branch_layout_repairs.variants(BRANCH.replace('x=3;', 'return;'), 'f'))


def test_goto_leaf_copies_its_terminal_transfer_and_rejects_fallthrough_or_shadowing():
    source = 'void f(int x){if(x)goto leaf;work();goto done;leaf:draw(x);goto done;done:return;}'
    choices = [text for family, text in branch_layout_repairs.variants(source, 'f') if family == 'goto_leaf']
    assert choices and 'if(x){draw(x);goto done;}' in choices[0]
    fallthrough = source.replace('draw(x);goto done;', 'draw(x);x++;goto done;')
    assert not any(family == 'goto_leaf' for family, _ in branch_layout_repairs.variants(fallthrough, 'f'))
    scoped = source.replace('leaf:draw(x);goto done;', '{int x;leaf:draw(x);goto done;}')
    assert not any(family == 'goto_leaf' for family, _ in branch_layout_repairs.variants(scoped, 'f'))


def test_goto_leaf_can_exit_an_if_arm_with_the_same_enclosing_switch_break():
    source = 'void f(int mode,int flag){switch(mode){case 1:if(flag){leaf:draw(mode);}else{other();}break;case 2:goto leaf;}}'
    choices = [text for family, text in branch_layout_repairs.variants(source, 'f') if family == 'goto_leaf']
    assert choices and 'case 2:{draw(mode);break;}' in choices[0]
    nested_loop = source.replace('case 2:goto leaf;', 'case 2:while(flag){goto leaf;}')
    assert not any(family == 'goto_leaf' for family, _ in branch_layout_repairs.variants(nested_loop, 'f'))
    unbraced = source.replace('case 2:goto leaf;', 'case 2:while(flag)goto leaf;')
    assert not any(family == 'goto_leaf' for family, _ in branch_layout_repairs.variants(unbraced, 'f'))
    preceding_loop = source.replace('switch(mode)', 'do{work();}while(flag);switch(mode)')
    assert any(family == 'goto_leaf' for family, _ in branch_layout_repairs.variants(preceding_loop, 'f'))


def test_switch_reordering_keeps_grouped_cases_and_adds_a_missing_noop_zero():
    source = 'void f(int x){switch(x){case 1:case 2:draw(3);break;case 5:draw(7);break;}}'
    choices = [text for family, text in branch_layout_repairs.variants(source, 'f') if family == 'switch_layout']
    assert choices and any('case 0: break;' in text for text in choices)
    assert all('case 1:case 2:draw(3);break;' in text and 'case 5:draw(7);break;' in text for text in choices)
    default = source.replace('case 5:', 'default:')
    assert not any('case 0: break;' in text for _, text in branch_layout_repairs.variants(default, 'f'))
    goto = 'void f(int x){switch(x){case 1:leaf:draw(3);break;case 5:goto leaf;}}'
    assert any('case 5:goto leaf;case 1:leaf:draw(3);break;' in text for _, text in branch_layout_repairs.variants(goto, 'f'))


@pytest.mark.parametrize('source', [
    'void f(int x){switch(x){case 1:draw(3);case 5:draw(7);break;}}',
    'void f(int x){switch(x){case 1:if(x)break;case 5:draw(7);break;}}',
    'int f(int x){switch(x){case 1:if(x)return 3;case 5:return 7;}}',
    'void f(int x){switch(x){case 1:int y=3;draw(y);break;case 5:draw(7);break;}}',
    'void f(int x){switch(x){case VALUE:draw(3);break;case 5:draw(7);break;}}',
])
def test_switch_reordering_rejects_fallthrough_conditional_terminals_declarations_and_symbolic_labels(source):
    # A no-op zero insertion does not move the existing fallthrough/declarations and is independently safe.
    assert not any(family == 'switch_layout' and 'case 0: break;' not in text
                   for family, text in branch_layout_repairs.variants(source, 'f'))
