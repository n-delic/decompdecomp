"""Rank applicable hypotheses by observed mismatches without excluding other families."""

GUARDED = ('mips_debug_break','live_return_address','live_return_address_initializers')


def guarded_first(candidates, labels=None):
    """Finite target-backed proposals precede generic learned or localisation hypotheses."""
    family=lambda c:labels.get(c,'') if labels is not None else c
    return sorted(candidates,key=lambda c:GUARDED.index(family(c)) if family(c) in GUARDED else len(GUARDED))


def order(families, rows):
    from .allocation_evidence import diagnose
    from .permuter_focus import classify, normalized
    rows = normalized(rows)
    focus = classify(rows)
    markers = {row[0] for row in rows if row[0] != ' '}
    from .matching_investigations import paired_loads
    from .normalization_repairs import frame_gap
    register_priority = (
            'unsigned_byte_increment', 'return_contract', 'narrow_expression_inline', 'plain_float_slots',
            'narrow_call_destination', 'branch_scalar_lifetime', 'embedded_call_assignment', 'inline_call_count', 'typed_pointer_snapshot',
            'shared_scaled_offset', 'pointer_conditional_load_inline', 'branch_store_tail', 'address_association',
            'dead_index_scale', 'dead_pointer_clear', 'store_index_reuse', 'split_mask_assignment',
            'existing_identity_lifetime', 'shared_store_constant', 'nested_halfword_arithmetic',
            'block_accumulator_lifetime', 'inline_copy_loop', 'typed_command_record', 'pointer_index_scope', 'pointer_index_order', 'store_value_lifetime', 'assignment_chain_lifetime', 'shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'float_call_staging', 'inline_scalar_chain', 'paired_operand_reuse', 'cfg_equal_value_increment', 'equal_value_increment', 'loop_weight_scope', 'decision_address_bundle', 'qualified_selector_load', 'selector_address_staging', 'single_case_switch', 'halfword_arithmetic', 'branch_pointer_lifetimes', 'branch_pointer_declarations', 'typed_clamp_load', 'typed_pointer_load', 'loop_update_order', 'pointer_argument_alias', 'allocation_schedule_bridge', 'allocation_initialization_order', 'byte_fill_constant', 'spill_declaration_order', 'record_value_forwarding', 'narrow_definition_lifetime', 'sink_zero_initialization', 'structured_backedge', 'pointer_call_staging', 'pointer_load_inline', 'word_zero_test_reuse', 'pointer_local_reuse', 'independent_field_store_order', 'paired_pointer_declarations', 'copy_register_reuse', 'constant_index_temporary', 'balanced_call_references', 'balanced_pointer_calls', 'call_return_normalization', 'split_narrow_lifetime', 'combined_call_lifetimes', 'branch_tail_lifetimes', 'staged_call_arguments', 'single_field_record', 'record_access_shape', 'split_call_result', 'direct_mask_condition', 'inline_scalar_shape', 'expression_placement', 'scalar_schedule_order', 'combined_scheduling', 'direct_compound_store', 'address_association', 'masked_value_reuse', 'paired_integer_lifetimes', 'split_integer_phase', 'cfg_integer_lifetimes', 'constant_register_reuse', 'last_shift_reuse', 'unsigned_assignment_reuse', 'table_slot_lifetime', 'store_address_lifetime', 'pointer_cast_inline', 'comparison_reuse', 'macro_byte_address_order', 'integer_address_order',
            'comparison_operand_order', 'integer_product_order', 'arithmetic_operand_order', 'typed_temporary', 'value_lifetime', 'split_integer_lifetime', 'merge_integer_lifetimes',
            'split_scalar_lifetime', 'array_index_width', 'local_width', 'declaration_order')
    if diagnose(rows)['register_cycles']:
        preferred = ('later_local_reuse','pointer_integer_phase_union','paired_integer_lifetimes','split_integer_lifetime',
                     'merge_integer_lifetimes','block_accumulator_lifetime','local_declaration_order') + register_priority
    elif diagnose(rows)['register_chains'] and diagnose(rows)['register_only']:
        preferred = ('pointer_local_reuse','pointer_load_inline','pointer_argument_alias','paired_pointer_declarations',
                     'copy_register_reuse','split_integer_lifetime','merge_integer_lifetimes','local_declaration_order') + register_priority
    elif focus=='control' and (diagnose(rows)['branch_target'] or
            (diagnose(rows)['delay_slot'] and not diagnose(rows)['scheduling'])):
        preferred = ('structured_backedge','guard_forward_jump','branch_orientation','loop_update_order',
                     'goto_leaf','shared_return_tail','redundant_loop_guard','combined_scheduling')
    elif paired_loads(rows):
        preferred = ('paired_load_order','scalar_schedule_order','local_declaration_order','small_helper_expansion','combined_scheduling')
    elif markers and markers <= {'s'}:
        preferred = ('argument_home_slot', 'callee_array_extent', 'modular_guard_width', 'local_width', 'spill_normalization', 'unused_scalar_local',
                     'local_storage', 'parameter_storage', 'local_declaration_order', 'stack_padding')
    elif frame_gap(rows):
        preferred = ('argument_home_slot', 'callee_array_extent', 'spill_normalization', 'split_narrow_lifetime', 'call_return_normalization',
                     'stack_address_temporary', 'combined_scheduling', 'stack_padding')
    elif diagnose(rows)['register_only'] or diagnose(rows)['copy_near_miss']:
        preferred = ('later_local_reuse','later_operation_reuse','later_call_reuse','branch_local_reuse','cfg_equal_value_increment','equal_value_increment','loop_weight_scope',
                     'shared_branch_locals','paired_operand_reuse','allocation_schedule_bridge','copy_register_reuse','masked_value_reuse') + register_priority
    elif diagnose(rows)['scheduling']:
        preferred = ('unsigned_byte_increment','embedded_call_assignment','inline_call_count','typed_pointer_snapshot','call_one_shot_scope', 'debug_initialization_group', 'debug_constant_initialization', 'typed_global_identity', 'block_accumulator_lifetime', 'shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'affine_counter_guard', 'affine_counter', 'initialization_cluster','allocation_initialization_order','byte_fill_constant','scalar_schedule_order','combined_scheduling')
    elif diagnose(rows)['allocation_schedule']:
        preferred = ('unsigned_byte_increment','narrow_call_destination','branch_scalar_lifetime','inline_call_count','embedded_call_assignment','typed_pointer_snapshot','call_one_shot_scope', 'debug_initialization_group', 'paired_operand_reuse', 'affine_counter_guard', 'affine_counter',
                     'allocation_initialization_order', 'scalar_schedule_order', 'combined_scheduling')
    elif focus == 'call':
        preferred = ('target_callee_name', 'overlay_callee_alias', 'untouched_argument_forwarding', 'float_call_staging', 'success_pointer_update', 'branch_pointer_lifetimes', 'branch_pointer_declarations', 'known_call_excess_arguments', 'known_call_signature', 'call_postincrement', 'target_call_argument', 'target_local_bundle', 'float_argument_bits', 'runtime_scalar_signature', 'call_forwarded_arguments', 'call_arity', 'callee_parameter_width', 'callee_return_width', 'call_result_inline', 'parameter_width', 'parameter_storage')
    elif markers and markers <= {'r', 's'}:
        preferred = ('callee_buffer_extent', 'local_declaration_order', 'spill_declaration_order', 'small_helper_expansion', 'stack_address_temporary', 'combined_scheduling', 'stack_padding', 'unused_scalar_local', 'local_storage', 'parameter_storage',
                     'spill_declaration_order', 'allocation_schedule_bridge', 'local_declaration_order', 'stack_array', 'parameter_width', 'declaration_order') if 's' in markers else register_priority
    elif 'r' in markers and markers <= {'r', 'i'}:
        # Address rendering can coexist with a register near miss. Keep address
        # hypotheses first; these are advisory priorities, never address proofs.
        preferred = ('scalar_global_address', 'macro_byte_address_order', 'integer_address_order', 'call_return_normalization',
                     'split_narrow_lifetime', 'inline_scalar_shape', 'local_widening', 'signedness')
    elif markers & {'<', '>', '|', 'o'}:
        preferred = ('typed_global_identity', 'independent_address_materialization', 'decision_address_bundle', 'single_case_switch', 'halfword_arithmetic', 'shared_flag_guard', 'count_lifetime_bundle', 'count_load_qualification', 'count_phase_lifetime', 'normalization_scope', 'target_call_argument', 'known_address_alias', 'single_field_record', 'target_local_bundle', 'structured_backedge', 'loop_invariant_placement', 'combined_scheduling', 'shared_return_tail', 'redundant_loop_guard', 'call_initialization', 'address_association', 'paired_load_order', 'pointer_before_store', 'guard_forward_jump', 'branch_call', 'goto_leaf', 'switch_layout',
                     'branch_orientation', 'loop_induction', 'callee_parameter_width', 'call_result_inline', 'parameter_width')
    elif 'i' in markers:
        preferred = ('known_address_alias', 'target_call_argument', 'target_local_bundle', 'local_widening', 'macro_byte_address_order', 'integer_address_order', 'signedness', 'narrowing', 'constant_local')
    else:
        preferred=()
    preferred=(('debug_conversion_tail',) if focus=='control' else ())+(('delayed_narrow_capture','shared_shift_mask') if focus in {'register','call'} or diagnose(rows)['scheduling'] else ())+('plain_float_slots','return_contract','narrow_expression_inline')+preferred
    if diagnose(rows)['register_only']:
        preferred = ('dead_copy_source','divisor_normalization_copy', 'divisor_value_scope', 'divisor_flag_split', 'regional_load_reuse') + preferred
    elif diagnose(rows)['scheduling'] or diagnose(rows)['delay_slot']:
        preferred = ('debug_statement_boundary',) + preferred
    preferred=('hazard_statement_lines','known_zero_mask','post_call_narrow_capture')+preferred

    # These families only exist after a narrow target/source guard. Generic delay-slot
    # or register-shape heuristics must not spend the whole budget before that operation.
    preferred=GUARDED+('mapped_spill_storage','mapped_register_lifetime')+preferred
    ranked=sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))
    from .trial_evidence import order as evidence_order
    return evidence_order(ranked,rows)


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
