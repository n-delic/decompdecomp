"""decomprepair 0.5.12 and 0.5.13's families reach the repairs the deep pass and autopilot try. Each comes from N64 functions
that used a whole 10,000-variant deep pass with no improvement; the library's own tests hold their shapes."""
from decompnet import seeds
from decompnet.app import draft_repairs

FAMILIES = ('assignment_at_use', 'comparison_sides', 'narrow_shift_pair', 'pointer_byte_step', 'returned_flag',
            'goto_search_loop', 'signed_division', 'stack_object_extent', 'stack_object_size', 'switch_range',
            'target_string_symbol', 'comma_flag_condition', 'compound_global_update',
            'conditional_ternary', 'duplicate_return_node', 'field_load_hoist', 'flag_returns', 'float_zero_local',
            'narrow_widening', 'narrowed_call_argument', 'reloaded_global', 'short_counter_loop', 'unknown32_float')
FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef short s16;\ntypedef float f32;\n'


def test_every_family_is_known_to_the_agents_and_the_deep_pass():
    assert set(FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_a_family_is_offered_through_draft_repairs():
    # m2c's expansion of a signed remainder by 0x10000, back to `%`
    source = seeds.M2C_MACROS + '''
s32 g();
f32 f(f32 arg0) {
    s32 temp_v0;
    s32 var_v0;

    temp_v0 = g();
    var_v0 = temp_v0 >> 0x10;
    if (temp_v0 < 0) {
        var_v0 = (s32) (temp_v0 + 0xFFFF) >> 0x10;
    }
    return (f32) (temp_v0 - (var_v0 << 0x10)) * 0.000015258789f * arg0;
}
'''
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert any(labels.get(t) == 'signed_division' for t in texts), sorted(set(labels.values()))
