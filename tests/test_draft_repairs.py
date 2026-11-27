"""Targeted candidates and their independent, cached background budget."""

import re
import threading

import pytest

from decompnet.app import draft_repairs
from decompnet.app.draft_repairs import variants
from decompnet.app.permute import State
from tests.test_permute import AGENT, FakeNet, ProjectStub, ScriptedPermuter


def test_address_conversion_and_commutation_are_combined():
    src = 'int f(int i) { return *(int *)((u8 *)table + i); }'
    expected = 'int f(int i) { return *(int *)(i + (int)table); }'
    assert expected in variants(src, 'f', 'mips')
    # (the temporary and regrouping families read only C, so every processor gets those; the address one is MIPS's)
    assert expected not in variants(src, 'f', 'x86_64')
    assert expected not in variants(src, 'f', 'unknown')


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'x86_64', 'arm', 'thumb', 'unknown'])
def test_early_return_is_shared_and_keeps_expression(arch):
    src = 'int f(int x) { int result; result = 0; if (x >= 0) { result = (int)root((float)x); } return result; }'
    outputs = variants(src, 'f', arch)
    assert any('if (x < 0) return 0;' in out and 'return (int)root((float)x);' in out for out in outputs)


def test_guard_with_call_returns_can_be_inverted():
    src = '''int f(int x) {
    if (x == 1) {
        return send(x);
    }
    return receive(x, nested());
}
'''
    outputs = variants(src, 'f', 'mips', limit=None)
    assert any('if (x != 1)' in out and out.index('return receive(x, nested());') < out.index('return send(x);')
               for out in outputs)


def test_nested_call_argument_boundary_and_opaque_text():
    src = 'int f(int x) { /* other(x, 123) */ return send(inner(x, 2), 0x40); }'
    out = variants(src, 'f', 'mips')
    assert any('/* other(x, 123) */ return send(inner(x, 2));' in v for v in out)
    assert all('other(x, 123)' in v for v in out)
    # (other families may wrap the statement, never dropping what has an effect or isn't a literal or a name)
    for call in ('send(x, side_effect())', 'send(x, "abc")'):
        assert all(call in v for v in variants(f'int f(int x) {{ return {call}; }}', 'f', 'mips'))


def test_only_requested_definition_and_no_directives():
    src = 'int other(int x) { return send(x, 7); }\nint f(int x) { return x; }'
    assert not variants(src, 'f', 'mips')
    assert not variants('int f(int x) {\n#define GO send(x, 7)\nreturn GO; }', 'f', 'mips')
    assert not variants('int f(int x);', 'f', 'mips')
    # Extra statements and references to the accumulator must not be discarded.
    for expr in ('result + x', 'x'):
        src = f'int f(int x) {{ int result; result=0; side(); if(x>=0){{result={expr};}} return result; }}'
        labels = {}
        outputs = variants(src, 'f', 'mips', labels=labels)
        assert all('side();' in out for out in outputs)
        # (a sub-expression held in a temporary writes the same expression under another name: its own tests check it)
        assert all(f'result={expr};' in out or (expr=='result + x' and ('result = result; result += x;' in out or 'result= (x) + (result);' in out))
                   for out in outputs if not labels.get(out, '').startswith('temporary_hoist'))


def setup_repair_net(count=1):
    net = FakeNet()
    with net.store.tx() as db:
        db.execute("DELETE FROM functions")
        for i in range(1, count + 1):
            db.execute('INSERT INTO functions(id,project_id,name,n_insns,best_score,best_source) VALUES(?,1,?,10,200,?)',
                       (i, f'f{i}', f'int f{i}(int x) {{ return send(x, 7); }}'))
    return net


@pytest.fixture
def argument_drop_only(monkeypatch):
    """One proposal a function, the real argument drop: the round's cache, claim and stop rules are counted apart
    from how many other families the library has for `return send(x, 7);` (run_weight_scope wraps it too)."""
    real = draft_repairs.variants

    def only(source, name, *args, **kwargs):
        return [v for v in real(source, name, *args, **kwargs) if v == source.replace('send(x, 7)', 'send(x)')]

    monkeypatch.setattr(draft_repairs, 'variants', only)


@pytest.mark.usefixtures('argument_drop_only')
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


@pytest.mark.usefixtures('argument_drop_only')
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


