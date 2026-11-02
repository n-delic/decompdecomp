"""Rank applicable hypotheses by observed mismatches without excluding other families."""

def order(families, rows):
    from .allocation_evidence import diagnose
    from .permuter_focus import classify, normalized
    rows = normalized(rows)
    focus = classify(rows)
    markers = {row[0] for row in rows if row[0] != ' '}
    from .matching_investigations import paired_loads
    from .normalization_repairs import frame_gap
    if paired_loads(rows):
        preferred = ('paired_load_order','scalar_schedule_order','local_declaration_order','small_helper_expansion','combined_scheduling')
    elif markers and markers <= {'s'}:
        preferred = ('modular_guard_width', 'local_width', 'spill_normalization', 'unused_scalar_local',
                     'local_storage', 'parameter_storage', 'local_declaration_order', 'stack_padding')
    elif frame_gap(rows):
        preferred = ('spill_normalization', 'split_narrow_lifetime', 'call_return_normalization',
                     'stack_address_temporary', 'combined_scheduling', 'stack_padding')
    elif diagnose(rows)['register_only'] or diagnose(rows)['copy_near_miss']:
        preferred = ('cfg_equal_value_increment','equal_value_increment','loop_weight_scope',
                     'shared_branch_locals','paired_operand_reuse','allocation_schedule_bridge','copy_register_reuse','masked_value_reuse')
    elif diagnose(rows)['scheduling']:
        preferred = ('shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'affine_counter_guard', 'affine_counter', 'initialization_cluster','allocation_initialization_order','byte_fill_constant','scalar_schedule_order','combined_scheduling')
    elif diagnose(rows)['allocation_schedule']:
        preferred = ('paired_operand_reuse', 'affine_counter_guard', 'affine_counter',
                     'allocation_initialization_order', 'scalar_schedule_order', 'combined_scheduling')
    elif focus == 'call':
        preferred = ('float_call_staging', 'success_pointer_update', 'branch_pointer_lifetimes', 'branch_pointer_declarations', 'known_call_signature', 'call_postincrement', 'target_call_argument', 'target_local_bundle', 'float_argument_bits', 'runtime_scalar_signature', 'call_forwarded_arguments', 'call_arity', 'callee_parameter_width', 'callee_return_width', 'call_result_inline', 'parameter_width', 'parameter_storage')
    elif markers and markers <= {'r', 's'}:
        preferred = ('callee_buffer_extent', 'local_declaration_order', 'spill_declaration_order', 'small_helper_expansion', 'stack_address_temporary', 'combined_scheduling', 'stack_padding', 'unused_scalar_local', 'local_storage', 'parameter_storage',
                     'spill_declaration_order', 'allocation_schedule_bridge', 'local_declaration_order', 'stack_array', 'parameter_width', 'declaration_order') if 's' in markers else (
            'shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'float_call_staging', 'inline_scalar_chain', 'paired_operand_reuse', 'cfg_equal_value_increment', 'equal_value_increment', 'loop_weight_scope', 'decision_address_bundle', 'qualified_selector_load', 'selector_address_staging', 'single_case_switch', 'halfword_arithmetic', 'branch_pointer_lifetimes', 'branch_pointer_declarations', 'typed_clamp_load', 'typed_pointer_load', 'loop_update_order', 'pointer_argument_alias', 'allocation_schedule_bridge', 'allocation_initialization_order', 'byte_fill_constant', 'spill_declaration_order', 'record_value_forwarding', 'narrow_definition_lifetime', 'sink_zero_initialization', 'structured_backedge', 'pointer_call_staging', 'pointer_load_inline', 'word_zero_test_reuse', 'pointer_local_reuse', 'independent_field_store_order', 'paired_pointer_declarations', 'copy_register_reuse', 'constant_index_temporary', 'balanced_call_references', 'balanced_pointer_calls', 'call_return_normalization', 'split_narrow_lifetime', 'combined_call_lifetimes', 'branch_tail_lifetimes', 'staged_call_arguments', 'single_field_record', 'record_access_shape', 'split_call_result', 'direct_mask_condition', 'inline_scalar_shape', 'expression_placement', 'scalar_schedule_order', 'combined_scheduling', 'direct_compound_store', 'address_association', 'masked_value_reuse', 'paired_integer_lifetimes', 'split_integer_phase', 'cfg_integer_lifetimes', 'constant_register_reuse', 'last_shift_reuse', 'unsigned_assignment_reuse', 'table_slot_lifetime', 'store_address_lifetime', 'pointer_cast_inline', 'comparison_reuse', 'integer_address_order',
            'comparison_operand_order', 'integer_product_order', 'arithmetic_operand_order', 'typed_temporary', 'value_lifetime', 'split_integer_lifetime', 'merge_integer_lifetimes',
            'split_scalar_lifetime', 'array_index_width', 'local_width', 'declaration_order')
    elif 'r' in markers and markers <= {'r', 'i'}:
        # Address rendering can coexist with a register near miss. Keep address
        # hypotheses first; these are advisory priorities, never address proofs.
        preferred = ('scalar_global_address', 'integer_address_order', 'call_return_normalization',
                     'split_narrow_lifetime', 'inline_scalar_shape', 'local_widening', 'signedness')
    elif markers & {'<', '>', '|', 'o'}:
        preferred = ('decision_address_bundle', 'single_case_switch', 'halfword_arithmetic', 'shared_flag_guard', 'count_lifetime_bundle', 'count_load_qualification', 'count_phase_lifetime', 'normalization_scope', 'target_call_argument', 'known_address_alias', 'single_field_record', 'target_local_bundle', 'structured_backedge', 'loop_invariant_placement', 'combined_scheduling', 'shared_return_tail', 'redundant_loop_guard', 'call_initialization', 'address_association', 'paired_load_order', 'pointer_before_store', 'guard_forward_jump', 'branch_call', 'goto_leaf', 'switch_layout',
                     'branch_orientation', 'loop_induction', 'callee_parameter_width', 'call_result_inline', 'parameter_width')
    elif 'i' in markers:
        preferred = ('known_address_alias', 'target_call_argument', 'target_local_bundle', 'local_widening', 'integer_address_order', 'signedness', 'narrowing', 'constant_local')
    else:
        return list(families)
    return sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))


