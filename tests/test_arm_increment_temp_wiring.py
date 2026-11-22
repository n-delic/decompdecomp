"""decomprepair's arm_increment_temp reaches the draft repair path (and so the Deep Permuter's beam): a 183-instruction
KMC N64 draw function stood at 60 with its display-list cursor bump 'addiu' after the matrix value's 'subu' in one
arm; writing the bump through one temporary assigned in both arms matched it (decomprepair 0.5.629)."""
from decompnet.app import draft_repairs

SOURCE = """\
void draw(int flag) {
    Gfx *dl;
    u32 sel;

    if (flag) {
        dl = D_cursor;
        sel = D_slot;
        D_cursor = dl + 1;
        dl->w0 = 1;
        sel -= 0x80000000;
    } else {
        dl = D_cursor;
        dl->w0 = 1;
        sel = *(s32 *)((char *)D_state + 0x74C);
        D_cursor = dl + 1;
        sel -= 0x80000000;
    }
    ((u32 *)dl)[1] = sel;
}
"""


def test_the_shared_temporary_is_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'draw', 'mips', labels=labels, limit=None)
    child = next(t for t in result if labels.get(t) == 'arm_increment_temp')
    assert child.count('D_cursor = cursor_next;') == 2
    assert 'arm_increment_temp' in draft_repairs.AGENT_FAMILIES
