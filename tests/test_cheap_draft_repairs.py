"""Autopilot's draft stage tries the cheap repair families beside the raw draft (app/cheap_draft_repairs.py)."""
from decompnet.app import cheap_draft_repairs

# The shape of a small MMIO accessor as m2c writes it: a raw register address read through a plain pointer.
MMIO = """s32 func_8007ABC0(void) {
    return (*(s32 *)0xA4040010 & 0x1C) != 0;
}
"""
PLAIN = "s32 f(s32 a) {\n    return a + 1;\n}\n"


def test_a_register_read_gets_its_volatile_copy_as_a_candidate():
    found = cheap_draft_repairs.variants(MMIO, 'func_8007ABC0', 'mips')
    assert ('mmio_address', MMIO.replace('(s32 *)', '(volatile s32 *)')) in found


def test_an_absolute_value_rule_runs_when_the_target_negates():
    source = ("s32 f(s32 a, s32 b) {\n    s32 var_v0;\n    var_v0 = a - b;\n    if (var_v0 < 0) {\n"
              "        var_v0 = -var_v0;\n    }\n    return var_v0;\n}\n")
    negates = '/* 0 0 00000000 */  negu       $v0, $v0\n'
    adds = '/* 0 0 00000000 */  addu       $v0, $a0, $a1\n'
    assert 'abs_idiom' in [family for family, _ in cheap_draft_repairs.variants(source, 'f', 'mips', negates)]
    assert 'abs_idiom' not in [family for family, _ in cheap_draft_repairs.variants(source, 'f', 'mips', adds)]


def test_an_empty_leaf_draft_gets_the_unused_local_its_frame_shows():
    draft = "void f(void) {\n\n}\n"
    opens = 'addiu      $sp, $sp, -0x10\naddiu      $sp, $sp, 0x10\njr         $ra\nnop\n'
    assert ('unused_frame_local', "void f(void) {\n    u8 __repair_frame[16];\n\n}\n") in cheap_draft_repairs.variants(
        draft, 'f', 'mips', opens)
    # (a frame the target stores to, or one a call needs, is not an unused local)
    for asm in (opens + 'sw         $a0, 0x4($sp)\n', opens + 'jal        func_80000000\n', 'jr         $ra\nnop\n'):
        assert 'unused_frame_local' not in [family for family, _ in cheap_draft_repairs.variants(draft, 'f', 'mips', asm)]


def test_a_draft_that_shows_no_shape_costs_no_candidate():
    assert cheap_draft_repairs.variants(PLAIN, 'f', 'mips') == []


def test_other_processors_get_no_register_candidates():
    assert cheap_draft_repairs.variants(MMIO, 'func_8007ABC0', 'x86') == []


def test_candidates_are_capped_and_never_repeat_the_draft(monkeypatch):
    many = [f"s32 g{i}(void) {{ return {i}; }}\n" for i in range(30)]
    monkeypatch.setattr(cheap_draft_repairs.mmio_repairs, 'variants', lambda *args: many + [MMIO])
    found = cheap_draft_repairs.variants(MMIO, 'func_8007ABC0', 'mips')
    assert len(found) == cheap_draft_repairs.MAX_CANDIDATES
    assert MMIO not in [source for _family, source in found]


def test_a_failing_family_is_skipped_and_the_rest_still_run(monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError('parser')
    monkeypatch.setattr(cheap_draft_repairs.symbol_width_repairs, 'variants', broken)
    monkeypatch.setattr(cheap_draft_repairs.stack_run_array_repairs, 'variants', broken)
    found = cheap_draft_repairs.variants(MMIO, 'func_8007ABC0', 'mips')
    assert found and found[0][0] == 'mmio_address'


def test_the_setting_turns_the_stage_off():
    assert cheap_draft_repairs.enabled({})
    assert not cheap_draft_repairs.enabled({'cheap_draft_repairs': False})


# A switch as m2c writes it when its jump table sends several entries to one place: stacked labels over one body, then
# the stores after the switch through one symbol's address.
STACKED = """void f(void) {
    switch (sPage) {
    case 1:
    case 2:
        M2C_FIELD(&D_80070000, s16 *, 0) = 1;
        break;
    case 3:
        M2C_FIELD(&D_80070000, s16 *, 0) = 2;
        break;
    }
    M2C_FIELD(&D_80070010, s32 *, 0x14) = 0;
    M2C_FIELD(&D_80070010, s32 *, 0x18) = 0;
}
"""
SHARED_TABLE = [(1, [0x80040030, 0x80040030, 0x800400C8])]


def test_a_shared_table_entry_gets_the_copied_bodies_then_the_run_pointer_on_top():
    found = cheap_draft_repairs.variants(STACKED, 'f', 'mips', tables=SHARED_TABLE)
    assert [family for family, _ in found[:2]] == ['case_label_copies', 'case_label_copies+symbol_run_pointer']
    copied, pointed = found[0][1], found[1][1]
    assert copied.count('M2C_FIELD(&D_80070000, s16 *, 0) = 1;') == 2
    assert '__run_' not in copied
    # only the run after the switch is read through the pointer, on top of the copies
    assert pointed.count('M2C_FIELD(&D_80070000, s16 *, 0) = 1;') == 2
    assert 's8 *__run_D_80070010 = (s8 *) &D_80070010;' in pointed


def test_stacked_labels_without_a_shared_table_entry_are_left_alone():
    for tables in ((), [(1, [0x80040030, 0x80040060, 0x800400C8])]):
        families = [family for family, _ in cheap_draft_repairs.variants(STACKED, 'f', 'mips', tables=tables)]
        assert not [family for family in families if 'case_label_copies' in family]
