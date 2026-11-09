"""Targeted candidates and their independent, cached background budget."""

import re
import threading

import pytest

from decompnet.app.draft_repairs import variants
from decompnet.app.permute import State
from tests.test_permute import AGENT, FakeNet, ProjectStub, ScriptedPermuter


def test_address_conversion_and_commutation_are_combined():
    src = 'int f(int i) { return *(int *)((u8 *)table + i); }'
    assert 'int f(int i) { return *(int *)(i + (int)table); }' in variants(src, 'f', 'mips')
    assert not variants(src, 'f', 'x86_64')
    assert not variants(src, 'f', 'unknown')


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'x86_64', 'arm', 'thumb', 'unknown'])
def test_early_return_is_shared_and_keeps_expression(arch):
    src = 'int f(int x) { int result; result = 0; if (x >= 0) { result = (int)root((float)x); } return result; }'
    outputs = variants(src, 'f', arch)
    assert any('if (x < 0) return 0;' in out and 'return (int)root((float)x);' in out for out in outputs)


def test_nested_call_argument_boundary_and_opaque_text():
    src = 'int f(int x) { /* other(x, 123) */ return send(inner(x, 2), 0x40); }'
    out = variants(src, 'f', 'mips')
    assert 'return send(inner(x, 2));' in out[0]
    assert '/* other(x, 123) */' in out[0]
    assert all('other(x, 123)' in v for v in out)
    assert not variants('int f(int x) { return send(x, side_effect()); }', 'f', 'mips')
    assert not variants('int f(int x) { return send(x, "abc"); }', 'f', 'mips')


def test_only_requested_definition_and_no_directives():
    src = 'int other(int x) { return send(x, 7); }\nint f(int x) { return x; }'
    assert not variants(src, 'f', 'mips')
    assert not variants('int f(int x) {\n#define GO send(x, 7)\nreturn GO; }', 'f', 'mips')
    assert not variants('int f(int x);', 'f', 'mips')
    # Extra statements and references to the accumulator must not be discarded.
    for expr in ('result + x', 'x'):
        src = f'int f(int x) {{ int result; result=0; side(); if(x>=0){{result={expr};}} return result; }}'
        assert all('side();' in out and (f'result={expr};' in out or (expr=='result + x' and ('result = result; result += x;' in out or 'result= (x) + (result);' in out))) for out in variants(src, 'f', 'mips'))


def setup_repair_net(count=1):
    net = FakeNet()
    with net.store.tx() as db:
        db.execute("DELETE FROM functions")
        for i in range(1, count + 1):
            db.execute('INSERT INTO functions(id,project_id,name,n_insns,best_score,best_source) VALUES(?,1,?,10,200,?)',
                       (i, f'f{i}', f'int f{i}(int x) {{ return send(x, 7); }}'))
    return net


def test_outside_cutoff_cached_and_does_not_spend_random_turns():
    net = setup_repair_net()
    perm = ScriptedPermuter(net, {})
    proj = net.project_row('game')
    assert perm.candidates(proj) == []
    state = State()
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == state.repairs == 1
    assert net.function()['permuted'] == 0
    assert not net.store.all('SELECT * FROM claims')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 1
    # Changes in the actual shared header invalidate the cache.
    net._context = lambda *args: {'header': 'typedef int word;', 'version': 2}
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 2


def test_rotation_claims_stop_and_exact_match_counts():
    net = setup_repair_net(21)
    perm = ScriptedPermuter(net, {'int f21(int x) { return send(x); }': 0})
    proj = net.project_row('game')
    state = State()
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(1,1,0,9999999999)')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 20
    assert all('int f1(' not in s for s in perm.judged)
    assert state.matched == 1
    state.stop.set()
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 20


def test_project_mode_uses_project_header_and_judge():
    net = setup_repair_net()
    net.fork_slug = lambda slug: 'fork'

    class Fork(ProjectStub):
        def permuter_header(self, slug, function):
            assert (slug, function) == ('fork', 'f1')
            return 'typedef int word;'

    net.forks = Fork()
    perm = ScriptedPermuter(net, {})
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert len(perm.judged) == 1


def test_compile_budget_rotates_to_later_functions():
    net = setup_repair_net(25)
    with net.store.tx() as db:
        for i in range(1, 26):
            db.execute('UPDATE functions SET best_source=? WHERE id=?',
                       (f'int f{i}(int x) {{ send(x, 7); return send(x, 9); }}', i))
    perm = ScriptedPermuter(net, {})
    state = State()
    proj = net.project_row('game')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 32
    perm._repair_round(AGENT, proj, state)
    # Family count can grow; the next round must still start beyond the last visited function.
    last = int(re.search(r'int f(\d+)\(',perm.judged[31])[1])
    assert perm.judged[32].startswith(f'int f{last+1}(')


def test_failed_judge_releases_claim_without_caching():
    net = setup_repair_net()
    perm = ScriptedPermuter(net, {})

    def fail(*args):
        raise RuntimeError('compiler interrupted')

    perm._score = fail
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert not net.store.all('SELECT * FROM claims')
    assert not net.store.all('SELECT * FROM reuse_trials')


def test_run_again_does_not_start_random_permutation_or_remote_room_work():
    net = setup_repair_net()
    net.local_agent = lambda name: (AGENT, None)
    perm = ScriptedPermuter(net, {})
    state = perm.repair_pass('game', threading.Event())
    assert state.repairs == 1
    assert net.function()['permuted'] == 0
    net.links = {'game': object()}
    state = perm.repair_pass('game', threading.Event())
    assert state.repairs == 0


