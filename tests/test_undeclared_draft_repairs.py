"""Drafts that read a jump-table-named data table or the stack frame's slots get them declared."""
from decompnet import seeds

RAW = """void f(s32 a) {
    s32 sp10;

    sp10 = *((a * 4) + &jtbl_80000A18);
    return sp10 + *(s32 *)(sp + 0x14);
}
"""
ASM = "addiu $sp, $sp, -0x20\nlw $v0, ($a0)\njr $ra\naddiu $sp, $sp, 0x20\n"


def test_a_compilable_draft_declares_the_table_and_the_frame(monkeypatch):
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args, **kwargs: RAW)
    out = seeds.generate_from_asm(ASM, 'f', 'gcc-2.7.2-kmc', valid=True)
    assert 'extern s32 jtbl_80000A18[];' in out and '((s32 *)jtbl_80000A18)[a]' in out
    assert '#define sp10 (*(s32 *)(sp + 0x10))' in out and 'u8 sp[32];' in out


def test_a_draft_for_reading_is_left_as_m2c_wrote_it(monkeypatch):
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args, **kwargs: RAW)
    assert seeds.generate_from_asm(ASM, 'f', 'gcc-2.7.2-kmc') == RAW
