import pytest

from decompnet.app.address_repairs import variants as repairs
from decompnet.app.constant_repairs import variants
from decompnet.arch.mips_literals import changes


def fn(words, big=True, relocs='[]'):
    return {'target':b''.join(w.to_bytes(4,'big' if big else 'little') for w in words),'relocs':relocs}


@pytest.mark.parametrize('big',[True,False])
def test_pure_constant_miss_gets_repaired_without_any_address_change(big):
    candidate,target=fn([0x2402001c],big),fn([0x2402001d],big)
    report={'rows':[],'candidate_target':candidate['target'].hex(),'candidate_relocs':'[]'}
    assert repairs('return 28;',report,target,big,set()) == ['return 29;']


@pytest.mark.parametrize('big',[True,False])
@pytest.mark.parametrize('funct,operator,old,new',[(0,'<<',4,2),(2,'>>',4,2),(3,'>>',4,2)])
def test_fixed_shift_amounts_preserve_registers_and_suffixes(big,funct,operator,old,new):
    base=(4<<16)|(2<<11)|funct
    assert variants(f'return x {operator} 4U;',fn([base|(old<<6)],big),fn([base|(new<<6)],big),big) == [f'return x {operator} 2U;']


@pytest.mark.parametrize('source,expected',[
    ('return x * 16;', 'return x * 4;'),
    ('return 16 * x;', 'return 4 * x;'),
    ('return x << 4;', 'return x << 2;'),
])
def test_left_shift_evidence_can_repair_explicit_multipliers(source,expected):
    assert variants(source,fn([0x00041100]),fn([0x00041080]),True) == [expected]


def test_right_shift_evidence_can_repair_explicit_divisor():
    assert variants('return x / 16;',fn([0x00041102]),fn([0x00041082]),True) == ['return x / 4;']


def test_unrelated_literals_comments_strings_and_symbol_addresses_are_preserved():
    source='int func_16(void) { return x * 16; /* x * 16 */ char *s="16"; }'
    assert variants(source,fn([0x00041100]),fn([0x00041080]),True) == [source.replace('x * 16;','x * 4;')]
    assert variants('return x + 16;',fn([0x00041100]),fn([0x00041080]),True) == []


def test_repeated_literal_edits_are_individual_then_bounded_group():
    source='return a + 28 + b + 28;'
    result=variants(source,fn([0x2402001c]),fn([0x2402001d]),True)
    assert result == ['return a + 29 + b + 28;','return a + 28 + b + 29;','return a + 29 + b + 29;']
    assert len(variants(' + '.join(['28']*20),fn([0x2402001c]),fn([0x2402001d]),True)) == 4


def test_multiple_changed_instructions_can_suggest_consistent_group_edit():
    assert variants('return 28 + 28;',fn([0x2402001c,0x2403001c]),fn([0x2402001d,0x2403001d]),True)[0] == 'return 29 + 29;'


def test_two_independent_edits_can_combine_and_stay_bounded():
    result=variants('return x * 16 + 28;',fn([0x00041100,0x2403001c]),fn([0x00041080,0x2403001d]),True)
    assert result[0] == 'return x * 4 + 29;'
    assert len(result)<=8


def test_branches_register_changes_variable_shifts_and_signed_values_are_rejected():
    for old,new in [(0x10000004,0x10000008),(0x2402001c,0x2403001d),(0x00841004,0x00841044),(0x2402fffc,0x2402fff8)]:
        assert variants('return x * 16 + 4;',fn([old]),fn([new]),True) == []


def test_relocated_and_conflicting_immediates_do_not_make_proposals():
    assert variants('28',fn([0x2402001c],relocs='[[0,5,0]]'),fn([0x2402001d]),True) == []
    assert variants('28 + 28',fn([0x2402001c,0x2403001c]),fn([0x2402001d,0x2403001e]),True) == []
    assert changes(b'a',b'bb',set(),True) == []


def test_octal_literals_and_unsigned_suffixes_follow_c_values():
    assert variants('return 034UL;',fn([0x2402001c]),fn([0x2402001d]),True) == ['return 29UL;']
    assert variants('return -28;',fn([0x2402001c]),fn([0x2402001d]),True) == []


def test_invalid_literal_in_disabled_text_does_not_abort_search():
    source='#if 0\n08\n#endif\nreturn 28;'
    assert variants(source,fn([0x2402001c]),fn([0x2402001d]),True) == [source.replace('28','29')]
