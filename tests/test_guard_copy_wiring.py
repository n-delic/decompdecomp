"""decomprepair 0.5.574's guard_copy_cse_barrier reaches the draft repair path (a table entry loaded into $v0 and
copied into $s0 in the branch's delay slot: 459 -> 244 on a 731-instruction KMC function)."""
from decompnet.app import draft_repairs

DRAFT = """\
void draw(void **table, s32 idx, s32 *base) {
    void *temp_v0_6;
    temp_v0_6 = *(s32 *)((idx * 4) + *base);
    D_cursor += 8;
    if (temp_v0_6 != NULL) {
        if ((((Item *)temp_v0_6)->value != 0) || (prepare(temp_v0_6), (((Item *)temp_v0_6)->value != 0))) {
            emit(temp_v0_6);
        }
    }
}
"""


def test_guard_copy_is_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(DRAFT, 'draw', 'mips', labels=labels, limit=None)
    assert 'guard_copy_cse_barrier' in labels.values()
    text = next(t for t in result if labels.get(t) == 'guard_copy_cse_barrier')
    assert 'temp_v0_6_gc = temp_v0_6;\n        temp_v0_6 = NULL;' in text
    assert 'guard_copy_cse_barrier' in draft_repairs.AGENT_FAMILIES