@pytest.mark.usefixtures('argument_drop_only')
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


@pytest.mark.usefixtures('argument_drop_only')
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
    expected = f'int f(int i) {{ return *(int *)(i + (int){pointer}); }}'
    assert expected in variants(src, 'f', 'mips')
    assert expected not in variants(src, 'f', 'x86_64')


def test_member_rule_excludes_calls_and_increments():
    for expr in ['get()->base', 'obj[i++].base', 'obj->base++']:
        src = f'int f(int i) {{ return *(int *)(((u8 *) {expr}) + i); }}'
        assert all(f'((u8 *) {expr}) + i' in out for out in variants(src, 'f', 'mips'))


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
    from decomprepair import narrow_chains
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
    from decomprepair import expression_repairs
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
    from decomprepair import temporary_repairs
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


@pytest.mark.usefixtures('argument_drop_only')
def test_progress_movement_abandons_stale_repair_snapshot_without_caching(monkeypatch, caplog):
    from decompnet.app.progress import ProgressMoved

    net = setup_repair_net(2)
    perm = ScriptedPermuter(net, {})
    source = net.function()['best_source']
    def moved(*args):
        raise ProgressMoved('Progress changed while this attempt compiled.')
    monkeypatch.setattr(perm, '_score', moved)
    with caplog.at_level('INFO', logger='decompdecomp'):
        perm._repair_round(AGENT, net.project_row('game'), State())
    assert not net.store.all('SELECT * FROM reuse_trials')
    assert not net.store.all('SELECT * FROM claims')
    assert net.function()['best_source'] == source and net.function()['permuted'] == 0
    assert all(record.exc_info is None for record in caplog.records)
    assert 'deferred' in caplog.records[-1].getMessage()
    # A fresh pass can judge both functions; the moved trial is not a cached miss.
    monkeypatch.setattr(perm, '_score', ScriptedPermuter._score.__get__(perm))
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert len(perm.judged) == 2


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'arm'])
def test_shared_switch_default_tail_reaches_permuter_candidates(arch):
    source = """\
void f(int x) {
    switch (x) {
        case 0: start();
        default:
        joined:
            finish();
            break;
        case 1: goto joined;
        case 2: break;
    }
}
"""
    labels = {}
    choices = variants(source, 'f', arch, labels=labels)
    [lifted] = [choice for choice in choices if labels[choice] == 'switch_default_tail']
    assert lifted.index('case 2: break;') < lifted.index('joined:\n')
    assert lifted.index('goto after_switch_tail;') < lifted.index('joined:\n')


def test_a_byte_range_test_and_masked_equality_become_the_masked_chain():
    # A KMC GCC 2.7.2 function at 215 (an lb and a missing andi): the unsigned local and the masked chain, 15.
    src = ('s32 f(void *p) {\n    s8 t;\n    s32 r;\n\n    t = M2C_FIELD(p, u8 *, 0x82);\n'
           '    if (((u32) (t - 9) < 2U) || ((t & 0xFF) == 0xB)) {\n        r = 1;\n    } else {\n        r = 0;\n    }\n'
           '    return r;\n}\n')
    outputs = variants(src, 'f', 'mips')
    assert any('u8 t;' in out and '((t & 0xFF) == 9) || ((t & 0xFF) == 0xA) || ((t & 0xFF) == 0xB)' in out for out in outputs)


def test_list_search_copy_respects_header_macros():
    source = '''
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))
void *find(s32 key) {
    void *node;
    void *result;
    node = get_head();
    result = NULL;
    if (node != NULL) {
loop_1:
        result = node;
        if (M2C_FIELD(node, s32 *, 12) != key) {
            node = M2C_FIELD(node, void **, 4);
            result = NULL;
            if (node != NULL) {
                goto loop_1;
            }
        }
    }
    return result;
}
'''
    labels = {}
    candidates = variants(source, 'find', 'mips', labels=labels, limit=None)
    assert any(labels.get(candidate) == 'goto_search_loop' for candidate in candidates)
    labels = {}
    candidates = variants(source, 'find', 'mips', labels=labels, limit=None, header='#define HIDDEN result')
    assert all(labels.get(candidate) != 'goto_search_loop' for candidate in candidates)


