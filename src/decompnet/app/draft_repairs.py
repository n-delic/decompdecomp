"""Small deterministic hypotheses before the random permuter's near-miss cutoff.

These are candidate rewrites, not equivalence proofs. Only the ordinary compiler and exact judge may keep
one. Work targets the requested definition and its candidate-owned declarations; strings, comments and
preprocessor lines are opaque.
"""

import re
import threading

from decomprepair import (
    address_association_repairs,
    address_param_copy_repairs,
    affine_operand_repairs,
    allocation_repairs,
    allocator_shape_repairs,
    arm_pointer_split_repairs,
    assignment_tree_switch_repairs,
    assignment_use_repairs,
    bit_predicate_repairs,
    bit_role_repairs,
    block_accumulator_repairs,
    branch_layout_repairs,
    branch_pointer_repairs,
    branch_scalar_repairs,
    branch_tail_distribution,
    buffer_address_repairs,
    busy_wait_repairs,
    byte_address_inline_repairs,
    byte_increment_repairs,
    call_assignment_repairs,
    call_count_repairs,
    call_index_split_repairs,
    call_index_staging_repairs,
    call_initialization_repairs,
    call_lifetime_repairs,
    call_preparation_repairs,
    call_scope_repairs,
    call_site_prototype_repairs,
    callee_prototype_repairs,
    captured_control_repairs,
    captured_index_repairs,
    captured_repairs,
    carried_store_arms_repairs,
    case_split_repairs,
    case_temporary_repairs,
    clamp_barrier_repairs,
    comma_flag_condition_repairs,
    comma_temporary_repairs,
    compare_operand_stage,
    compare_tree_switch_repairs,
    compared_load_inline_repairs,
    comparison_sides_repairs,
    compiler_phase_repairs,
    compiler_shape_repairs,
    component_index_repairs,
    compound_global_update_repairs,
    condition_table_anchor,
    conditional_ternary_repairs,
    const_read_repairs,
    constant_exit_return_repairs,
    constant_pointer_repairs,
    conversion_scheduling_repairs,
    copy_lifetime_repairs,
    count_lifetime_repairs,
    countdown_guard_repairs,
    counted_call_loop_repairs,
    counter_companion_repairs,
    counter_loop_repairs,
    dead_local_stage_repairs,
    debug_boundary_repairs,
    debug_initializer_repairs,
    decision_address_repairs,
    decoded_byte_repairs,
    deferred_store_repairs,
    delay_slot_duplicate_repairs,
    duplicate_return_node_repairs,
    entry_parameter_copy_repairs,
    equal_value_flow,
    evidence_repairs,
    example_lifetime_repairs,
    expression_repairs,
    field_load_hoist_repairs,
    field_order_repairs,
    field_struct_view_repairs,
    field_width_repairs,
    flag_returns_repairs,
    float_bit_argument_repairs,
    float_intermediate_repairs,
    float_prototype_repairs,
    float_record_copy_repairs,
    float_zero_local_repairs,
    focused_lifetime_repairs,
    formatter_repairs,
    frame_padding_repairs,
    global_array_repairs,
    global_identity_repairs,
    global_pointer_local_repairs,
    global_pointer_run_repairs,
    global_record_copy_repairs,
    global_store_repairs,
    goto_loop_repairs,
    goto_structure_repairs,
    goto_tail_order_repairs,
    guard_inversion_repairs,
    guard_shape_repairs,
    guarded_call_repairs,
    guarded_do_for_repairs,
    halfword_sign_repairs,
    hardware_operation_repairs,
    identity_lifetime_repairs,
    if_tree_switch_repairs,
    increment_expression_repairs,
    inferred_layout_repairs,
    init_placement_repairs,
    inline_scheduling_repairs,
    integer_constant_repairs,
    integer_lifetime_repairs,
    integer_sum_repairs,
    irregular_case_repairs,
    join_copy_sink_repairs,
    join_store_split_repairs,
    known_bit_repairs,
    later_local_repairs,
    leading_parameter_repairs,
    leaf_frame_repairs,
    lifetime_repairs,
    live_return_address_repairs,
    load_pair_repairs,
    load_store_order_repairs,
    loaded_byte_repairs,
    local_inline_repairs,
    local_widening_repairs,
    local_width_repairs,
    loop_address_inline_repairs,
    loop_goto_form_repairs,
    loop_guard_storage_repairs,
    loop_index_inline_repairs,
    loop_param_reuse_repairs,
    loop_sentinel_repairs,
    loop_shape_repairs,
    loop_tail_update_order_repairs,
    loop_top_label_repairs,
    loop_weight_repairs,
    macro_address_repairs,
    macro_repairs,
    macro_scope_weight,
    masked_counter_repairs,
    masked_flag_test_repairs,
    masked_range_equalities,
    masked_reuse_repairs,
    match_followup_repairs,
    match_shape_repairs,
    matched_prototype_repairs,
    mips_float_local_reuse,
    mmio_repairs,
    narrow_call_repairs,
    narrow_expression_repairs,
    narrow_shift_repairs,
    narrow_store_repairs,
    narrow_widening_repairs,
    narrowed_call_argument_repairs,
    near_match_repairs,
    near_repairs,
    nested_guard_repairs,
    normalization_repairs,
    observed_repairs,
    offset_base_local_repairs,
    operand_order_repairs,
    opposing_float_bounds_repairs,
    out_of_line_guard_repairs,
    param_address_local_repairs,
    param_mask_repairs,
    parameter_copy_drop_repairs,
    parameter_mask_drop_repairs,
    parameter_shape_repairs,
    passthrough_argument_repairs,
    pattern_repairs,
    phantom_local_repairs,
    phase_lifetime_repairs,
    phase_union_repairs,
    pointer_capture_repairs,
    pointer_cast_repairs,
    pointer_pair_repairs,
    pointer_result_repairs,
    pointer_scale_repairs,
    pointer_walk_index_repairs,
    post_call_capture_repairs,
    preheader_order_repairs,
    prelude_pointer_inline_repairs,
    prepared_call_repairs,
    product_operand_reuse_repairs,
    product_temporary_repairs,
    queue_cursor_index_repairs,
    range_case_switch_repairs,
    raw_address_repairs,
    read_before_assign_repairs,
    record_lifetime_recovery,
    record_lifetime_repairs,
    record_slot_repairs,
    record_store_bundles,
    register_carrier_repairs,
    register_repairs,
    register_reuse_repairs,
    reloaded_global_repairs,
    result_debris_repairs,
    returned_flag_repairs,
    row_subscript_repairs,
    saved_register_argument_repairs,
    scalar_storage_reuse,
    scaled_size_inline_repairs,
    scheduling_repairs,
    scope_topology_repairs,
    sdk_io_repairs,
    sdk_shape_repairs,
    search_exit_loop_repairs,
    search_loop_repairs,
    shared_constant_store_repairs,
    shared_offset_repairs,
    shared_product_repairs,
    shared_return_repairs,
    short_counter_loop_repairs,
    signed_division_repairs,
    slot_helper_repairs,
    source_variable_reuse_repairs,
    stack_extent_repairs,
    stack_object_size_repairs,
    stack_record_repairs,
    stack_repairs,
    stack_run_array_repairs,
    stack_unpack_loop_repairs,
    storage_scope_repairs,
    store_address_repairs,
    store_expression_forms,
    store_result_repairs,
    store_value_first_repairs,
    stored_float_zero_repairs,
    stored_pointer_read_repairs,
    stored_return_value_repairs,
    structure_return_repairs,
    switch_case_order_repairs,
    switch_default_if_repairs,
    switch_default_tail_repairs,
    switch_range_repairs,
    switch_variable_split_repairs,
    symbol_base_repairs,
    symbol_width_repairs,
    tail_call_arm_repairs,
    tail_shape_repairs,
    target_call_arity_repairs,
    target_call_constant_repairs,
    target_call_order_repairs,
    target_constant_repairs,
    target_local_repairs,
    target_string_repairs,
    terminal_guard_repairs,
    ternary_argument_repairs,
    unknown32_float_repairs,
    unknown_symbol_offset_repairs,
    unknown_table_index_repairs,
    unsigned_conversion_repairs,
    unsigned_shift_repairs,
    unsigned_switch_repairs,
    value_range_repairs,
    varargs_signature_repairs,
    volatile_reload_repairs,
    width_promotion_repairs,
    word_block_copy_repairs,
    wrapped_counter_repairs,
    zero_argument_repairs,
    zero_chain_repairs,
    zero_test_repairs,
)

