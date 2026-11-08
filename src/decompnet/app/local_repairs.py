"""Bounded deterministic source proposals before random permutation, shared by both judges.

These transformations are matching hypotheses, not equivalence proofs. Only a fresh ordinary
compiler and byte/object judge can accept them. No proposal writes headers, targets or game files.
"""

import logging
import re
import shlex
import time
from collections import deque

from ..forks.csource import _blank, find
from . import (
    address_association_repairs,
    affine_operand_repairs,
    allocation_repairs,
    allocator_call_guards,
    allocator_shape_repairs,
    argument_mask_repairs,
    block_accumulator_repairs,
    branch_layout_repairs,
    branch_locals,
    branch_pointer_repairs,
    branch_scalar_repairs,
    byte_address_inline_repairs,
    byte_increment_repairs,
    call_assignment_repairs,
    call_count_repairs,
    call_initialization_repairs,
    call_lifetime_repairs,
    call_result_repairs,
    call_scope_repairs,
    captured_control_repairs,
    captured_index_repairs,
    captured_lifetimes,
    captured_repairs,
    compiler_shape_repairs,
    component_index_repairs,
    constant_initializer_staging,
    constant_pointer_repairs,
    constant_store_scope,
    copy_lifetime_repairs,
    copy_shape_repairs,
    count_lifetime_repairs,
    countdown_guard_repairs,
    debug_boundary_repairs,
    debug_initializer_repairs,
    decision_address_repairs,
    decoded_byte_repairs,
    equal_value_flow,
    evidence_repairs,
    example_lifetime_repairs,
    expression_locals,
    expression_repairs,
    field_order_repairs,
    float_intermediate_repairs,
    focused_lifetime_repairs,
    formatter_repairs,
    global_store_repairs,
    guarded_call_repairs,
    helper_expansion,
    identity_lifetime_repairs,
    increment_expression_repairs,
    inferred_layout_repairs,
    initializer_clusters,
    inline_scheduling_repairs,
    integer_constant_repairs,
    integer_lifetime_repairs,
    known_bit_repairs,
    later_local_repairs,
    lifetime_repairs,
    load_pair_repairs,
    loaded_byte_repairs,
    local_widening_repairs,
    local_width_repairs,
    loop_repairs,
    loop_storage_shapes,
    loop_weight_repairs,
    macro_address_repairs,
    masked_reuse_repairs,
    match_followup_repairs,
    match_shape_repairs,
    narrow_call_repairs,
    narrow_chains,
    narrow_expression_repairs,
    narrow_load_subtraction,
    narrow_store_repairs,
    normalization_repairs,
    observed_repairs,
    operand_order_repairs,
    parameter_carriers,
    pattern_repairs,
    permuter_local,
    phase_lifetime_repairs,
    phase_union_repairs,
    pointer_capture_repairs,
    pointer_cast_repairs,
    pointer_index_scope,
    pointer_pair_repairs,
    post_call_capture_repairs,
    preheader_order_repairs,
    record_lifetime_repairs,
    register_repairs,
    register_reuse_repairs,
    scalar_normalization_scope,
    scalar_storage_reuse,
    scheduling_repairs,
    scratch_role_repairs,
    sdk_shape_repairs,
    shared_offset_repairs,
    stack_padding_repairs,
    stack_repairs,
    storage_scope_repairs,
    store_address_repairs,
    store_result_repairs,
    structured_loop_repairs,
    switch_constant_dispatch,
    temporary_repairs,
    terminal_guard_repairs,
    typed_draft_repairs,
    unsigned_shift_repairs,
    value_range_repairs,
    value_repairs,
    zero_chain_repairs,
    zero_test_repairs,
)

log = logging.getLogger("decompdecomp")
MAX_SOURCE_BYTES = 65536
MAX_HEADER_BYTES = 1048576
MAX_CANDIDATES = 256
PER_FAMILY = 8
GENERATION_SECONDS = 2


