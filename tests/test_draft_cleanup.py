"""The draft cleanup stage (app/draft_cleanup.py): cleaned copies of a machine draft, judged beside it."""
from decompnet.app import draft_cleanup

DRAFT = """extern M2C_UNK D_800C0784;

s32 func_80044968(s32 arg0) {
    s32 var_v0;
    void *temp_v1;

    var_v0 = 0;
    if (arg0 >= 0) {
        temp_v1 = *(&D_800C0784 + (arg0 * 0x10));
        if (temp_v1 != NULL) {
            var_v0 = M2C_FIELD(temp_v1, s32 *, 0x18);
        }
    }
    return var_v0;
}
"""


def test_raw_record_access_becomes_a_record_view():
    [(family, source)] = draft_cleanup.cleaned(DRAFT, 'func_80044968', 'mips')
    assert family == 'draft_cleanup'
    assert 'D_800C0784[arg0]' in source and '(arg0 * 0x10)' not in source


def test_untouched_draft_gives_no_copy():
    plain = "s32 f(s32 a) {\n    return a + 1;\n}\n"
    assert draft_cleanup.cleaned(plain, 'f', 'mips') == []


def test_a_failing_rewrite_keeps_the_draft(monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError('parser')
    monkeypatch.setattr(draft_cleanup.field_struct_view_repairs, 'variants', broken)
    monkeypatch.setattr(draft_cleanup.gbi_macro_commands, 'variants', broken)
    assert draft_cleanup.cleaned(DRAFT, 'func_80044968', 'mips') == []


def test_setting_turns_it_off():
    assert draft_cleanup.enabled({}) and not draft_cleanup.enabled({'draft_cleanup': False})


def test_both_modes_use_the_stage():
    # ROM mode (autopilot's draft candidates) and project mode (forkengine's autopilot drafts) both reach it
    from pathlib import Path
    root = Path(draft_cleanup.__file__).parent
    assert 'draft_cleanup.cleaned(' in (root / 'engine' / 'autopilot.py').read_text()
    assert 'draft_cleanup.cleaned(' in (root / 'forkengine.py').read_text()


WALK_ASM = '''glabel f
/* 0 80073A00 00002021 */  addu       $a0, $zero, $zero
/* 4 80073A04 3C03800E */  lui        $v1, %hi(D_A)
/* 8 80073A08 24631A70 */  addiu      $v1, $v1, %lo(D_A)
/* C 80073A0C AC600000 */  sw         $zero, 0x0($v1)
/* 10 80073A10 24840001 */  addiu      $a0, $a0, 0x1
/* 14 80073A14 2C820004 */  sltiu      $v0, $a0, 0x4
/* 18 80073A18 1440FFFC */  bnez       $v0, .L80073A0C
/* 1C 80073A1C 24630004 */  addiu      $v1, $v1, 0x4
/* 20 80073A20 03E00008 */  jr         $ra
/* 24 80073A24 00000000 */  nop
'''

WALK = '''extern s32 D_A;

void f(void) {
    s32 *var_v1;
    u32 var_a0;

    var_a0 = 0;
    var_v1 = &D_A;
    do {
        *var_v1 = 0;
        var_a0 += 1;
        var_v1 += 4;
    } while (var_a0 < 4U);
}
'''


def test_m2c_byte_step_is_fixed_and_the_cursor_offered_as_an_index():
    # an N64 game: m2c's `+= 4` on an `s32 *` for a 4-byte addiu compiles as a 16-byte step (draft-start-candidates.md)
    found = dict(draft_cleanup.cleaned(WALK, 'f', 'mips', WALK_ASM))
    assert 'var_v1 += 1;' in found['draft_cleanup']
    assert '*(&D_A + var_a0) = 0;' in found['draft_cleanup_index'] and 'var_v1' not in found['draft_cleanup_index']


def test_without_the_target_a_byte_step_is_left_alone():
    found = dict(draft_cleanup.cleaned(WALK, 'f', 'mips'))
    assert 'draft_cleanup' not in found  # no immediate says the step was bytes
    assert '(u8 *)&D_A + var_a0 * 16' in found['draft_cleanup_index']  # the index as C reads the step


def test_every_byte_step_site_is_fixed_in_one_draft():
    # one table slot read twice, both indexes scaled again by m2c: one fix alone left the function an instruction off
    draft = ('extern M2C_UNK D_A;\nvoid f(s32 i, s32 j) {\n    g(*((i * 4) + &D_A));\n'
             '    g(*((j * 4) + &D_A));\n}\n')
    found = dict(draft_cleanup.cleaned(draft, 'f', 'mips', 'sll        $v0, $a0, 2\n'))
    assert '(&D_A)[i]' in found['draft_cleanup'] and '(&D_A)[j]' in found['draft_cleanup']