from .. import compile_timing, m2cfix, seeds
from ..forks.csource import _blank, find
from . import (
    branch_locals,
    capture_followup_repairs,
    captured_lifetimes,
    carrier_sequence_repairs,
    copy_shape_repairs,
    entry_point_repairs,
    float_storage_repairs,
    helper_expansion,
    initial_draft_repairs,
    initializer_clusters,
    loop_storage_shapes,
    narrow_load_subtraction,
    parameter_carriers,
    pointer_index_scope,
    pointer_snapshot_repairs,
    promoted_entry_repairs,
    recent_capture_repairs,
    record_layout_search,
    repair_cleanup,
    return_contract_repairs,
    scan_load_repairs,
    scratch_role_repairs,
    snapshot_branch_repairs,
    stack_padding_repairs,
    switch_constant_dispatch,
    switch_tables,
)
from .repair_generation import call as _generated

AGENT_FAMILIES = mips_float_local_reuse.FAMILIES + (opposing_float_bounds_repairs.FAMILY, stored_float_zero_repairs.FAMILY, loop_sentinel_repairs.FAMILY, target_call_constant_repairs.FAMILY, out_of_line_guard_repairs.FAMILY, record_layout_search.FAMILY,) + (initial_draft_repairs.FRAME_FAMILY,) + unknown_symbol_offset_repairs.FAMILIES + masked_counter_repairs.FAMILIES + delay_slot_duplicate_repairs.FAMILIES + constant_exit_return_repairs.FAMILIES + target_constant_repairs.FAMILIES + const_read_repairs.FAMILIES + shared_return_repairs.FAMILIES + read_before_assign_repairs.FAMILIES + irregular_case_repairs.FAMILIES + unsigned_switch_repairs.FAMILIES + volatile_reload_repairs.FAMILIES + pointer_walk_index_repairs.FAMILIES + queue_cursor_index_repairs.FAMILIES + guarded_do_for_repairs.FAMILIES + parameter_copy_drop_repairs.FAMILIES + load_store_order_repairs.FAMILIES + passthrough_argument_repairs.FAMILIES + goto_loop_repairs.FAMILIES + carried_store_arms_repairs.FAMILIES + compared_load_inline_repairs.FAMILIES + if_tree_switch_repairs.FAMILIES + call_site_prototype_repairs.FAMILIES + masked_flag_test_repairs.FAMILIES + masked_range_equalities.FAMILIES + row_subscript_repairs.FAMILIES + shared_product_repairs.FAMILIES + store_expression_forms.FAMILIES + stack_run_array_repairs.FAMILIES + busy_wait_repairs.FAMILIES + global_record_copy_repairs.FAMILIES + assignment_tree_switch_repairs.FAMILIES + range_case_switch_repairs.FAMILIES + register_carrier_repairs.FAMILIES + shared_constant_store_repairs.FAMILIES + ternary_argument_repairs.FAMILIES + counted_call_loop_repairs.FAMILIES + leaf_frame_repairs.FAMILIES + nested_guard_repairs.FAMILIES + offset_base_local_repairs.FAMILIES + parameter_mask_drop_repairs.FAMILIES + parameter_shape_repairs.FAMILIES + search_exit_loop_repairs.FAMILIES + stored_pointer_read_repairs.FAMILIES + halfword_sign_repairs.FAMILIES + source_variable_reuse_repairs.FAMILIES + goto_tail_order_repairs.FAMILIES + prelude_pointer_inline_repairs.FAMILIES + switch_variable_split_repairs.FAMILIES + target_call_order_repairs.FAMILIES + wrapped_counter_repairs.FAMILIES + address_param_copy_repairs.FAMILIES + compare_tree_switch_repairs.FAMILIES + float_record_copy_repairs.FAMILIES + leading_parameter_repairs.FAMILIES + switch_case_order_repairs.FAMILIES + tail_shape_repairs.FAMILIES + varargs_signature_repairs.FAMILIES + call_index_split_repairs.FAMILIES + case_split_repairs.FAMILIES + comma_temporary_repairs.FAMILIES + deferred_store_repairs.FAMILIES + global_pointer_local_repairs.FAMILIES + loop_address_inline_repairs.FAMILIES + loop_shape_repairs.FAMILIES + result_debris_repairs.FAMILIES + saved_register_argument_repairs.FAMILIES + stack_record_repairs.FAMILIES + tail_call_arm_repairs.FAMILIES + unknown_table_index_repairs.FAMILIES + counter_loop_repairs.FAMILIES + float_prototype_repairs.FAMILIES + switch_default_if_repairs.FAMILIES + switch_default_tail_repairs.FAMILIES + field_width_repairs.FAMILIES + goto_structure_repairs.FAMILIES + loop_goto_form_repairs.FAMILIES + symbol_base_repairs.FAMILIES + target_call_arity_repairs.FAMILIES + unsigned_conversion_repairs.FAMILIES + guard_inversion_repairs.FAMILIES + integer_sum_repairs.FAMILIES + stored_return_value_repairs.FAMILIES + word_block_copy_repairs.FAMILIES + comma_flag_condition_repairs.FAMILIES + compound_global_update_repairs.FAMILIES + conditional_ternary_repairs.FAMILIES + duplicate_return_node_repairs.FAMILIES + join_copy_sink_repairs.FAMILIES + join_store_split_repairs.FAMILIES + arm_pointer_split_repairs.FAMILIES + param_address_local_repairs.FAMILIES + slot_helper_repairs.FAMILIES + field_load_hoist_repairs.FAMILIES + flag_returns_repairs.FAMILIES + float_zero_local_repairs.FAMILIES + narrow_widening_repairs.FAMILIES + narrowed_call_argument_repairs.FAMILIES + reloaded_global_repairs.FAMILIES + short_counter_loop_repairs.FAMILIES + unknown32_float_repairs.FAMILIES + assignment_use_repairs.FAMILIES + comparison_sides_repairs.FAMILIES + narrow_shift_repairs.FAMILIES + pointer_scale_repairs.FAMILIES + returned_flag_repairs.FAMILIES + search_loop_repairs.FAMILIES + signed_division_repairs.FAMILIES + stack_extent_repairs.FAMILIES + stack_unpack_loop_repairs.FAMILIES + stack_object_size_repairs.FAMILIES + switch_range_repairs.FAMILIES + target_string_repairs.FAMILIES + symbol_width_repairs.FAMILIES + call_index_staging_repairs.FAMILIES + guard_shape_repairs.FAMILIES + global_array_repairs.FAMILIES + local_inline_repairs.FAMILIES + product_temporary_repairs.FAMILIES + loop_param_reuse_repairs.FAMILIES + scaled_size_inline_repairs.FAMILIES + clamp_barrier_repairs.FAMILIES + loop_index_inline_repairs.FAMILIES + frame_padding_repairs.FAMILIES + field_struct_view_repairs.FAMILIES + entry_parameter_copy_repairs.FAMILIES + store_value_first_repairs.FAMILIES + init_placement_repairs.FAMILIES + param_mask_repairs.FAMILIES + width_promotion_repairs.FAMILIES + record_slot_repairs.FAMILIES + bit_predicate_repairs.FAMILIES + scope_topology_repairs.FAMILIES + (bit_role_repairs.FAMILY,) + (carrier_sequence_repairs.FAMILY,) + compiler_phase_repairs.FAMILIES + snapshot_branch_repairs.FAMILIES + branch_tail_distribution.FAMILIES + call_preparation_repairs.FAMILIES + repair_cleanup.FAMILIES + record_lifetime_recovery.FAMILIES + pointer_result_repairs.FAMILIES + record_store_bundles.FAMILIES + narrow_store_repairs.FAMILIES + scratch_role_repairs.FAMILIES + parameter_carriers.FAMILIES + narrow_load_subtraction.FAMILIES + integer_constant_repairs.FAMILIES + loop_storage_shapes.FAMILIES + scalar_storage_reuse.FAMILIES + (switch_constant_dispatch.FAMILY, guarded_call_repairs.FAMILY, decoded_byte_repairs.FAMILY, zero_chain_repairs.FAMILY, component_index_repairs.FAMILY, unsigned_shift_repairs.FAMILY, preheader_order_repairs.FAMILY, terminal_guard_repairs.FAMILY, promoted_entry_repairs.FAMILY,) + zero_argument_repairs.FAMILIES + (matched_prototype_repairs.FAMILY, countdown_guard_repairs.FAMILY,) + loaded_byte_repairs.FAMILIES + pointer_capture_repairs.FAMILIES + scan_load_repairs.FAMILIES + byte_address_inline_repairs.FAMILIES + byte_increment_repairs.FAMILIES + loop_tail_update_order_repairs.FAMILIES + loop_top_label_repairs.FAMILIES + product_operand_reuse_repairs.FAMILIES + dead_local_stage_repairs.FAMILIES + counter_companion_repairs.FAMILIES + global_pointer_run_repairs.FAMILIES + (known_bit_repairs.FAMILY, post_call_capture_repairs.FAMILY) + loop_guard_storage_repairs.FAMILIES + macro_address_repairs.FAMILIES + live_return_address_repairs.FAMILIES + copy_lifetime_repairs.FAMILIES + (debug_boundary_repairs.FAMILY,) + conversion_scheduling_repairs.FAMILIES + (hardware_operation_repairs.FAMILY,) + (return_contract_repairs.FAMILY, float_storage_repairs.FAMILY, narrow_expression_repairs.FAMILY) + narrow_call_repairs.FAMILIES + (branch_scalar_repairs.FAMILY, call_count_repairs.FAMILY, pointer_snapshot_repairs.FAMILY,) + call_assignment_repairs.FAMILIES + constant_pointer_repairs.FAMILIES + (shared_offset_repairs.FAMILY, 'branch_store_tail', 'common_scalar_tail',) + later_local_repairs.FAMILIES + call_scope_repairs.FAMILIES + prepared_call_repairs.FAMILIES + debug_initializer_repairs.FAMILIES + phase_union_repairs.FAMILIES + captured_index_repairs.FAMILIES + recent_capture_repairs.FAMILIES + (identity_lifetime_repairs.FAMILY, 'typed_global_identity', 'block_accumulator_lifetime', pointer_index_scope.FAMILY,) + copy_shape_repairs.FAMILIES + focused_lifetime_repairs.FAMILIES + (store_result_repairs.FAMILY,) + equal_value_flow.FAMILIES + example_lifetime_repairs.FAMILIES + affine_operand_repairs.FAMILIES + loop_weight_repairs.FAMILIES + macro_scope_weight.FAMILIES + condition_table_anchor.FAMILIES + compare_operand_stage.FAMILIES + branch_pointer_repairs.FAMILIES + capture_followup_repairs.FAMILIES + ('paired_load_order',) + allocation_repairs.FAMILIES + case_temporary_repairs.FAMILIES + phantom_local_repairs.FAMILIES + value_range_repairs.FAMILIES + count_lifetime_repairs.FAMILIES + helper_expansion.FAMILIES + record_lifetime_repairs.FAMILIES + raw_address_repairs.FAMILIES + zero_test_repairs.FAMILIES + pointer_pair_repairs.FAMILIES + field_order_repairs.FAMILIES + match_shape_repairs.FAMILIES + increment_expression_repairs.FAMILIES + allocator_shape_repairs.FAMILIES + buffer_address_repairs.FAMILIES + normalization_repairs.FAMILIES + call_lifetime_repairs.FAMILIES + sdk_io_repairs.FAMILIES + target_local_repairs.FAMILIES + formatter_repairs.FAMILIES + ('target_switch_order',) + inline_scheduling_repairs.FAMILIES + ('runtime_scalar_signature', 'shared_return_tail', 'direct_compound_store', 'redundant_loop_guard', 'float_argument_bits', 'arithmetic_operand_order', 'call_forwarded_arguments', 'small_copy', 'typed_record_address', 'one_shot_scope', 'one_shot_unwrap',
                  'pointer_block_scope', 'array_address_share', 'combined_integer_lifetimes', 'inferred_record_layout',
                  'float_product_intermediate','pointer_chase_reuse','independent_assignment_order',
                  'terminal_early_return','member_store_replay','scalar_global_address','split_mask_stores',
                  'split_integer_phase','paired_integer_lifetimes','initialization_cluster')