def variants(source, name, arch="", flags=(), header="", *, limit=MAX_CANDIDATES,
             deadline=None, stopped=lambda: False, offset=0):
    """Offer a bounded, fair page of unique C candidates without scoring or adopting any.

    Rotate the first family on later turns so short compiler budgets eventually reach every
    generator. Unknown ABI/flags leave the corresponding guarded families inapplicable.
    """
    if (not isinstance(source,str) or not isinstance(header,str) or not source or not name
            or len(source.encode()) > MAX_SOURCE_BYTES or len(header.encode()) > MAX_HEADER_BYTES
            or limit <= 0 or stopped() or not find(source,name)):
        return []
    clean = _blank(source)
    if re.search(r'::|\b(?:class|template|namespace|asm|__asm|__asm__)\b', clean):
        return []
    deadline = min(deadline if deadline is not None else float("inf"), time.monotonic()+GENERATION_SECONDS)
    flags = tuple(shlex.split(flags)) if isinstance(flags,str) else tuple(flags)
    debug_flags = [flag for flag in flags if re.fullmatch(r'-g(?:[0-3]|gdb[0-3]?)?',flag)]
    debug_lines = bool(debug_flags and debug_flags[-1] not in {'-g0','-ggdb0'})
    generators = [
    (branch_locals.variant, lambda: [("branch_local_declarations",value)] if (value := branch_locals.variant(source,name)) else []),
    (later_local_repairs.variants, lambda: later_local_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (loop_storage_shapes.variants, lambda: loop_storage_shapes.variants(source=source, name=name, header=header, arch=arch)),
    (scalar_storage_reuse.variants, lambda: scalar_storage_reuse.variants(source=source, name=name, header=header, arch=arch)),
    (constant_store_scope.variants, lambda: constant_store_scope.variants(source=source, name=name, header=header)),
    (scalar_normalization_scope.variants, lambda: scalar_normalization_scope.variants(source=source, name=name, header=header)),
    (equal_value_flow.variants, lambda: equal_value_flow.variants(source=source, name=name, header=header)),
    (expression_locals.variants, lambda: expression_locals.variants(source=source, name=name)),
    (narrow_chains.variants, lambda: narrow_chains.variants(source=source, name=name)),
    (allocator_call_guards.variants, lambda: allocator_call_guards.variants(source=source, name=name, header=header)),
    (branch_layout_repairs.store_tails, lambda: branch_layout_repairs.store_tails(source,name,header)),
    (branch_layout_repairs.scalar_tails, lambda: branch_layout_repairs.scalar_tails(source,name,header)),
    (debug_boundary_repairs.call_pairs, lambda: debug_boundary_repairs.call_pairs(source,name,header)),
    (address_association_repairs.variants, lambda: address_association_repairs.variants(source=source, name=name)),
    (affine_operand_repairs.variants, lambda: affine_operand_repairs.variants(source=source, name=name)),
    (allocation_repairs.variants, lambda: allocation_repairs.variants(source=source, name=name, header=header)),
    (allocator_shape_repairs.variants, lambda: allocator_shape_repairs.variants(source=source, name=name, header=header)),
    (argument_mask_repairs.variants, lambda: argument_mask_repairs.variants(source=source, name=name, header=header)),
    (block_accumulator_repairs.variants, lambda: block_accumulator_repairs.variants(source=source, name=name)),
    (branch_layout_repairs.variants, lambda: branch_layout_repairs.variants(source=source, name=name)),
    (branch_pointer_repairs.variants, lambda: branch_pointer_repairs.variants(source=source, name=name, header=header)),
    (branch_scalar_repairs.variants, lambda: branch_scalar_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (byte_address_inline_repairs.variants, lambda: byte_address_inline_repairs.variants(source=source, name=name, flags=flags, header=header, arch=arch)),
    (byte_increment_repairs.variants, lambda: byte_increment_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (call_assignment_repairs.variants, lambda: call_assignment_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (call_count_repairs.variants, lambda: call_count_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (call_initialization_repairs.variants, lambda: call_initialization_repairs.variants(source=source, name=name)),
    (call_lifetime_repairs.variants, lambda: call_lifetime_repairs.variants(source=source, name=name, header=header)),
    (call_result_repairs.variants, lambda: call_result_repairs.variants(source=source, name=name)),
    (call_scope_repairs.variants, lambda: call_scope_repairs.variants(source=source, name=name, header=header)),
    (captured_control_repairs.variants, lambda: captured_control_repairs.variants(source=source, name=name)),
    (captured_index_repairs.variants, lambda: captured_index_repairs.variants(source=source, name=name, header=header)),
    (captured_lifetimes.variants, lambda: captured_lifetimes.variants(source=source, name=name)),
    (captured_repairs.variants, lambda: captured_repairs.variants(source=source, name=name)),
    (compiler_shape_repairs.variants, lambda: compiler_shape_repairs.variants(source=source, name=name, arch=arch, flags=flags, header=header)),
    (component_index_repairs.variants, lambda: component_index_repairs.variants(source=source, name=name, header=header)),
    (constant_initializer_staging.variants, lambda: constant_initializer_staging.variants(source=source, name=name, header=header, arch=arch)),
    (constant_pointer_repairs.variants, lambda: constant_pointer_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (copy_lifetime_repairs.variants, lambda: copy_lifetime_repairs.variants(source=source, name=name, header=header)),
    (copy_shape_repairs.variants, lambda: copy_shape_repairs.variants(source=source, name=name, header=header)),
    (count_lifetime_repairs.variants, lambda: count_lifetime_repairs.variants(source=source, name=name, arch=arch, header=header)),
    (countdown_guard_repairs.variants, lambda: countdown_guard_repairs.variants(source=source, name=name, header=header)),
    (debug_boundary_repairs.variants, lambda: debug_boundary_repairs.variants(source=source, name=name, flags=flags, header=header)),
    (debug_initializer_repairs.variants, lambda: debug_initializer_repairs.variants(source=source, name=name, flags=flags, header=header)),
    (decision_address_repairs.variants, lambda: decision_address_repairs.variants(source=source, name=name, arch=arch, header=header)),
    (decoded_byte_repairs.variants, lambda: decoded_byte_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (evidence_repairs.variants, lambda: evidence_repairs.variants(source=source, name=name, header=header)),
    (example_lifetime_repairs.variants, lambda: example_lifetime_repairs.variants(source=source, name=name, header=header)),
    (expression_repairs.variants, lambda: expression_repairs.variants(source=source, name=name, arch=arch)),
    (field_order_repairs.variants, lambda: field_order_repairs.variants(source=source, name=name, header=header)),
    (float_intermediate_repairs.variants, lambda: float_intermediate_repairs.variants(source=source, name=name)),
    (focused_lifetime_repairs.variants, lambda: focused_lifetime_repairs.variants(source=source, name=name)),
    (formatter_repairs.variants, lambda: formatter_repairs.variants(source=source, name=name)),
    (global_store_repairs.variants, lambda: global_store_repairs.variants(source=source, name=name, header=header)),
    (guarded_call_repairs.variants, lambda: guarded_call_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (helper_expansion.variants, lambda: helper_expansion.variants(source=source, name=name, header=header)),
    (identity_lifetime_repairs.variants, lambda: identity_lifetime_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (increment_expression_repairs.variants, lambda: increment_expression_repairs.variants(source=source, name=name)),
    (inferred_layout_repairs.variants, lambda: inferred_layout_repairs.variants(source=source, name=name, arch=arch)),
    (initializer_clusters.variants, lambda: initializer_clusters.variants(source=source, name=name, header=header)),
    (inline_scheduling_repairs.variants, lambda: inline_scheduling_repairs.variants(source=source, name=name, header=header)),
    (integer_constant_repairs.variants, lambda: integer_constant_repairs.variants(source=source, name=name, header=header)),
    (integer_lifetime_repairs.variants, lambda: integer_lifetime_repairs.variants(source=source, name=name)),
    (known_bit_repairs.variants, lambda: known_bit_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (lifetime_repairs.variants, lambda: lifetime_repairs.variants(source=source, name=name)),
    (load_pair_repairs.variants, lambda: load_pair_repairs.variants(source=source, name=name)),
    (loaded_byte_repairs.variants, lambda: loaded_byte_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (local_widening_repairs.variants, lambda: local_widening_repairs.variants(source=source, name=name)),
    (local_width_repairs.variants, lambda: local_width_repairs.variants(source=source, name=name)),
    (loop_repairs.variants, lambda: loop_repairs.variants(source=source, name=name)),
    (loop_weight_repairs.variants, lambda: loop_weight_repairs.variants(source=source, name=name, header=header)),
    (macro_address_repairs.variants, lambda: macro_address_repairs.variants(source=source, name=name, arch=arch, flags=flags, header=header)),
    (masked_reuse_repairs.variants, lambda: masked_reuse_repairs.variants(source=source, name=name)),
    (match_followup_repairs.variants, lambda: match_followup_repairs.variants(source=source, name=name, header=header)),
    (match_shape_repairs.variants, lambda: match_shape_repairs.variants(source=source, name=name, header=header)),
    (narrow_call_repairs.variants, lambda: narrow_call_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (narrow_expression_repairs.variants, lambda: narrow_expression_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (narrow_load_subtraction.variants, lambda: narrow_load_subtraction.variants(source=source, name=name, header=header, arch=arch)),
    (narrow_store_repairs.variants, lambda: narrow_store_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (normalization_repairs.variants, lambda: normalization_repairs.variants(source=source, name=name, header=header)),
    (observed_repairs.variants, lambda: observed_repairs.variants(source=source, name=name)),
    (operand_order_repairs.variants, lambda: operand_order_repairs.variants(source=source, name=name, header=header)),
    (parameter_carriers.variants, lambda: parameter_carriers.variants(source=source, name=name, header=header, arch=arch)),
    (pattern_repairs.variants, lambda: pattern_repairs.variants(source=source, name=name, debug_lines=debug_lines)),
    (permuter_local.variants, lambda: permuter_local.variants(source=source, name=name)),
    (phase_lifetime_repairs.variants, lambda: phase_lifetime_repairs.variants(source=source, name=name, header=header)),
    (phase_union_repairs.variants, lambda: phase_union_repairs.variants(source=source, name=name, arch=arch, flags=flags, header=header)),
    (pointer_capture_repairs.variants, lambda: pointer_capture_repairs.variants(source=source, name=name, header=header)),
    (pointer_cast_repairs.variants, lambda: pointer_cast_repairs.variants(source=source, name=name, arch=arch)),
    (pointer_index_scope.variants, lambda: pointer_index_scope.variants(source=source, name=name, header=header)),
    (pointer_pair_repairs.variants, lambda: pointer_pair_repairs.variants(source=source, name=name, header=header)),
    (post_call_capture_repairs.variants, lambda: post_call_capture_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (preheader_order_repairs.variants, lambda: preheader_order_repairs.variants(source=source, name=name, header=header)),
    (record_lifetime_repairs.variants, lambda: record_lifetime_repairs.variants(source=source, name=name, header=header)),
    (register_repairs.variants, lambda: register_repairs.variants(source=source, name=name, arch=arch)),
    (register_reuse_repairs.variants, lambda: register_reuse_repairs.variants(source=source, name=name)),
    (scheduling_repairs.variants, lambda: scheduling_repairs.variants(source=source, name=name)),
    (scratch_role_repairs.variants, lambda: scratch_role_repairs.variants(source=source, name=name, header=header, arch=arch)),
    (sdk_shape_repairs.variants, lambda: sdk_shape_repairs.variants(source=source, name=name, header=header)),
    (shared_offset_repairs.variants, lambda: shared_offset_repairs.variants(source=source, name=name, header=header)),
    (stack_padding_repairs.variants, lambda: stack_padding_repairs.variants(source=source, name=name)),
    (stack_repairs.variants, lambda: stack_repairs.variants(source=source, name=name)),
    (storage_scope_repairs.variants, lambda: storage_scope_repairs.variants(source=source, name=name, arch=arch, flags=flags)),
    (store_address_repairs.variants, lambda: store_address_repairs.variants(source=source, name=name, arch=arch)),
    (store_result_repairs.variants, lambda: store_result_repairs.variants(source=source, name=name, header=header)),
    (structured_loop_repairs.variants, lambda: structured_loop_repairs.variants(source=source, name=name)),
    (switch_constant_dispatch.variants, lambda: switch_constant_dispatch.variants(source=source, name=name, header=header)),
    (temporary_repairs.variants, lambda: temporary_repairs.variants(source=source, name=name)),
    (terminal_guard_repairs.variants, lambda: terminal_guard_repairs.variants(source=source, name=name, header=header)),
    (typed_draft_repairs.variants, lambda: typed_draft_repairs.variants(source=source, name=name, header=header)),
    (unsigned_shift_repairs.variants, lambda: unsigned_shift_repairs.variants(source=source, name=name, header=header)),
    (value_range_repairs.variants, lambda: value_range_repairs.variants(source=source, name=name, header=header)),
    (value_repairs.variants, lambda: value_repairs.variants(source=source, name=name)),
    (zero_chain_repairs.variants, lambda: zero_chain_repairs.variants(source=source, name=name, header=header)),
    (zero_test_repairs.variants, lambda: zero_test_repairs.variants(source=source, name=name, arch=arch, header=header)),
    ]
    offset %= len(generators)
    generators = generators[offset:]+generators[:offset]
    groups, seen = [], {source}
    # Each family is bounded internally too. The shared deadline is checked between families;
    # a single Python rewrite is not preempted, and the input-size guard bounds its source.
    for function, generate in generators:
        if stopped() or time.monotonic() >= deadline:
            break
        try:
            choices = generate()
        except (ValueError, IndexError, TypeError, RecursionError) as error:
            log.debug("local repairs: %s declined this source: %s",function.__module__,error)
            continue
        group = deque()
        for choice in choices:
            family, candidate = (function.__module__.rsplit('.',1)[-1], choice) if isinstance(choice,str) else choice
            if (not isinstance(candidate,str) or candidate in seen or len(candidate.encode()) > MAX_SOURCE_BYTES
                    or not find(candidate,name)):
                continue
            seen.add(candidate)
            group.append((family,candidate))
            if len(group) >= PER_FAMILY:
                break
        if group:
            groups.append(group)
    result = []
    while groups and len(result) < min(limit,MAX_CANDIDATES):
        for group in groups:
            if group:
                result.append(group.popleft())
                if len(result) >= min(limit,MAX_CANDIDATES):
                    break
        groups = [group for group in groups if group]
    return result
