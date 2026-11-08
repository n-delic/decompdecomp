import re

import pytest

from decompnet.app.store_result_repairs import variants


def source(body, prefix='', parameters='struct R *p, int flag'):
    return prefix+'\nvoid demo('+parameters+') {'+body+'}\nint neighbour(void) { return 7; }'


def reused(text, variable='height'):
    return [candidate for _, candidate in variants(text, 'demo') if re.search(r'=\s*\(\s*'+variable+r'\s*=', candidate)]


def test_initialized_real_operand_reused_without_reassociation_or_store_change():
    text = source('int y; int full; y=5; full=20; { int height=full/2; p->value=(y+height)+1; }')
    choices = reused(text)
    assert len(choices) == 1
    assert choices[0] == text.replace('p->value=(y+height)+1;', 'p->value=(height = (y+height)+1);')
    assert choices[0].endswith('int neighbour(void) { return 7; }')


def test_first_declaration_and_plain_field_named_like_operand_initialize_correctly():
    text = source('int height; int y; height=p->height; y=p->y; p->value=(y+height)+1;')
    assert reused(text)


@pytest.mark.parametrize('tail', [
    'use(height);',
    'if(flag) use(height);',
    'goto old; height=7; old: use(height);',
    'switch(flag) {case 1:height=7;break;default:use(height);}',
    'while(flag) {if(flag==2) use(height);break;}',
])
def test_every_outgoing_path_must_stop_using_old_operand(tail):
    assert not reused(source('int height; int y; height=8; y=5; p->value=y+height;'+tail))


def test_loop_backedge_and_continue_prevent_last_textual_use_reuse():
    text = source('int height; int y; height=8; y=5; do { p->value=y+height; } while(flag);')
    assert not reused(text)
    reset = text.replace('p->value=y+height;', 'height=8; p->value=y+height;')
    assert reused(reset)


@pytest.mark.parametrize('body', [
    'int height; int y; if(flag)height=8; y=5; p->value=y+height;',
    'int height=height; int y; y=5; p->value=y+height;',
    'int height; int y; goto skip; height=8; skip:y=5; p->value=y+height;',
    'int height; int y; height=8; y=5; escape(&(height)); p->value=y+height;',
    'volatile int height; int y; height=8; y=5; p->value=y+height;',
    'const int height=8; int y; y=5; p->value=y+height;',
    'short height; int y; height=8; y=5; p->value=y+height;',
    'unsigned int height; int y; height=8; y=5; p->value=y+height;',
    'long height; int y; height=8; y=5; p->value=y+height;',
    'int height; float y; height=8; y=5; p->value=y+height;',
    'int height; int y; height=8; y=5; { float height; p->value=y+height; }',
    'int height; int y; height=8; y=5; p->value=get()+height;',
    'int height; int y; height=8; y=5; p->value=y++ +height;',
    'int height; int y; height=8; y=5; p->value=p->other+height;',
    'int height; int y; height=8; y=5; p->value=(short)y+height;',
    'int height; int y; height=8; y=5; if(flag)p->value=y+height;',
    'struct Inner {int height;}; int y; height=8; y=5; p->value=y+height;',
    'int height; int y; height=8; y=5; { struct Inner {int height;}; p->value=y+height; }',
    'int height; int y; height=8; y=5; { char q, height; height=127; p->value=y+height; }',
    'int height; int y; height=8; y=5; { char q, *height; p->value=y+height; }',
    'int height; int y; height=8; y=5; for(char height=0; height<3; height++) {p->value=y+height;}',
    'int height; int y; height=8; y=5; int &alias=height; p->value=y+height; use(alias);',
])
def test_unsupported_types_effects_initialization_and_bindings_are_not_rewritten(body):
    assert not reused(source(body))


def test_scope_and_parameter_shadows_do_not_supply_an_expression_type():
    assert not reused(source('int height; height=8; { int y; y=5; } p->value=y+height;', parameters='struct R *p, float y'))
    assert not reused(source('{int height;height=8; p->value=y+height;}', parameters='struct R *p, float height, int y'))


@pytest.mark.parametrize('alias', ['', 'typedef unsigned int s32;', 'typedef short s32;', 'typedef const int s32;'])
def test_unknown_narrow_qualified_and_unsigned_aliases_are_opaque(alias):
    text = source('s32 height; s32 y; height=8; y=5; p->value=y+height;', alias)
    assert not reused(text)


def test_signed_alias_is_resolved_in_header_and_negative_literal_keeps_association():
    text = source('s32 height; s32 y; height=8; y=5; p->value=(y-height)-1;')
    choices = variants(text, 'demo', 'typedef signed int s32;')
    assert any('p->value=(height = (y-height)-1);' in candidate for _, candidate in choices)


def test_only_fixed_global_slot_macros_are_permitted():
    prefix = '#define SLOT (*(Rect **)(global_slot + 8))\nextern u8 global_slot[];'
    text = source('int height; int y; height=8; y=5; SLOT->value=y+height;', prefix)
    assert reused(text)
    assert not reused('#define Rect pointer_type_using(height)\n'+text)
    hidden = text.replace('global_slot + 8', 'height + 8')
    assert not reused(hidden)
    assert not reused(text.replace('extern u8 global_slot[];', ''), variable='height')
    assert not reused(text.replace('global_slot + 8', 'hidden_base + 8').replace('extern u8 global_slot[];', '#define hidden_base height'))
    assert not reused(source('int height; int y; height=8; y=5; TOUCH(); p->value=y+height;', '#define TOUCH() use(height)'))
    assert not reused(source('int height; int y; height=8; y=5; p->value=y+height;', '#define height shared'))


def test_function_local_alias_cannot_override_header_width():
    text=source('typedef short s32; s32 height=127; s32 y=1; p->value=y+height;')
    assert not variants(text, 'demo', 'typedef int s32;')