LIMIT = 20
_IDENT = r'[A-Za-z_]\w*'
_MEMBER = rf'{_IDENT}(?:\s*(?:->|\.)\s*{_IDENT})*'


_IDLE_WORKERS = []
_IDLE_LOCK = threading.Lock()
_WORKER_SLOTS = threading.BoundedSemaphore(4)
IDLE_WORKER_SECONDS = 300  # a kept worker unused this long is closed at the next request (bounded either way)


class _ProposalDeadline(TimeoutError):
    pass


def _worker_batch(request, check, handlers=None):
    import time

    from . import library_skew
    from .deep_proposals import Worker

    library_skew.ensure()
    check()
    while not _WORKER_SLOTS.acquire(timeout=0.05):
        check()
    try:
        now = time.monotonic()
        with _IDLE_LOCK:
            stale = [worker for worker in _IDLE_WORKERS
                     if now - worker.idle_since > IDLE_WORKER_SECONDS or getattr(worker, 'epoch', 0) != library_skew.EPOCH]
            _IDLE_WORKERS[:] = [worker for worker in _IDLE_WORKERS if worker not in stale]
            worker = _IDLE_WORKERS.pop() if _IDLE_WORKERS else None
        for expired in stale:
            expired.close()
        if worker is None or worker.closed.is_set():
            worker = Worker()
            worker.epoch = library_skew.EPOCH
        result = worker.run(request, check, handlers)
        with _IDLE_LOCK:
            worker.idle_since = time.monotonic()
            if getattr(worker, 'epoch', 0) == library_skew.EPOCH:
                _IDLE_WORKERS.append(worker)
            else:
                worker.close()  # started before the library was reloaded
        return result
    finally:
        _WORKER_SLOTS.release()


