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
