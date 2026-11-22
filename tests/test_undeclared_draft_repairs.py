"""Drafts that read a jump-table-named data table or the stack frame's slots compile after _compilable."""
from decompnet import seeds

DRAFT = """s32 f(s32 a) {
    s32 sp10;

    sp10 = *((a * 4) + &jtbl_80000A18);
    return sp10 + *(s32 *)(sp + 0x14);
}
"""


def test_compilable_declares_the_table_and_the_frame():
    out = seeds._compilable(DRAFT, 'f', 'addiu $sp, $sp, -0x20', '')
    assert 'extern s32 jtbl_80000A18[];' in out and '((s32 *)jtbl_80000A18)[a]' in out
    assert '#define sp10 (*(s32 *)(sp + 0x10))' in out and 'u8 sp[32];' in out
    assert '&jtbl' not in out.replace('jtbl_80000A18[]', '')