def _in_worker(source, name, arch, asm, flags, labels, kwargs):
    """Keep parsers and rewrite families outside the server's interpreter."""
    import hashlib
    import json
    import time

    from ..network import NetworkError
    from . import repair_generation
    from .purpose_work import checkpoint

    state = repair_generation.ACTIVE.get()
    now = time.monotonic()
    if state and repair_generation.exhausted():
        return []
    deadline = now + max(30, 2 * repair_generation.FAMILY_SECONDS)
    if state:
        if state.deadline is not None:
            # Give the child time to return partial results after its cooperative
            # deadline. A stuck child can still be stopped by the parent.
            deadline = min(deadline, state.deadline + 0.25)
        kwargs['_generation'] = {
            'scope': hashlib.sha256(repr(state.scope).encode()).hexdigest(),
            'families': sorted(state.families),
            'deadline': state.deadline,
        }
    try:
        request = json.dumps([[source, name, arch, asm, list(flags)], kwargs], ensure_ascii=False).encode()
    except (TypeError, ValueError) as error:
        raise NetworkError("Could not send the repair inputs to the proposal worker.") from error

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise _ProposalDeadline

    try:
        result = _worker_batch(request, check)
    except _ProposalDeadline:
        if state:
            state.incomplete = True
            return []
        raise NetworkError("The repair proposal worker took too long. Retry the repair or choose a smaller search.") from None
    except OSError as error:
        raise NetworkError("Could not run the repair proposal worker. Check the app installation and retry.") from error

    choices = []
    for text, family in result['candidates']:
        choices.append(text)
        if family and labels is not None:
            labels[text] = family
    if state:
        state.incomplete |= result['incomplete']
        for key in ('calls', 'generated_stages', 'cache_hits', 'generation_ms'):
            state.metrics[key] += result['generation'][key]
        repair_generation.count(state, ((family, text) for text, family in result['candidates']))
    return choices


def variants(source: str, name: str, arch: str, asm: str = '', flags=(), labels=None, *, limit=LIMIT, header='', tables=(), callees='', entry_scope=None) -> list[str]:
    import asyncio

    from ..network import NetworkError

    # Server entry points dispatch work to request workers. Synchronous pipe and
    # process waits must stay off the HTTP event loop even if a new caller forgets.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise NetworkError("Run repair proposal generation outside the server event loop.")
    if threading.current_thread() is not threading.main_thread():
        kwargs = dict(limit=limit, header=header, tables=tables, callees=callees, entry_scope=entry_scope)
        return _in_worker(source, name, arch, asm, flags, labels, kwargs)
    return _variants(source, name, arch, asm, flags, labels, limit=limit, header=header, tables=tables,
                     callees=callees, entry_scope=entry_scope)


