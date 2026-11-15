"""Homemade C controls for selected no-argument contracts and copy removal."""
import json

import pytest

from decompnet.app import draft_repairs, local_experiments
from decomprepair import zero_argument_repairs as repair
from tests.test_claims import _game

SOURCE = 'void helper(int); int sample(int x){int y; y=x; helper(y); return y+1;}'
SELECTED = 'void helper(void){}'


def proposals(source=SOURCE, selected=SELECTED, header=''):
    return repair.variants(source, 'sample', header, selected)


def test_explicit_selected_empty_contract_and_measured_composition():
    result = proposals()
    assert result and result[0][0] == repair.COMBINED
    assert 'helper(void)' in result[0][1] and 'helper()' in result[0][1]
    assert 'return x+1' in result[0][1] and 'y=' not in result[0][1]
    assert any(f == repair.CALL and 'int y;' in s for f, s in result)
    assert any(f == repair.ALIAS and 'helper(x)' in s for f, s in result)
    assert not proposals(selected='')
    assert not proposals(result[0][1])


def test_closed_include_guard_does_not_make_unconditional_definition_ambiguous():
    assert proposals(selected='#ifndef DRAFT_TYPES\n#define DRAFT_TYPES\ntypedef int Other;\n#endif\n'+SELECTED)
    assert not proposals(selected='#if FEATURE\n'+SELECTED+'\n#endif')


@pytest.mark.parametrize('argument', ['x++', '++x', '(x=2)', 'effect()', '*ptr', 'x+1', '&x', '(int)x'])
def test_unknown_or_effectful_argument_is_never_discarded(argument):
    assert not proposals(SOURCE.replace('helper(y)', 'helper('+argument+')'))


@pytest.mark.parametrize('source,selected,header', [
    (SOURCE, 'void helper(){}', ''),
    (SOURCE, 'int helper(void){return 0;}', ''),
    (SOURCE, 'void helper(void){work();}', ''),
    (SOURCE, SELECTED+SELECTED, ''),
    (SOURCE, 'void helper(int x){}', ''),
    (SOURCE, SELECTED, 'void helper(int);'),
    (SOURCE+ ' void helper(int);', SELECTED, ''),
    (SOURCE.replace('void helper(int);', 'void helper(int){}'), SELECTED, ''),
    (SOURCE.replace('helper(y)', 'helper(y,y,y,y,y)'), SELECTED, ''),
    (SOURCE.replace('helper(y)', 'helper()'), SELECTED, ''),
    (SOURCE.replace('helper(y)', 'escape(helper);helper(y)'), SELECTED, ''),
    (SOURCE.replace('int x)', 'int x, int (*helper)(int))'), SELECTED, ''),
    (SOURCE.replace('int y;', 'volatile int y;'), SELECTED, ''),
    (SOURCE.replace('int y;', 'vint y;'), SELECTED, 'typedef volatile int vint;'),
    (SOURCE.replace('helper(y)', 'helper(secret)'), SELECTED, 'extern int secret;'),
    (SOURCE, SELECTED, '#define helper other'),
    (SOURCE, SELECTED, '#define y other'),
    (SOURCE, SELECTED, '#define void int'),
    ('#if FEATURE\n'+SOURCE+'\n#endif', SELECTED, ''),
])
def test_shared_uncertain_effectful_or_nonempty_contracts_stay_review_items(source, selected, header):
    assert not proposals(source, selected, header)


@pytest.mark.parametrize('body', [
    'int y; y=x; x++; helper(y); return y;',
    'int y; y=x; (x)=2; helper(y); return y;',
    'int y; y=x; x<<=1; helper(y); return y;',
    'int y; y=x; (x)>>=1; helper(y); return y;',
    'int y; y=x; ++(x); helper(y); return y;',
    'int y; y=x; escape(&x); helper(y); return y;',
    'int y; y=x; y+=2; helper(y); return y;',
    'int y; y=x; (y)=2; helper(y); return y;',
    'int y; y=x; y<<=1; helper(y); return y;',
    'int y; y=x; (y)>>=1; helper(y); return y;',
    'int y; y=x; escape(&(y)); helper(y); return y;',
    'int y; if(x) y=x; helper(y); return y;',
    'int y; y=x; {int x=8; helper(y);} return y;',
    'int y; y=x; {typedef int T; T x=8; helper(y);} return y;',
    'int y; y=x; {struct Other {int v;} x; helper(y);} return y;',
    'int y; y=x; for(;y<3;y++) helper(y); return y;',
])
def test_parameter_copy_elision_rejects_mutation_escape_shadow_or_missing_dominance(body):
    source = 'void helper(int); int sample(int x){'+body+'}'
    assert not repair.aliases(source, 'sample', '')