@pytest.mark.parametrize('pointer', ['obj->base', 'obj.data.base', 'obj -> link -> base'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_member_address_cast_with_optional_parentheses(pointer, wrapped):
    cast = f'(u8 *) {pointer}'
    if wrapped:
        cast = f'({cast})'
    src = f'int f(int i) {{ return *(int *)({cast} + i); }}'
    assert f'int f(int i) {{ return *(int *)(i + (int){pointer}); }}' in variants(src, 'f', 'mips')
    assert not variants(src, 'f', 'x86_64')


def test_member_rule_excludes_calls_and_increments():
    for expr in ['get()->base', 'obj[i++].base', 'obj->base++']:
        src = f'int f(int i) {{ return *(int *)(((u8 *) {expr}) + i); }}'
        assert all('side();' in out and (f'result={expr};' in out or (expr=='result + x' and ('result = result; result += x;' in out or 'result= (x) + (result);' in out))) for out in variants(src, 'f', 'mips'))


@pytest.mark.parametrize('imported', [False,True])
def test_scaled_pointer_candidate_reaches_the_normal_mode_judge(imported):
    net=setup_repair_net()
    source='void f1(){q=(u16 *)a+i;}'
    expected='void f1(){q=(u16 *)(i * 2 + (int)a);}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    if imported:
        net.fork_slug=lambda slug:'fork'
        class Fork(ProjectStub):
            def permuter_header(self,slug,function):
                assert (slug,function)==('fork','f1')
                return 'typedef unsigned short u16; typedef int s32;'
        net.forks=Fork()
    perm=ScriptedPermuter(net,{expected:0});state=State()
    perm._repair_round(AGENT,net.project_row('game'),state)
    assert expected in perm.judged and state.matched==1
    assert not net.store.all('SELECT * FROM claims')


@pytest.mark.parametrize('imported', [False,True])
def test_linked_byte_chain_reaches_normal_judge_without_accepting_intermediate_steps(imported):
    from decompnet.app import narrow_chains
    source='void f1(){s32 wide;s32 copy;u8 narrow;wide=read();copy=wide;copy=(u16)copy&0xFF;narrow=wide&0xFF;if(narrow)return;send(wide&0xFF);}'
    expected=narrow_chains.variants(source,'f1')[0]
    net=setup_repair_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    if imported:
        net.fork_slug=lambda slug:'fork'
        class Fork(ProjectStub):
            def permuter_header(self,slug,function):
                assert (slug,function)==('fork','f1')
                return 'typedef unsigned char u8; typedef unsigned short u16; typedef int s32;'
        net.forks=Fork()
    perm=ScriptedPermuter(net,{expected:0});state=State()
    perm._repair_round(AGENT,net.project_row('game'),state)
    assert expected in perm.judged and state.matched==1
    assert not net.store.all('SELECT * FROM claims')


@pytest.mark.parametrize('imported',[False,True])
def test_inline_expression_candidate_is_kept_only_by_the_normal_mode_judge(imported):
    from decompnet.app import expression_repairs
    source='void f1(){int x;int a;int b;x=a+b;}'
    expected=next(c for c in expression_repairs.variants(source,'f1','mips') if c.startswith('static inline'))
    net=setup_repair_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    if imported:
        net.fork_slug=lambda slug:'fork'
        class Fork(ProjectStub):
            def permuter_header(self,slug,function):
                assert (slug,function)==('fork','f1')
                return ''
        net.forks=Fork()
    perm=ScriptedPermuter(net,{expected:0});state=State()
    perm._repair_round(AGENT,net.project_row('game'),state)
    assert expected in perm.judged and state.matched==1
    assert not net.store.all('SELECT * FROM claims')


@pytest.mark.parametrize('imported',[False,True])
def test_spare_pointer_staging_uses_the_normal_mode_judge(imported):
    from decompnet.app import temporary_repairs
    source='void f1(){void *spare;u8 *p;p=*((void **)(base+4));send(p);spare=next();use(spare);}'
    expected=temporary_repairs.variants(source,'f1')[0]
    net=setup_repair_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    if imported:
        net.fork_slug=lambda slug:'fork'
        class Fork(ProjectStub):
            def permuter_header(self,slug,function):
                assert (slug,function)==('fork','f1')
                return ''
        net.forks=Fork()
    perm=ScriptedPermuter(net,{expected:0});state=State()
    perm._repair_round(AGENT,net.project_row('game'),state)
    assert expected in perm.judged and state.matched==1
    assert not net.store.all('SELECT * FROM claims')


def test_a_cached_success_is_rejudged_when_a_function_is_reopened():
    net=setup_repair_net()
    winner='int f1(int x) { return send(x); }'
    perm=ScriptedPermuter(net,{winner:0})
    perm._repair_round(AGENT,net.project_row('game'),State())
    assert winner in perm.judged
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',best_score=200,best_source=?",('int f1(int x) { return send(x, 7); }',))
    before=len(perm.judged)
    perm._repair_round(AGENT,net.project_row('game'),State())
    assert perm.judged[before:]==[winner]


def test_one_unavailable_project_header_does_not_stop_the_remaining_repairs():
    from decompnet.forks.project import ForkError
    net=setup_repair_net(2)
    net.fork_slug=lambda _: 'fork'
    class Headers(ProjectStub):
        def permuter_header(self,slug,name):
            if name=='f1':
                raise ForkError('Build this unit first.')
            return ''
    net.forks=Headers()
    perm=ScriptedPermuter(net,{})
    perm._repair_round(AGENT,net.project_row('game'),State())
    assert perm.judged and all('f2(' in source for source in perm.judged)
    assert not net.store.all('SELECT * FROM claims')