@compile_timing.timed('proposal_generation')
def _variants(source: str, name: str, arch: str, asm: str = '', flags=(), labels=None, *, limit=LIMIT, header='', tables=(), callees='', entry_scope=None) -> list[str]:
    span = find(source, name)
    if span is None:
        return []
    start, end = span
    clean = _blank(source)
    opening = clean.find('{', start, end)
    if opening < 0:
        return []
    # The dedicated path preserves directives and never rewrites their bodies.
    semantic=(_generated(snapshot_branch_repairs.variants,source,name,header,arch)
              + _generated(known_bit_repairs.variants,source,name,header,arch)
              + _generated(post_call_capture_repairs.variants,source,name,header,arch))
    semantic += _generated(switch_default_tail_repairs.variants, source, name)
    scoped = []
    if arch == 'mips':
        scoped = (_generated(loop_sentinel_repairs.variants, source, name, header, arch, asm)
                  + _generated(target_call_constant_repairs.variants, source, name, asm)
                  + _generated(out_of_line_guard_repairs.variants, source, name, header)
                  + _generated(mips_float_local_reuse.variants, source, name)
                  + _generated(stored_float_zero_repairs.variants, source, name, asm)
                  + _generated(opposing_float_bounds_repairs.variants, source, name, asm))
    early=scoped+_generated(switch_constant_dispatch.variants,source,name,header,**(entry_scope or {})) + _generated(guarded_call_repairs.variants,source,name,header,arch) + _generated(decoded_byte_repairs.variants,source,name,header,arch,**(entry_scope or {})) + _generated(zero_chain_repairs.variants,source,name,header,**(entry_scope or {})) + _generated(component_index_repairs.variants,source,name,header,**(entry_scope or {})) + _generated(unsigned_shift_repairs.variants,source,name,header,**(entry_scope or {})) + _generated(preheader_order_repairs.variants,source,name,header,**(entry_scope or {})) + _generated(terminal_guard_repairs.variants,source,name,header) + _generated(promoted_entry_repairs.variants,source,name,header,**(entry_scope or {})) + _generated(zero_argument_repairs.variants,source,name,header,callees) + _generated(float_bit_argument_repairs.variants,source,name,header,arch,flags) + _generated(unknown_symbol_offset_repairs.variants,source,name,header) + _generated(masked_counter_repairs.variants,source,name) + _generated(initial_draft_repairs.frame_variants,source,name,asm,arch) + _generated(delay_slot_duplicate_repairs.variants,source,name) + _generated(constant_exit_return_repairs.variants,source,name) + _generated(target_constant_repairs.variants,source,name,asm) + _generated(load_store_order_repairs.variants,source,name) + _generated(passthrough_argument_repairs.variants,source,name,asm=asm,arch=arch,header=header,callees=callees,require_evidence=True) + _generated(goto_loop_repairs.variants,source,name) + _generated(carried_store_arms_repairs.variants,source,name) + _generated(compared_load_inline_repairs.variants,source,name) + _generated(if_tree_switch_repairs.variants,source,name) + _generated(call_site_prototype_repairs.variants,source,name) + _generated(masked_flag_test_repairs.variants,source,name) + _generated(masked_range_equalities.variants,source,name) + _generated(row_subscript_repairs.variants,source,name) + _generated(shared_product_repairs.variants,source,name) + _generated(store_expression_forms.variants,source,name) + _generated(stack_run_array_repairs.variants,source,name,asm) + _generated(busy_wait_repairs.variants,source,name) + _generated(global_record_copy_repairs.variants,source,name,asm) + _generated(assignment_tree_switch_repairs.variants,source,name) + _generated(range_case_switch_repairs.variants,source,name) + _generated(register_carrier_repairs.variants,source,name) + _generated(shared_constant_store_repairs.variants,source,name) + _generated(ternary_argument_repairs.variants,source,name) + _generated(counted_call_loop_repairs.variants,source,name) + _generated(leaf_frame_repairs.variants,source,name,asm) + _generated(nested_guard_repairs.variants,source,name) + _generated(offset_base_local_repairs.variants,source,name) + _generated(parameter_mask_drop_repairs.variants,source,name,asm) + _generated(parameter_shape_repairs.variants,source,name,asm) + _generated(search_exit_loop_repairs.variants,source,name) + _generated(stored_pointer_read_repairs.variants,source,name) + _generated(halfword_sign_repairs.variants,source,name) + _generated(source_variable_reuse_repairs.variants,source,name) + _generated(goto_tail_order_repairs.variants,source,name) + _generated(prelude_pointer_inline_repairs.variants,source,name) + _generated(switch_variable_split_repairs.variants,source,name) + _generated(target_call_order_repairs.variants,source,name,asm) + _generated(wrapped_counter_repairs.variants,source,name) + _generated(address_param_copy_repairs.variants,source,name) + _generated(compare_tree_switch_repairs.variants,source,name) + _generated(float_record_copy_repairs.variants,source,name,asm) + _generated(leading_parameter_repairs.variants,source,name,asm) + _generated(switch_case_order_repairs.variants,source,name) + _generated(tail_shape_repairs.variants,source,name) + _generated(varargs_signature_repairs.variants,source,name,asm) + _generated(call_index_split_repairs.variants,source,name) + _generated(case_split_repairs.variants,source,name) + _generated(comma_temporary_repairs.variants,source,name) + _generated(deferred_store_repairs.variants,source,name) + _generated(global_pointer_local_repairs.variants,source,name) + _generated(loop_address_inline_repairs.variants,source,name) + _generated(loop_shape_repairs.variants,source,name) + _generated(result_debris_repairs.variants,source,name) + _generated(saved_register_argument_repairs.variants,source,name,asm) + _generated(stack_record_repairs.variants,source,name) + _generated(tail_call_arm_repairs.variants,source,name) + _generated(unknown_table_index_repairs.variants,source,name,asm) + _generated(counter_loop_repairs.variants,source,name) + _generated(float_prototype_repairs.variants,source,name) + _generated(switch_default_if_repairs.variants,source,name) + _generated(field_width_repairs.variants,source,name,asm) + _generated(goto_structure_repairs.variants,source,name) + _generated(loop_goto_form_repairs.variants,source,name) + _generated(symbol_base_repairs.variants,source,name) + _generated(target_call_arity_repairs.variants,source,name,asm) + _generated(unsigned_conversion_repairs.variants,source,name) + _generated(guard_inversion_repairs.variants,source,name) + _generated(integer_sum_repairs.variants,source,name) + _generated(stored_return_value_repairs.variants,source,name,asm) + _generated(word_block_copy_repairs.variants,source,name) + _generated(comma_flag_condition_repairs.variants,source,name) + _generated(compound_global_update_repairs.variants,source,name) + _generated(conditional_ternary_repairs.variants,source,name) + _generated(duplicate_return_node_repairs.variants,source,name) + _generated(join_copy_sink_repairs.variants,source,name) + _generated(join_store_split_repairs.variants,source,name) + _generated(arm_pointer_split_repairs.variants,source,name) + _generated(param_address_local_repairs.variants,source,name) + _generated(slot_helper_repairs.variants,source,name) + _generated(field_load_hoist_repairs.variants,source,name) + _generated(flag_returns_repairs.variants,source,name) + _generated(float_zero_local_repairs.variants,source,name,asm) + _generated(narrow_widening_repairs.variants,source,name) + _generated(narrowed_call_argument_repairs.variants,source,name) + _generated(reloaded_global_repairs.variants,source,name,asm) + _generated(short_counter_loop_repairs.variants,source,name) + _generated(unknown32_float_repairs.variants,source,name,asm) + _generated(assignment_use_repairs.variants,source,name) + _generated(comparison_sides_repairs.variants,source,name) + _generated(narrow_shift_repairs.variants,source,name) + _generated(pointer_scale_repairs.variants,source,name,asm) + _generated(returned_flag_repairs.variants,source,name) + _generated(search_loop_repairs.variants,source,name) + _generated(signed_division_repairs.variants,source,name) + _generated(stack_extent_repairs.variants,source,name,asm) + _generated(stack_unpack_loop_repairs.variants,source,name,asm) + _generated(stack_object_size_repairs.variants,source,name,asm) + _generated(switch_range_repairs.variants,source,name,asm) + _generated(target_string_repairs.variants,source,name,asm) + _generated(symbol_width_repairs.variants,source,name,asm) + _generated(call_index_staging_repairs.variants,source,name) + _generated(matched_prototype_repairs.variants,source,name,header,callees) + _generated(scan_load_repairs.variants,source,name,header,arch) + _generated(byte_address_inline_repairs.variants,source,name,flags,header,arch) + _generated(byte_increment_repairs.variants,source,name,header,arch) + _generated(loop_tail_update_order_repairs.variants,source,name,header) + _generated(loop_top_label_repairs.variants,source,name) + _generated(product_operand_reuse_repairs.variants,source,name) + _generated(dead_local_stage_repairs.variants,source,name) + _generated(counter_companion_repairs.variants,source,name) + _generated(global_pointer_run_repairs.variants,source,name) + semantic + _generated(phase_union_repairs.array_variants,source,name,arch,flags,header) + _generated(macro_address_repairs.variants,source,name,arch,flags,header) + _generated(live_return_address_repairs.variants,source,name,arch,asm,header) + _generated(copy_lifetime_repairs.variants,source,name,header) + _generated(debug_boundary_repairs.variants,source,name,flags,header,limit=None) + _generated(conversion_scheduling_repairs.variants,source,name,header,arch,asm,flags) + _generated(hardware_operation_repairs.variants,source,name,arch,asm) + _generated(shared_offset_repairs.variants,source,name,header) + _generated(branch_layout_repairs.store_tails,source,name,header) + _generated(branch_layout_repairs.scalar_tails,source,name,header) + _generated(captured_index_repairs.variants,source,name,header) + _generated(decision_address_repairs.variants, source,name,arch,header) + _generated(recent_capture_repairs.variants,source,name,arch,asm,header,callees)
    if '#' in clean[opening:end]:
        def generate(masked):
            inner_labels = {}
            safe_header = macro_repairs.prepare(header)[0] or ''
            choices = variants(masked,name,arch,asm,flags,inner_labels,limit=None,header=safe_header,tables=tables,callees=callees,entry_scope=None)
            return [(inner_labels[text],text) for text in choices]
        choices = early + _generated(macro_repairs.variants, source,name,generate,header)
        if labels is not None:labels.update({text:family for family,text in choices})
        return [text for _,text in choices][:limit] if limit is not None else [text for _,text in choices]
    body = clean[opening + 1:end].rstrip()
    if not body.endswith('}'):
        return []
    body = body[:-1]
    offset = opening + 1
    proposals = []
    labels = labels if labels is not None else {}
    early = (_generated(record_layout_search.variants,source,name,header,arch)
             + _generated(branch_tail_distribution.variants,source,name,header,arch)
             + _generated(call_preparation_repairs.variants,source,name,header,arch)
             + _generated(repair_cleanup.variants,source,name,header,arch)
             + _generated(record_lifetime_recovery.variants,source,name,header,arch)
             + _generated(pointer_result_repairs.variants,source,name,header)
             + _generated(record_store_bundles.variants,source,name,header,arch)
             + _generated(narrow_store_repairs.variants,source,name,header,arch)
             + _generated(return_contract_repairs.variants,source,name,header,arch,asm)
             + _generated(float_storage_repairs.variants,source,name,header,arch,asm)
             + _generated(narrow_expression_repairs.variants,source,name,header,arch)
             + later_local_repairs.variants(source,name,header,arch) + early)
    early = _generated(integer_constant_repairs.variants,source,name,header,**(entry_scope or {})) + early
    early = _generated(parameter_carriers.variants,source,name,header,arch) + _generated(narrow_load_subtraction.variants,source,name,header,arch) + early
    early = _generated(carrier_sequence_repairs.variants,source,name,header,arch) + _generated(compiler_phase_repairs.variants,source,name,header,arch) + _generated(scratch_role_repairs.variants,source,name,header,arch) + _generated(scalar_storage_reuse.variants,source,name,header,arch) + _generated(loop_storage_shapes.variants,source,name,header,arch) + early
    scope = entry_scope or {}
    early = (_generated(bit_predicate_repairs.variants,source,name,header,arch,**scope)
             + _generated(bit_predicate_repairs.stages,source,name,header,arch,**scope)
             + _generated(bit_role_repairs.variants,source,name,header,arch,**scope)
             + _generated(scope_topology_repairs.variants,source,name,header,arch,**scope) + early)
    # (pointers only read made const: first when the target loads through an argument between its prologue's saves,
    # which gcc 2.7.2 does only for memory it knows is read-only; decomprepair.const_read_repairs)
    const_reads = _generated(const_read_repairs.variants,source,name,asm)
    early = const_reads + early if const_read_repairs.early_load(asm) else early + const_reads
    # (a repeated constant return written once, the other paths jumping to it; decomprepair.shared_return_repairs)
    early = early + _generated(shared_return_repairs.variants,source,name)
    # (an assignment m2c placed after its local's first read moved up to it; decomprepair.read_before_assign_repairs)
    early = early + _generated(read_before_assign_repairs.variants,source,name)
    # (an empty case m2c invented, its `/* irregular */` break, dropped; decomprepair.irregular_case_repairs)
    early = early + _generated(irregular_case_repairs.variants,source,name)
    # (a switch tested with one sltiu: its operand cast to u32; decomprepair.unsigned_switch_repairs)
    early = early + _generated(unsigned_switch_repairs.variants,source,name)
    # (a global pointer reloaded before each store through it, declared volatile; decomprepair.volatile_reload_repairs)
    early = early + _generated(volatile_reload_repairs.variants,source,name)
    # (m2c's cursor walk over a global array, written back as the indexed loop; decomprepair.pointer_walk_index_repairs)
    early = early + _generated(pointer_walk_index_repairs.variants,source,name)
    # (m2c's cursors over a stack work queue, written as the counter's own indexed accesses; decomprepair.queue_cursor_index_repairs)
    early = early + _generated(queue_cursor_index_repairs.variants,source,name)
    # (m2c's guard and do/while, written as the for loop gcc marks with LOOP_VTOP; decomprepair.guarded_do_for_repairs)
    early = early + _generated(guarded_do_for_repairs.variants,source,name)
    # (m2c's local copy of a parameter the game changed in place, dropped; decomprepair.parameter_copy_drop_repairs)
    early = early + _generated(parameter_copy_drop_repairs.variants,source,name)
    for family,text in early:
        proposals.append(text);labels[text]=family
    for family,text in _generated(copy_shape_repairs.variants, source,name,header) + _generated(capture_followup_repairs.variants, source,name,header,callees):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(helper_expansion.variants, source, name, header) + _generated(global_identity_repairs.variants, source, name, arch, asm, header) + _generated(raw_address_repairs.variants, source, name, arch, asm, header) + _generated(buffer_address_repairs.variants, source, name, arch, asm, header) + _generated(sdk_io_repairs.variants, source, name, arch, asm, header) + _generated(target_local_repairs.variants, source, name, arch, asm, header) + _generated(formatter_repairs.variants, source, name) + _generated(switch_tables.variants, source, name, tables):
        proposals.append(text)
        labels[text] = family
    if arch == 'mips':
        for text in _generated(entry_point_repairs.variants, source, name, asm):
            proposals.append(text)
            labels[text] = 'interior_entry_symbol'
    for family, text in _generated(count_lifetime_repairs.variants, source, name, arch, header) + _generated(value_range_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(record_lifetime_repairs.variants, source,name,header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(operand_order_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family,text in _generated(global_store_repairs.variants, source,name,header):
        proposals.append(text)
        labels[text]=family
    for family,text in _generated(identity_lifetime_repairs.variants,source,name,header,arch):
        proposals.append(text);labels[text]=family
    for family,text in _generated(prepared_call_repairs.variants,source,name,header,arch):
        proposals.append(text)
        labels[text]=family
    for family,text in (_generated(call_scope_repairs.variants,source,name,header)
                        + _generated(debug_initializer_repairs.variants,source,name,flags,header)
                        + _generated(phase_union_repairs.variants,source,name,arch,flags,header)):
        proposals.append(text);labels[text]=family
    allocation_candidates = _generated(branch_pointer_repairs.variants, source,name,header) + _generated(allocation_repairs.variants, source, name, header)
    # Register near misses in a large switch: which cases share a temporary, and (GCC 2.x MIPS) an unused 64-bit
    # local whose zero-size CLOBBER moves one insn and so breaks an allocation tie. Every block start is proposed:
    # the one that matters is wherever the first differing register sits, which the source alone does not say.
    allocation_candidates += _generated(case_temporary_repairs.variants, source, name)
    # (A block start is a guess, so a function with many blocks gets hundreds of proposals: only where a big switch
    # makes that worth a compile each, not for every small function.)
    if arch == 'mips' and len(re.findall(r'\bcase\b', clean[start:end])) >= 3:
        allocation_candidates += _generated(phantom_local_repairs.variants, source, name, limit=400)
    for family, text in allocation_candidates:
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(loaded_byte_repairs.variants, source, name, header, arch) + _generated(pointer_capture_repairs.variants, source, name, header) + _generated(pointer_pair_repairs.variants, source, name, header) + _generated(field_order_repairs.variants, source, name, header) + _generated(zero_test_repairs.variants, source, name, arch, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (semantic + _generated(branch_scalar_repairs.variants,source,name,header,arch)
                         + _generated(call_count_repairs.variants,source,name,header,arch)
                         + _generated(call_assignment_repairs.variants,source,name,header,arch)
                         + _generated(constant_pointer_repairs.variants,source,name,header,arch)
                         + _generated(pointer_snapshot_repairs.variants,source,name,header,arch,asm)
                         + _generated(narrow_call_repairs.variants,source,name,header,arch)):
        proposals.append(text);labels[text] = family
    for family, text in _generated(example_lifetime_repairs.variants, source, name, header) + _generated(call_lifetime_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(match_shape_repairs.variants, source, name, header) + _generated(match_followup_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(captured_lifetimes.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(increment_expression_repairs.variants, source, name) + _generated(loop_weight_repairs.variants, source, name, header) + _generated(equal_value_flow.variants, source, name, header) + _generated(macro_scope_weight.variants, source, name, header) + _generated(condition_table_anchor.variants, source, name, header) + _generated(compare_operand_stage.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (_generated(loop_guard_storage_repairs.variants, source, name, asm, header)
                         + _generated(countdown_guard_repairs.variants, source, name, header)):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(captured_control_repairs.variants, source,name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(float_intermediate_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(structure_return_repairs.variants, source, name, header) + _generated(sdk_shape_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (_generated(stack_repairs.variants, source, name) if arch in {"mips", "ppc", "x86", "arm", "thumb"} else []):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(register_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(inferred_layout_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(compiler_shape_repairs.variants, source, name, arch, flags, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(evidence_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (_generated(address_association_repairs.variants, source, name) + _generated(call_initialization_repairs.variants, source, name)
                         + _generated(masked_reuse_repairs.variants, source, name) + _generated(pointer_index_scope.variants, source, name, header) + _generated(focused_lifetime_repairs.variants, source, name) + _generated(store_result_repairs.variants, source, name, header) + _generated(register_reuse_repairs.variants, source, name) + _generated(affine_operand_repairs.variants, source, name)):
        proposals.append(text)
        labels[text] = family
    for text in _generated(mmio_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = 'mmio_address'
    for family, text in _generated(observed_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in (_generated(near_match_repairs.variants, source, name, asm)
                         + _generated(near_match_repairs.bitwise_operand_order, source, name, header)
                         + _generated(near_match_repairs.float_update_order, source, name, header)):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(captured_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(global_array_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(record_slot_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(width_promotion_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(param_mask_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(init_placement_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(local_inline_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    # a repeated table product staged in a block-local (decomp-repair 0.5.287): a voice-list walk's last step
    for family, text in _generated(product_temporary_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(loop_param_reuse_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(scaled_size_inline_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(clamp_barrier_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(field_struct_view_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(field_struct_view_repairs.site_variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(field_struct_view_repairs.printed_variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(store_value_first_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(entry_parameter_copy_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(frame_padding_repairs.variants, source, name, asm):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(loop_index_inline_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(guard_shape_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for text in _generated(load_pair_repairs.variants, source,name):
        proposals.append(text)
        labels[text]='paired_load_order'
    for family,text in _generated(lifetime_repairs.variants, source,name):
        proposals.append(text)
        labels[text]=family
    for text in _generated(store_address_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = 'store_address_lifetime'
    for text in _generated(stack_padding_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = 'stack_padding'
    for text in _generated(local_width_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = 'local_width'
    for text in _generated(local_widening_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = 'local_widening'
    for family,text in _generated(phase_lifetime_repairs.variants, source,name,header):
        proposals.append(text)
        labels[text] = family
    for family,text in _generated(initializer_clusters.variants, source,name,header):
        proposals.append(text)
        labels[text] = family
    for module in (callee_prototype_repairs, integer_lifetime_repairs, block_accumulator_repairs, branch_layout_repairs):
        for family, text in _generated(module.variants, source, name):
            proposals.append(text)
            labels[text] = family
    from .repair_generation import exhausted
    if exhausted():
        return _finish(source,name,proposals,labels,allocation_candidates,limit)
    # Two disjoint lifetime merges can be useful together even when either allocation is worse.
    # Keep the bundle tiny; each merge retains the existing conservative liveness boundaries.
    merges = [text for family, text in _generated(integer_lifetime_repairs.variants, source, name)
              if family == 'merge_integer_lifetimes']
    for first in merges[:2]:
        second = next((text for family, text in _generated(integer_lifetime_repairs.variants, first, name)
                       if family == 'merge_integer_lifetimes' and text != source), None)
        if second:
            proposals.append(second)
            labels[second] = 'combined_integer_lifetimes'
    for text in _generated(pointer_cast_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = 'pointer_cast_inline'
    from .repair_generation import exhausted
    if exhausted():
        return _finish(source,name,proposals,labels,allocation_candidates,limit)
    # Fresh m2c drafts already get this alternative in autopilot. Saved/transferred drafts need the
    # same chance, with their macro typedef block kept out of the refinement's type substitutions.
    from .repair_generation import wanted
    if wanted('m2c_refinement') and arch == 'mips' and ('M2C_FIELD(' in source or 'M2C_UNK' in source or re.search(r'\barg[0-3]\b',source)):
        plain = re.sub(r'#ifndef M2C_DEFINES.*?#endif\n?', '', source, flags=re.S)
        refined = m2cfix.refine_draft(plain, name, asm)
        if refined and refined != plain:
            proposals.append(seeds.M2C_MACROS + refined)
            labels[proposals[-1]] = 'm2c_refinement'
    if arch == "mips":
        for family, text in _generated(inline_scheduling_repairs.variants, source, name, header):
            proposals.append(text)
            labels[text] = family
    for family, text in _generated(allocator_shape_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(normalization_repairs.variants, source, name, header, arch):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(scheduling_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    if local := branch_locals.variant(source, name):
        proposals.insert(0, local)
        labels[local] = 'branch_locals'
    for text in _generated(storage_scope_repairs.variants, source, name, arch, flags):
        proposals.append(text)
        labels[text] = 'pointer_storage_scope'
    for family,text in _generated(pattern_repairs.variants, source,name,any(re.fullmatch(r'-g[1-3]?',f) for f in flags)):
        proposals.append(text)
        labels[text] = family
    if wanted('expression_or_near_repair'):
        proposals += _generated(expression_repairs.variants, source, name, arch)
        proposals += _generated(near_repairs.variants, source, name, arch, asm)

    from .repair_generation import exhausted
    if exhausted():
        return _finish(source,name,proposals,labels,allocation_candidates,limit)
    if not wanted('expression_or_near_repair'):
        return _finish(source,name,proposals,labels,allocation_candidates,limit)
    def add(a, b, text):
        candidate = source[:a] + text + source[b:]
        if candidate != source and candidate not in proposals:
            proposals.append(candidate)

    # A result initialized to zero and assigned in a single conditional arm. Require the whole body,
    # one comparison and one assignment: declarations or side effects elsewhere must not be discarded.
    early = re.fullmatch(
        rf'\s*(?:s32|int|long)\s+(?P<v>{_IDENT})\s*;\s*(?P=v)\s*=\s*0\s*;\s*'
        rf'if\s*\(\s*(?P<x>{_IDENT})\s*(?P<op>>=|<=|>|<)\s*0\s*\)\s*{{\s*'
        rf'(?P=v)\s*=\s*(?P<expr>[^;{{}}]+);\s*}}\s*return\s+(?P=v)\s*;\s*', body)
    if early and not re.search(r'\b' + re.escape(early['v']) + r'\b', early['expr']):
        inverse = {'>=': '<', '<=': '>', '>': '<=', '<': '>='}[early['op']]
        expr = source[offset + early.start('expr'):offset + early.end('expr')]
        add(offset, offset + len(body),
            f'\n    if ({early["x"]} {inverse} 0) return 0;\n    return {expr};\n')

    # Byte pointers converted to integer addresses can need the operands exchanged too. The 32-bit cast
    # is only proposed on supported 32-bit processors, never on a 64-bit or unknown imported project.
    if arch in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        address = re.compile(rf'\(\s*(?P<wrap>\()?\s*\(\s*(?:u8|char|unsigned\s+char)\s*\*\s*\)\s*'
                             rf'(?P<p>{_MEMBER})\s*(?(wrap)\))\s*\+\s*(?P<i>{_IDENT})\s*\)')
        for m in address.finditer(body):
            add(offset + m.start(), offset + m.end(), f'({m["i"]} + (int){source[offset + m.start("p"):offset + m.end("p")]})')

    # Try dropping a trailing literal or identifier from a call, not an arbitrary expression with side effects. Walk
    # balanced parentheses so nested calls/commas are not confused with the outer argument boundary.
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)\(', clean, re.M))
    for m in re.finditer(rf'\b({_IDENT})\s*\(', body):
        if m[1] in {'if', 'while', 'for', 'switch', 'sizeof', '_Alignof'} | macros:
            continue
        depth, commas, close = 1, [], None
        for pos in range(m.end(), len(body)):
            char = body[pos]
            depth += (char in '([') - (char in ')]')
            if depth == 1 and char == ',':
                commas.append(pos)
            if depth == 0:
                close = pos
                break
        if close is None or not commas:
            continue
        tail = body[commas[-1] + 1:close].strip()
        from decomprepair.repair_preflight import drop_argument_allowed
        if (re.fullmatch(rf'(?:0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*|{_IDENT})', tail)
                and drop_argument_allowed(source,m[1],body[m.end():close],header)):
            add(offset + commas[-1], offset + close, '')
        if len(proposals) >= LIMIT:
            break
    return _finish(source,name,proposals,labels,allocation_candidates,limit)


def _finish(source,name,proposals,labels,allocation_candidates,limit):
    # Preserve the targeted attribution when an older generic family emits the same C.
    for family, text in allocation_candidates:
        labels[text] = family
    for text in proposals:
        labels.setdefault(text,'expression_or_near_repair')
    # Round-robin families so a new family cannot crowd existing repairs out of a short pass.
    groups = {}
    from decomprepair.repair_preflight import invalid_call

    from .repair_generation import ACTIVE
    for text in dict.fromkeys(proposals):
        if text == source:
            continue
        if ACTIVE.get() is None and invalid_call(text, name):
            continue
        groups.setdefault(labels[text],[]).append(text)
    # Missing argument positions and unknown-object types need refinement before speculative
    # call scopes; adding a scope family must not displace this existing short preflight lane.
    from .repair_ranking import GUARDED, STRUCTURAL
    first=('m2c_refinement',)+GUARDED+STRUCTURAL
    groups={**{family:groups[family] for family in first if family in groups},**groups}
    ordered = []
    while any(groups.values()):
        for items in groups.values():
            if items:
                ordered.append(items.pop(0))
    return ordered if limit is None else ordered[:limit]