def evidence(net, function_id, agent_id):
    diffs = getattr(net, '_last_diffs', {})
    own = diffs.get((agent_id,function_id))
    if own:
        return own[0]
    # Advisory ordering only: a saved draft may predate this process's latest diff. Never use
    # this hint as a verdict or exclude unranked families; the normal judge tests each source.
    for (_,fid), value in reversed(list(diffs.items())):
        if fid == function_id:
            return value[0]
    return []


def remember(net, scope, family, baseline, measured):
    # Displayed instruction identity is only advisory evidence of ineffectiveness, never proof
    # of identical linked bytes. Errors/missing evidence do not count. Source text stays exact.
    if not baseline or not measured or baseline.get('rows') is None or measured.get('rows') is None:
        return
    if baseline.get('score') is None or measured.get('score') is None:
        return
    history=getattr(net,'_repair_effects',{})
    net._repair_effects=history
    key=(scope,family)
    unchanged=(baseline['score']==measured['score'] and baseline['rows']==measured['rows'])
    history[key]=history.get(key,0)+1 if unchanged else 0
    while len(history)>256:
        del history[next(iter(history))]


def demote(net, scope, families):
    history=getattr(net,'_repair_effects',{})
    return sorted(families,key=lambda family:history.get((scope,family),0)>=2)


def effect_scope(net, fn, source):
    """Family demotion belongs to this exact parent, not all future source shapes."""
    import hashlib

    from .agent_tokens import target_scope
    return hashlib.sha256((target_scope(net,fn,'')+source).encode()).hexdigest()


def private_abi_order(families, arch, asm, rows):
    """Defer ordinary entry-signature searches when the measured mismatch needs caller context."""
    from .. import arch as arches
    reader=arches.get(arch).source_private_abi
    if not reader or not reader(asm,rows):return list(families)
    ordinary={'runtime_scalar_signature','parameter_width','parameter_storage','float_argument_bits'}
    return sorted(families,key=lambda family:family in ordinary)
