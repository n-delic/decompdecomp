"""Synthetic counterexamples and real compiler checks for bounded integer/call layout hypotheses."""
import json

import pytest

from decompnet.app import (
    branch_layout_repairs,
    callee_prototype_repairs,
    draft_repairs,
    integer_lifetime_repairs,
    local_experiments,
    local_widening_repairs,
)

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


def test_callee_repair_changes_only_the_owned_prototype_and_not_the_calls():
    choices = callee_prototype_repairs.variants(PROTO, 'f')
    assert choices == [('callee_parameter_width', 'int draw(int,int);int f(int y){return draw(7,y-2);}')]
    source = 'int decode(int);int f(int bits){int index;index=decode(bits);return index&0xff;}'
    returns = [text for family, text in callee_prototype_repairs.variants(source, 'f') if family == 'callee_return_width']
    assert len(returns) == 2 and all('index=decode(bits);return index&0xff;' in text for text in returns)
    assert any('unsigned char decode(int)' in text for text in returns)


@pytest.mark.parametrize('source', [
    PROTO.replace('int draw(int,short);', 'int draw(int,short);int draw(int,short);'),
    PROTO.replace('int draw(int,short);', 'int draw(int,short,...);'),
    PROTO.replace('int draw(int,short);', 'int (*draw)(int,short);'),
    PROTO.replace('int draw(int,short);', '#if EXTERNAL\nint draw(int,short);\n#endif\n'),
    PROTO.replace('int f(int y){', 'int f(int y){int draw;'),
    PROTO.replace('int f(int y)', 'int f(int y, int draw)'),
    PROTO.replace('int draw(int,short);', 'int draw(int x,short y){return x+y;}'),
])
def test_callee_repair_leaves_ambiguous_variadic_indirect_shadowed_and_defined_callees_alone(source):
    assert not callee_prototype_repairs.variants(source, 'f')


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


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'arm', 'thumb'])
def test_new_hypotheses_reach_shared_draft_and_manual_family_searches(arch):
    examples = [('local_widening', WIDE), ('callee_parameter_width', PROTO), ('merge_integer_lifetimes', MERGE), ('branch_call', BRANCH)]
    for expected, source in examples:
        prefix = 'void other(void){int untouched;use(untouched);}\n'
        labels = {}
        choices = draft_repairs.variants(prefix+source, 'f', arch, labels=labels, limit=None)
        assert any(labels[text] == expected for text in choices), (expected, labels)
        assert all(text.startswith(prefix) for text in choices if labels[text] == expected)
        assert expected in local_experiments.FAMILIES


def test_real_mips_compiler_confirms_widening_and_callee_repair_can_recover_exact_objects(tmp_path):
    from decompnet import scoring, toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned gcc-2.7.2-kmc')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc'}})
    examples = [(WIDE, 'int f(int offset){return 320-offset;}', local_widening_repairs.variants(WIDE, 'f')),
                (PROTO, 'int draw(int,int);int f(int y){return draw(7,y-2);}', [text for _, text in callee_prototype_repairs.variants(PROTO, 'f')])]
    for original, target, candidates in examples:
        expected = compile_source(cfg, '', target)
        before = compile_source(cfg, '', original)
        assert expected.ok and before.ok
        obj = extract_function(expected.obj, 'f')
        assert scoring.diff(obj, extract_function(before.obj, 'f')).score > 0
        scores = []
        for candidate in candidates:
            compiled = compile_source(cfg, '', candidate)
            assert compiled.ok, compiled.log
            scores.append(scoring.diff(obj, extract_function(compiled.obj, 'f')).score)
        assert 0 in scores


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_manual_new_families_share_one_budget_revision_and_normal_compile_path(tmp_path, monkeypatch, mode):
    from decompnet.app import agent_tokens
    from tests.test_claims import _game
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('a')
    source = 'int draw(short);int f(int x){signed char coordinate;coordinate=320-x;return draw(coordinate);}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': mode, 'compiler': {'toolchain': 'example', 'flags': []}}),))
        db.execute('UPDATE functions SET best_source=?', (source,))
    net.claim_row(agent, 'game', 'f')
    fn = net._function('f', 'game')
    net._record(fn, agent, 10, source, 1)
    seen = []
    def compile(agent, function, text, slug):
        seen.append(text)
        net._record(fn, agent, 20, text, 1)
        return 'score 20'
    monkeypatch.setattr(net, 'compile_and_diff', compile)
    # This fixture tests revision/budget routing, not an imported project's build preflight.
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight', lambda *args: None)
    _, revision = agent_tokens.candidate(net, agent, 'game', 'f')
    result = local_experiments.run(net, agent, 'game', 'f', revision,
                                   families=['local_widening', 'callee_parameter_width'], max_trials=2)
    assert len(seen) == 2 and '1: local_widening, 2: callee_parameter_width' in result
    assert 'starting candidate retained' in result
    assert agent_tokens.candidate(net, agent, 'game', 'f')[0] == source
