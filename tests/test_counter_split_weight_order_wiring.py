"""decomprepair 0.5.569's m2c-macro fix (the loop-weight families fire on sources that keep M2C_FIELD), the reused
counter split with its weighted start, and the preheader run order reach the draft repair path: a 731-instruction KMC
function went 619 -> 519 -> 459 with them."""
from decompnet.app import draft_repairs

NAME = 'walk'
FAMILIES = ('reused_counter_split_scope', 'preheader_run_order')

DRAFT = """\
#ifndef M2C_DEFINES
#define M2C_DEFINES
#define M2C_FIELD(expr, type_ptr, offset) (((struct { __typeof__(*(type_ptr)0) v; } *)((s8 *)(expr) + (offset)))->v)
#endif
void walk(void *arg0, s32 mode, s32 *out) {
    s32 var_s6;
    s32 n;
    char *base;
    s32 buf;
    s32 var_a1;
    s32 temp;
    switch (mode) {
    case 3:
        n = M2C_FIELD(arg0, s8 *, 0x9F);
        var_s6 = (n * 3) + 2;
        out[0] = var_s6;
        out[1] = var_s6 + n;
        break;
    }
    temp = M2C_FIELD(arg0, s32 *, 0x10);
    if (temp >= 0) {
        base = &buf;
        var_a1 = temp;
        var_s6 = 0;
        do {
            out[var_a1] = M2C_FIELD(arg0, s32 *, 4) + var_s6;
            var_a1 += 0x20;
            var_s6 += 0xC;
        } while (var_a1 < 0x100);
    }
}
"""


def test_the_families_are_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(DRAFT, NAME, 'mips', labels=labels, limit=None)
    assert set(FAMILIES) <= set(labels.values())
    split = next(t for t in result if labels.get(t) == 'reused_counter_split_scope')
    assert '/*__repair_loop_weight*/do { var_s6_b1 = 0; } while (0);' in split
    # the loop-weight families work on a source that keeps m2c's own macros
    assert 'loop_weight_scope' in labels.values()
    for family in FAMILIES:
        assert family in draft_repairs.AGENT_FAMILIES
