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
