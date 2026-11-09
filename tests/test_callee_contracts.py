from types import SimpleNamespace

import pytest

from decompnet.app import callee_contracts as contracts
from decompnet.app import wrapper_contracts


def test_explicit_narrow_prototype_is_not_proof_of_callee_interface():
    hint=contracts.mismatch('extern void helper(u8, u16); void caller(void) { helper(1, 2); }',
                            'void helper(s32 a, s32 b) { work(a, b); }', 'helper')
    assert 'caller void(u8,u16), selected definition void(s32,s32)' in hint


def test_unused_argument_and_return_mismatch_are_visible():
    hint=contracts.mismatch('s8 helper(s32, s32);', 's32 helper(s32 a) { return a; }', 'helper')
    assert 'caller s8(s32,s32), selected definition s32(s32)' in hint


@pytest.mark.parametrize('prototype, definition', [
    ('extern int helper(unsigned int);', 's32 helper(u32 arg) { return 0; }'),
    ('void *helper(u8 *);', 'void *helper(unsigned char *p) { return p; }'),
    ('void helper(void);', 'void helper(void) {}'),
    ('void helper(int n);', 'void helper(s32 arg) {}'),
])
def test_compatible_known_spellings_are_quiet(prototype, definition):
    assert contracts.mismatch(prototype, definition, 'helper') == ''


@pytest.mark.parametrize('prototype, definition', [
    ('void helper();', 'void helper(s32 arg) {}'),
    ('void helper(int, ...);', 'void helper(s32 arg) {}'),
    ('void helper(Widget *);', 'void helper(void *p) {}'),
    ('void helper(const u8 *);', 'void helper(u8 *p) {}'),
    ('void helper(void (*)(int));', 'void helper(s32 p) {}'),
    ('void helper(s32[]);', 'void helper(s32 p) {}'),
    ('void helper(int); void helper(short);', 'void helper(s32 p) {}'),
    ('#define helper other\nvoid helper(u8);', 'void helper(s32 p) {}'),
    ('/* void helper(u8); */', 'void helper(s32 p) {}'),
    ('void caller(void) { helper(1); }', 'void helper(s32 p) {}'),
])
def test_ambiguous_or_complex_contracts_are_not_classified(prototype, definition):
    assert contracts.mismatch(prototype, definition, 'helper') == ''


def test_full_brief_reuses_single_bounded_lookup_and_existing_budget():
    calls=[]
    def read(*args):
        calls.append(args)
        return [{'name':'helper'+str(i),'matched_source':f'void helper{i}(s32 x) {{}}'} for i in range(5)]
    source=''.join(f'void helper{i}(u8);' for i in range(5))+'void caller(void) {'+''.join(f'helper{i}(1);' for i in range(5))+'}'
    hint=wrapper_contracts.brief(SimpleNamespace(project_store=lambda _project: SimpleNamespace(all=read)), {'id':1}, {'name':'caller'}, source)
    assert len(calls)==1
    assert all('helper'+str(i) in hint for i in range(3))
    assert 'helper3' not in hint
    assert 'No type or signature was changed' in hint


def test_return_call_is_not_mistaken_for_another_declaration():
    hint=contracts.mismatch('extern s8 helper(s32); s32 caller(s32 x) { return helper(x); }',
                            's32 helper(s32 x) { return x; }', 'helper')
    assert 'caller s8(s32), selected definition s32(s32)' in hint


@pytest.mark.parametrize('prototype, definition, expected', [
    ('void helper(Widget *, s32, s32, u16 *);',
     'void helper(u8 *obj, s32 a, s16 b, s16 *out) {}',
     'caller void(?,s32,s32,u16*), selected definition void(u8*,s32,s16,s16*)'),
    ('Widget *helper(u8);', 'Other *helper(s32 a) {}',
     'caller ?(u8), selected definition ?(s32)'),
    ('s8 helper(Widget *);', 's32 helper(Other *a) { return 0; }',
     'caller s8(?), selected definition s32(?)'),
])
def test_known_disagreement_survives_an_unresolved_typedef(prototype, definition, expected):
    hint = contracts.mismatch(prototype, definition, 'helper')
    assert expected in hint
    assert '? = unresolved typedef, not compared' in hint


@pytest.mark.parametrize('prototype, definition', [
    ('void helper(Widget *, u8);', 'void helper(Other *p, unsigned char a) {}'),
    ('Result helper(u8);', 's32 helper(u8 a) { return 0; }'),
    ('void helper(VoidAlias);', 'void helper(void) {}'),
    ('void helper(Widget *, u8);', 'void helper(u8 a) {}'),
    ('void helper(const Widget *, u8);', 'void helper(Other *p, s32 a) {}'),
    ('void helper(Widget *[], u8);', 'void helper(Other *p, s32 a) {}'),
    ('void helper(Widget *, ...);', 'void helper(Other *p, s32 a) {}'),
])
def test_partial_contract_does_not_classify_unknown_or_complex_types(prototype, definition):
    assert contracts.mismatch(prototype, definition, 'helper') == ''


def test_full_brief_keeps_partial_contract_warning_in_shared_budget():
    def read(*args):
        return [{'name': 'helper', 'matched_source': 'void helper(u8 *p, s16 x) {}'}]
    source = 'void helper(Widget *, s32); void caller(void) { helper(p, 1); }'
    hint = wrapper_contracts.brief(SimpleNamespace(project_store=lambda _project: SimpleNamespace(all=read)),
                                   {'id': 1}, {'name': 'caller'}, source)
    assert 'caller void(?,s32), selected definition void(u8*,s16)' in hint
    assert 'No type or signature was changed' in hint
