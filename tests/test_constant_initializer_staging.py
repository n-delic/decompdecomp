"""Atomic allocation repairs preserve initialization order and require target evidence."""
from decomprepair import constant_initializer_staging as init
from decomprepair import live_return_address_repairs as live

ASM='/* 0 80001000 3C01800B */ lui $at, 0x800b\n/* 4 80001004 AC3F2000 */ sw $ra, 0x2000($at)'
SOURCE='extern int D_800B2000; int f(void) { int result=0; int one=1; volatile int untouched; { int unknown; do { D_800B2000=unknown; } while(0); } result=call(one); return result; }'


def test_atomic_hardware_and_allocation_candidates():
    variants=live.variants(SOURCE,'f','mips',ASM)
    assert any(family==live.FAMILY and '(int)__builtin_mips_live_return_address()' in text for family,text in variants)
    bundles=[text for family,text in variants if family==live.BUNDLE]
    assert any('int one;\n int result=0; one = 1;' in text for text in bundles)
    assert any('call(((int)(1)))' in text for text in bundles)
    assert all('volatile int untouched;' in text and 'do {' in text for text in bundles)
    assert not live.variants(SOURCE,'f','mips','')
    assert not live.variants(SOURCE.replace('int unknown;','volatile int unknown;'),'f','mips',ASM)
    assert not live.variants(SOURCE.replace('extern int','extern short'),'f','mips',ASM)


def test_literal_inline_keeps_type_and_lines():
    source='int f(void) {\n unsigned int one=1;\n return sizeof(one)+one;\n}'
    choices=init.inlined(source,'f',arch='mips')
    assert len(choices)==1
    assert 'sizeof(((unsigned int)(1)))' in choices[0]
    assert choices[0].count('\n')==source.count('\n')


def test_mutable_escaped_qualified_shadowed_and_macro_locals_are_not_inlined():
    for body in ('int one=1; one++; return one;',
                 'int one=1; ++one; return one;',
                 'int one=1; (one)=2; return one;',
                 'int one=1; escape(&one); return one;',
                 'volatile int one=1; return one;',
                 'int one=1; { int one=2; use(one); } return one;'):
        assert not init.inlined('int f(void) {'+body+'}','f',arch='mips')
    assert not init.inlined('#define read() one\nint f(void) { int one=1; return read(); }','f',arch='mips')


def test_staging_retains_evaluation_order_and_rejects_intervening_effects():
    source='int f(void) { int result=0; int one=1; return call(one)+result; }'
    assert init.staged(source,'f',arch='mips')==['int f(void) { int one;\n int result=0; one = 1; return call(one)+result; }']
    assert not init.staged(source.replace('int one=1;', 'effect(); int one=1;'),'f',arch='mips')
    assert not init.staged(source.replace('int one=1;', 'volatile int one=1;'),'f',arch='mips')