def test_assignment_conversion_is_not_erased():
    assert not repair.aliases(SOURCE.replace('int y;', 'short y;'), 'sample', '')
    assert not repair.aliases(SOURCE.replace('int y;', 'unsigned int y;'), 'sample', '')
    assert not repair.aliases(SOURCE.replace('int y;', 's32 y;'), 'sample', 'typedef short s32;')


def test_unchanged_values_can_cross_calls_loops_and_branches():
    source = ('void helper(int); int sample(int x,int z){int a;int b;a=x;b=z;'
              'seed();if(z){helper(a);}else{helper(b);}return a+b;}')
    result = proposals(source)
    assert result[0][0] == repair.COMBINED
    assert 'return x+z' in result[0][1] and 'helper()' in result[0][1]
    assert source.count('seed()') == result[0][1].count('seed()')
    assert len(result) <= repair.LIMIT


def test_comment_and_string_names_are_not_rewritten_and_field_names_are_preserved():
    source = SOURCE.replace('return y+1;', 'say("y"); /* y */ return y+1;')
    candidate = proposals(source)[0][1]
    assert 'say("y")' in candidate and '/* y */' in candidate


def test_same_line_and_physical_line_cleanup_have_bounded_alternatives():
    source = 'void helper(int);\nint sample(int x){\nint y;\ny=x;\nhelper(y);\nreturn y;\n}\n'
    result = proposals(source)
    composed = [s for f,s in result if f == repair.COMBINED]
    assert any(s.count('\n') == source.count('\n') for s in composed)
    assert any(s.count('\n') < source.count('\n') for s in composed)


def test_shared_generation_and_guarded_ranking():
    from decompnet.app.repair_ranking import guarded_first
    labels = {}
    result = draft_repairs.variants(SOURCE, 'sample', 'mips', callees=SELECTED, labels=labels, limit=None)
    assert all(f in draft_repairs.AGENT_FAMILIES for f in repair.FAMILIES)
    assert any(labels[s] == repair.COMBINED for s in result)
    assert labels[guarded_first(result, labels)[0]] in (repair.COMBINED, repair.CALL)


@pytest.mark.parametrize('mode', ['rom', 'fork'])
@pytest.mark.parametrize('status', ['matched', 'open'])
def test_local_pass_uses_only_selected_callee_contracts_in_both_modes(tmp_path, monkeypatch, mode, status):
    net, _ = _game(tmp_path, [('f', 10, 'open'), ('helper', 0, status)])
    agent, _ = net.local_agent('solver')
    source = SOURCE.replace('sample', 'f')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute("UPDATE functions SET best_source=? WHERE name='f'", (source,))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'", (SELECTED,))
    net.claim_row(agent, 'game', 'f')
    fn = net._function('f', 'game')
    monkeypatch.setattr(net.forks, 'permuter_header', lambda *args: '')
    from decompnet.app.agent_tokens import candidate
    _, revision = candidate(net, agent, 'game', 'f')
    pools = local_experiments.available(net, agent, 'game', fn, source, revision, list(repair.FAMILIES))
    assert bool(pools[repair.COMBINED]) == (status == 'matched')
    assert net._function('f','game')['best_source'] == source
    assert net._function('helper','game')['matched_source'] == SELECTED


def test_pinned_period_compiler_recovers_independent_no_argument_control(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    try:
        toolchains.require('gcc-2.7.2-kmc')
    except toolchains.ToolchainError:
        pytest.skip('period compiler not installed')
    flags = ['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']
    cc = compiler_config(tmp_path, {'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':flags}})
    source = 'void helper(int); int seed(void); int sample(int x){int y;int z;y=x;z=seed();helper(y);return y+z;}'
    reference = 'void helper(void); int seed(void); int sample(int x){int z;z=seed();helper();return x+z;}'
    def compiled(text):
        result = compile_source(cc, '', text)
        assert result.ok, result.log
        return extract_function(result.obj, 'sample')
    target, baseline = compiled(reference), compiled(source)
    candidates = [compiled(s) for f,s in proposals(source) if f == repair.COMBINED]
    assert baseline.code != target.code
    assert any(c.code == target.code and c.relocs == target.relocs for c in candidates)


@pytest.mark.parametrize('word', sorted(repair.TYPE_WORDS))
def test_type_keyword_macros_reject_signature_only_conversion(word):
    source = 'void helper(int); int sample(signed int x){int y;y=x;helper(y);return y<0;}'
    header = '#define '+word+' unsigned\n'
    assert repair.variants(source, 'sample', header, SELECTED) == []
    assert repair.aliases(source, 'sample', header) == []
