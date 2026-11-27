"""Draft proposals for an m2c draft also come from its written-out spelling (decomprepair.m2c_spelling)."""
from decompnet.app import draft_repairs

DRAFT = """\
#ifndef M2C_DEFINES
#define M2C_DEFINES
typedef s32 M2C_UNK;
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))
#endif

M2C_UNK func_80001000(void *arg0, s32 arg1) {
    s32 temp_v0;

    temp_v0 = M2C_FIELD(arg0, s32 *, 0x10) + arg1;
    M2C_FIELD(arg0, s32 *, 0x14) = temp_v0;
    return temp_v0;
}
"""


def test_raw_proposals_come_first_and_written_out_ones_follow():
    labels = {}
    raw = draft_repairs._spelled(DRAFT, 'func_80001000', 'mips', '', (), {}, limit=None)
    both = draft_repairs.variants(DRAFT, 'func_80001000', 'mips', labels=labels, limit=None)
    assert both[:len(raw)] == raw
    extra = both[len(raw):]
    assert extra and all('M2C_FIELD(arg0' not in text for text in extra)
    assert all(text in labels for text in both)
    # a limited pass fills with the raw spelling's own limited proposals first
    limited = draft_repairs._spelled(DRAFT, 'func_80001000', 'mips', '', (), {}, limit=len(raw))
    assert draft_repairs.variants(DRAFT, 'func_80001000', 'mips', limit=len(raw))[:len(limited)] == limited
