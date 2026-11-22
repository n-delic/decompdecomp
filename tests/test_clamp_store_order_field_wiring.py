"""decomprepair 0.5.563's clamp arm stores, adjacent array store order and global field load stage reach the draft
repair path: a 731-instruction KMC function stood at 1009 and went 979, 919, 619 with these three steps (each a
family now, the chain reproducible from the saved draft)."""
from decompnet.app import draft_repairs

NAME = 'build'
FAMILIES = ('clamp_arm_stores', 'adjacent_array_store_order', 'global_field_load_stage')

DRAFT = """\
void build(void *arg0, f32 f) {
    s16 spA58[3];
    f32 sp50[16];
    s32 temp_ft0;
    s32 var_v0;
    u32 var_a0;
    s32 temp_s1_2;
    temp_ft0 = (s32) (f * 4096.0f);
    var_v0 = -0xDC;
    if (((s16) temp_ft0 < -0xDC) || (var_v0 = 0xDC, (((s16) temp_ft0 < 0xDD) == 0))) {
        spA58[1] = var_v0;
    }
    spA58[1] = 0;
    spA58[2] = (u16) M2C_FIELD(arg0, s16 *, 0x17C);
    sp50[0] = M2C_FIELD(arg0, f32 *, 0) * 0.015625f;
    sp50[4] = M2C_FIELD(arg0, f32 *, 4) * 0.015625f;
    var_a0 = sPoolUsed + 1;
    sPoolUsed = var_a0;
    temp_s1_2 = M2C_FIELD(sRenderState, s32 *, 0x74C) + (var_a0 << 6);
    emit(sp50, spA58, temp_s1_2);
}
"""


def test_the_three_steps_are_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(DRAFT, NAME, 'mips', labels=labels, limit=None)
    assert set(FAMILIES) <= set(labels.values())
    clamp = next(t for t in result if labels.get(t) == 'clamp_arm_stores')
    assert 'var_v0 = 0xDC' not in clamp and 'spA58[1] = -0xDC;' in clamp
    stage = next(t for t in result if labels.get(t) == 'global_field_load_stage')
    assert 'temp_s1_2 = __repair_field_base + (var_a0 << 6);' in stage
    for family in FAMILIES:
        assert family in draft_repairs.AGENT_FAMILIES
