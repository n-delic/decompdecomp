"""decomprepair 0.5.12 to 0.5.25's families reach the repairs the deep pass and autopilot try. Each comes from N64 functions
that used a whole 10,000-variant deep pass with no improvement; the library's own tests hold their shapes."""
from decompnet import seeds
from decompnet.app import draft_repairs

FAMILIES = ('assignment_at_use', 'comparison_sides', 'narrow_shift_pair', 'pointer_byte_step', 'returned_flag',
            'goto_search_loop', 'signed_division', 'stack_object_extent', 'stack_object_size', 'switch_range',
            'target_string_symbol', 'comma_flag_condition', 'compound_global_update',
            'conditional_ternary', 'duplicate_return_node', 'field_load_hoist', 'flag_returns', 'float_zero_local',
            'narrow_widening', 'narrowed_call_argument', 'reloaded_global', 'short_counter_loop', 'unknown32_float',
            # 0.5.14 and 0.5.15
            'goto_backward_loop', 'goto_forward_else', 'goto_shared_tail', 'float_unsigned_conversion',
            'target_call_arity', 'symbol_address_local', 'field_width', 'integer_pointer_sum', 'word_block_copy',
            'guard_inversion', 'stored_return_value',
            # 0.5.16
            'u16_counter_loop', 'float_prototype', 'switch_default_in_if',
            # 0.5.18
            'pointer_index_after_call', 'split_case_labels', 'comma_temporary_inline', 'deferred_stores',
            'global_pointer_local', 'loop_address_inline', 'do_constant_body', 'double_unsigned_conversion',
            'result_debris', 'saved_register_argument', 'stack_argument_record', 'double_padding_parameter',
            'call_into_arms', 'unknown_table_index',
            # 0.5.19
            'address_param_copy', 'compare_tree_switch', 'float_record_copy', 'leading_parameters', 'switch_case_order', 'negated_flag_return', 'early_base_local', 'hardware_register_store', 'used_before_set', 'varargs_signature', 'raw_store_width',
            # 0.5.20
            'halfword_sign', 'source_variable_reuse', 'target_call_order', 'wrapped_counter_step',
            # 0.5.21
            'counted_call_loop', 'unused_frame_local', 'unsigned_narrow_local', 'nested_guard', 'offset_base_local', 'parameter_mask_drop', 'parameter_mask_hoist', 'float_register_parameter', 'found_inside_loop', 'shared_step_loop', 'break_search_loop', 'stored_pointer_read',
            # 0.5.22
            'assignment_tree_switch', 'range_case_switch', 'register_carrier_argument', 'shared_constant_store', 'ternary_argument',
            # 0.5.23
            'busy_wait_volatile', 'global_record_copy',
            # 0.5.24
            'call_site_prototype', 'masked_flag_test', 'row_subscript', 'shared_product_local', 'stack_run_array',
            # 0.5.25
            'carried_store_arms', 'compared_load_inline', 'if_tree_switch',
            # 0.5.26 and 0.5.27
            'const_read',
            # 0.5.29
            'shared_return',
            # 0.5.31
            'read_before_assign', 'irregular_case',
            # 0.5.32
            'unsigned_switch', 'volatile_reload',
            # 0.5.35
            'pointer_walk_index')
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



def test_const_reads_come_first_when_the_target_loads_between_its_prologue_saves():
    # the signed remainder of the test above, read through a pointer: two families, in an order the target decides
    source = seeds.M2C_MACROS + '''
s32 g();
f32 f(f32 *arg0) {
    s32 temp_v0;
    s32 var_v0;

    temp_v0 = g();
    var_v0 = temp_v0 >> 0x10;
    if (temp_v0 < 0) {
        var_v0 = (s32) (temp_v0 + 0xFFFF) >> 0x10;
    }
    return (f32) (temp_v0 - (var_v0 << 0x10)) * 0.000015258789f * arg0[0];
}
'''
    early = ('glabel f\n/* 0 0 0 */  addiu      $sp, $sp, -0x28\n/* 4 4 0 */  sw         $s0, 0x14($sp)\n'
             '/* 8 8 0 */  lwc1       $fs0, 0x0($a0)\n/* C C 0 */  sw         $ra, 0x10($sp)\n')
    late = ('glabel f\n/* 0 0 0 */  addiu      $sp, $sp, -0x28\n/* 4 4 0 */  sw         $s0, 0x14($sp)\n'
            '/* 8 8 0 */  sw         $ra, 0x10($sp)\n/* C C 0 */  lwc1       $fs0, 0x0($a0)\n')
    for asm, first in ((early, True), (late, False)):
        labels = {}
        texts = draft_repairs.variants(source, 'f', 'mips', asm, FLAGS, labels, limit=None, header=HEADER)
        families = [labels.get(t) for t in texts]
        assert 'const_read' in families and 'signed_division' in families
        assert 'const f32 *arg0' in texts[families.index('const_read')]
        assert (families.index('const_read') < families.index('signed_division')) is first


def test_a_switch_tested_with_sltiu_is_offered_unsigned():
    # an N64 function 830 points off with m2c's signed operand was exact with `switch ((u32)arg1)`
    source = HEADER + '''typedef struct { char pad[0x58]; s32 flags; } S;
void f(S *arg0, s32 arg1) {
    s32 v = arg0->flags & ~0xF0;
    switch (arg1) {
    case 0: v |= 0x10; break;
    case 1: v |= 0x20; break;
    default: return;
    }
    arg0->flags = v;
}
'''
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None)
    assert any('switch ((u32)arg1)' in text and labels.get(text) == 'unsigned_switch' for text in out)


def test_a_cursor_walk_over_a_global_array_is_offered_as_an_indexed_loop():
    source = HEADER + '''extern void *D_A;
void g(void *);
void f(void) {
    unsigned long var_s2;
    void **var_s0;
    var_s2 = 0;
    var_s0 = &D_A;
    do {
        g(*var_s0);
        var_s2 += 1;
        var_s0 += 1;
    } while (var_s2 < 4U);
}
'''
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None)
    assert any('g(D_A[var_s2]);' in text and labels.get(text) == 'pointer_walk_index' for text in out)
