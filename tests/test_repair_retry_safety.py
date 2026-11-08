"""Repeated claims and captured fixes, using only homemade C and fake judges."""




import pytest

from decompnet.app import evidence_repairs, register_reuse_repairs


@pytest.mark.parametrize('control', ['for(i=0;i<n;i++){ total += stride << 2; }',
                                     'while(n--){ total += stride * 4; }',
                                     'do { total += stride << 2; } while(n--);',
                                     'again: total += stride << 2; if(n--) goto again;'])
def test_last_textual_use_is_not_reused_across_a_back_edge(control):
    source = 'int f(int n){ int stride=2; int i; int total=0; '+control+' return total; }'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_straight_line_last_shift_is_still_available():
    assert any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(
        'int f(){int index=2; return index << 2;}', 'f'))


def test_table_slot_is_staged_early_only_for_a_known_array():
    source='void f(){u8 *row; u16 index; int b; index=load(); b=next(); row=table[index]+b*4;}'
    variants=evidence_repairs.variants(source,'f','extern u8 *table[];')
    early=[s for f,s in variants if f=='table_slot_lifetime' and s.index('&table[index]')<s.index('b=next()')]
    assert early
    assert not any(s.index('&table[index]')<s.index('b=next()') for _,s in evidence_repairs.variants(source,'f'))
    for unsafe in (source.replace('u16 index;', ''), source.replace('void f()', 'void f(u8 **table)')):
        assert not any(s.index('&table[index]')<s.index('b=next()') for _,s in evidence_repairs.variants(unsafe,'f','extern u8 *table[];'))


def test_signedness_and_assignment_reuse_are_one_bounded_hypothesis():
    source='int f(){s32 word; s32 diff; u32 c=read(); u32 d=read(); diff=c-d; if(diff)return diff; word=read(); return word;}'
    variants=evidence_repairs.variants(source,'f')
    assert any(f=='unsigned_assignment_reuse' and 'u32 word;' in s and '(word = c)-d' in s for f,s in variants)
    assert not any(f=='unsigned_assignment_reuse' for f,_ in evidence_repairs.variants(source.replace('word=read();','use(word); word=read();'),'f'))


def test_forward_guard_keeps_the_body_scope_and_both_return_paths():
    source='int f(int a){if(a){if(a>2)return 0;return 1;}return 0;}'
    generated=[s for f,s in evidence_repairs.variants(source,'f') if f=='guard_forward_jump']
    assert generated and 'goto __repair_guard_body;' in generated[0]
    assert '__repair_guard_body: {if(a>2)return 0;return 1;}' in generated[0]
    assert not any(f=='guard_forward_jump' for f,_ in evidence_repairs.variants(source.replace('return 1;','work();'),'f'))


def test_shift_reuse_does_not_change_the_shift_counts_precedence():
    source='int f(){int index=2; return index << 2 + 1;}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))