def test_field_store_tail_is_available_to_drafts():
    source = '''
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))
void paint(void *obj, int selected) {
    if (selected) {
        M2C_FIELD(obj, u8 *, 0) = 0xFE;
    } else {
        M2C_FIELD(obj, u8 *, 0) = 0xFF;
    }
    M2C_FIELD(obj, u8 *, 3) = 0xFF;
}
'''
    labels = {}
    candidates = variants(source, 'paint', 'mips', labels=labels, limit=None)
    assert any(labels.get(candidate) == 'branch_store_tail' for candidate in candidates)


def test_short_repair_pass_validates_only_ranked_candidates(monkeypatch):
    from decompnet.app import draft_repairs
    from decomprepair import repair_preflight

    checked = []

    def invalid(candidate, name):
        checked.append(candidate)
        return candidate.startswith('invalid')

    monkeypatch.setattr(repair_preflight, 'invalid_call', invalid)
    proposals = ['invalid_a', 'b1', 'a1', 'b2', 'a2', 'b3', 'a3']
    labels = {candidate: candidate[-2] if not candidate.startswith('invalid') else 'a' for candidate in proposals}
    assert draft_repairs._finish('source', 'f', proposals, labels, [], 3) == ['b1', 'a1', 'b2']
    assert 'invalid_a' in checked
    assert 'b3' not in checked
    assert 'a3' not in checked


def test_draft_repairs_offer_direct_selected_byte_store():
    from decompnet.app import draft_repairs

    source = """
extern unsigned char selected;
extern unsigned char fallback;
void f(int outer, int inner) {
    volatile short scratch;
    if (outer) {
        if (inner) { scratch = 4; } else { scratch = 4; }
    } else { scratch = fallback; }
    selected = scratch;
}
"""
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    [direct] = [candidate for candidate in proposals if labels[candidate] == 'branch_store_tail']
    assert 'volatile short scratch;' not in direct
    assert direct.count('selected = 4;') == 2


def test_draft_repairs_offer_cleanup_for_uninitialized_identical_arms():
    from decompnet.app import draft_repairs

    source = """
int f(void) {
    float scratch;
    if (scratch) {
        setup();
    } else {
        setup();
    }
    return 0;
}
"""
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    [cleaned] = [candidate for candidate in proposals if labels[candidate] == 'common_scalar_tail']
    assert 'if (scratch)' not in cleaned
    assert cleaned.count('setup();') == 1


def test_draft_repairs_offer_a_separate_float_clamp_lifetime():
    from decompnet.app import draft_repairs

    source = """
float f(float x, float y, int enabled) {
    float value;
    if (enabled) {
        value = x / y;
        if (value < -1.0f) value = -1.0f;
        else if (value > 1.0f) value = 1.0f;
        return value * x;
    }
    value = x + y;
    return value;
}
"""
    asm = 'div.s $f4, $f2, $f0\nc.lt.s $f4, $f6\nc.lt.s $f2, $f4'
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', asm=asm, labels=labels)
    [clamp] = [candidate for candidate in proposals if labels[candidate] == 'split_float_clamp_lifetime']
    assert 'float __repair_clamped_float = x / y;' in clamp
    assert 'value = x + y;' in clamp


def test_shifted_byte_lifetime_is_offered_to_the_permuter():
    source = '''
void f(Voice *v, Note *n) {
    u32 pitch;
    u32 w;
    pitch = ((u32) (((u8 *) n)[2] * v->rate)) >> 7;
    pitch += ((u8 *) n)[1] << 16;
    w = n->word;
    use(pitch, w);
}
'''
    labels = {}
    children = variants(source, 'f', 'mips', labels=labels, header='typedef volatile u32 vu32;')
    assert any(labels.get(child) == 'shifted_byte_lifetime' and
               'if (__repair_shifted_byte & 1)' in child for child in children)
    assert not any(labels.get(child) == 'shifted_byte_lifetime'
                   for child in variants(source, 'f', 'x86', labels=labels))


def test_a_best_saved_before_a_rename_still_gets_children():
    # the whole-path audit: 13 of 34 stalls had no children at all, their best defining func_<VRAM> after a rename
    net = setup_repair_net()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='sendSeven', vram=?, best_source=? WHERE id=1",
                   (0x80001000, 'int func_80001000(int x) { return send(x, 7); }'))
    perm = ScriptedPermuter(net, {})
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert perm.judged and all('int sendSeven(' in source for source in perm.judged)
