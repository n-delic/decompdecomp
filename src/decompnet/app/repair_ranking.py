"""Rank applicable hypotheses by observed mismatches without excluding other families."""

import hashlib
import re

STRUCTURAL = ('scratch_role_reuse','scalar_storage_reuse','row_number_staging','explicit_loop_backedge','integer_array_pointer_phase','inferred_record_layout','scalar_normalization_scope')
GUARDED = ('matched_zero_argument_call', 'zero_argument_parameter_alias', 'matched_integer_prototype', 'mips_debug_break','live_return_address','live_return_address_initializers', 'kmc_address_operand_order', 'kmc_float_comparison_order',
           'callee_array_extent', 'callee_buffer_extent', 'loop_sentinel_placement')


def four_word_transfer(instructions):
    # Require a contiguous sequence on each base and the same values in the stores.
    decoded = [re.fullmatch(r'\w+\s+(\$\w+),\s*(-?(?:0x[0-9a-fA-F]+|[0-9]+))\((\$\w+)\)', op.strip())
               for op in instructions]
    if not all(decoded):
        return False
    for block in (decoded[:4], decoded[4:]):
        offsets = [int(entry[2], 16 if '0x' in entry[2] else 10) for entry in block]
        if len({entry[3] for entry in block}) != 1 or offsets != list(range(offsets[0], offsets[0]+16, 4)):
            return False
    values = [entry[1] for entry in decoded[:4]]
    return len(set(values)) == 4 and values == [entry[1] for entry in decoded[4:]]


def instruction_rows(rows):
    return [row for row in rows if isinstance(row, (list, tuple)) and len(row) == 3
            and all(isinstance(cell, str) for cell in row)]


def matching_topics(rows):
    """Recognize narrow MIPS patterns that select an existing repair or a brief tip."""
    def opcode(cell):
        parts = cell.split(None, 1)
        return parts[0] if parts else ''

    rows = instruction_rows(rows)
    target_rows = [(left, marker) for marker, left, _ in rows if left]
    target = [(opcode(left), marker) for left, marker in target_rows]
    changed = [(opcode(left), opcode(right)) for marker, left, right in rows if marker != ' ']
    topics = []
    for load, store in (('lw', 'sw'), ('lwc1', 'swc1')):
        for i in range(len(target)-5):
            ops, markers = zip(*target[i:i+6])
            if (ops == (load,)*3 + (store,)*3 and any(marker != ' ' for marker in markers)
                    and (i == 0 or target[i-1][0] != load)
                    and (i+6 == len(target) or target[i+6][0] != store)):
                topics.append('Grouped copies')
                break
        if topics:
            break
    if not topics:
        for i in range(len(target)-7):
            ops, markers = zip(*target[i:i+8])
            if (ops in (('lw',)*4 + ('sw',)*4, ('lwc1',)*4 + ('swc1',)*4)
                    and any(marker != ' ' for marker in markers)
                    and four_word_transfer([text for text, _ in target_rows[i:i+8]])):
                topics.append('Grouped copies')
                break
    widths = {'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwc1', 'ldc1'}
    if any(a in widths and b in widths and a != b for a, b in changed):
        topics.append('Load width and indexing')
    if any(a in {'jal', 'jalr'} or b in {'jal', 'jalr', 'cvt.d.s', 'cvt.s.d'} for a, b in changed):
        topics.append('Call signatures')
    if any(op in {'trunc.w.s', 'trunc.w.d'} and marker != ' ' for op, marker in target):
        topics.append('Unsigned conversions')
    return topics


TOPIC_REPAIRS = {
    'Grouped copies': ('global_record_copy', 'word_block_copy', 'float_record_copy'),
    'Load width and indexing': ('field_width', 'symbol_access_width', 'pointer_byte_step', 'pointer_walk_index'),
    'Call signatures': ('float_argument_bits', 'float_prototype', 'matched_integer_prototype', 'passthrough_argument'),
    'Unsigned conversions': ('float_unsigned_conversion',),
}


def guarded_first(candidates, labels=None):
    """Finite target-backed proposals precede generic learned or localisation hypotheses."""
    family=lambda c:labels.get(c,'') if labels is not None else c
    return sorted(candidates,key=lambda c:GUARDED.index(family(c)) if family(c) in GUARDED else len(GUARDED))


def order(families, rows):
    from .allocation_evidence import diagnose
    from .permuter_focus import classify, normalized
    rows = normalized(rows)
    focus = classify(rows)
    diagnosis = diagnose(rows)
    markers = {row[0] for row in rows if row[0] != ' '}
    from decomprepair.normalization_repairs import frame_gap

    from .matching_investigations import paired_loads
    register_priority = (
            'record_layout_sequence', 'branch_packing_tail', 'frame_branch_tail_sequence', 'inferred_frame_reservation', 'record_lifetime_sequence', 'pointer_result_reuse', 'coordinated_field_store_order', 'narrow_store_sequence', 'dead_assignment_narrow_capture', 'narrow_field_update_fold',
            'scratch_role_reuse',
            'narrow_carrier_sequence', 'parameter_carriers',
            'inline_byte_address_bundle', 'inline_byte_address', 'debug_branch_scope', 'loaded_byte_compound', 'unsigned_byte_increment', 'return_contract', 'narrow_expression_inline', 'plain_float_slots',
            'narrow_call_destination', 'branch_scalar_lifetime', 'embedded_call_assignment', 'inline_call_count', 'typed_pointer_snapshot',
            'shared_scaled_offset', 'pointer_conditional_load_inline', 'branch_store_tail', 'address_association', 'loop_address_hoist', 'loop_counter_fresh', 'loop_counter_reassign', 'loop_counter_merge', 'loop_counter_swap', 'switch_arm_store',
            'dead_index_scale', 'dead_pointer_clear', 'store_index_reuse', 'split_mask_assignment',
            'existing_identity_lifetime', 'shared_store_constant', 'nested_halfword_arithmetic',
            'block_accumulator_lifetime', 'inline_copy_loop', 'typed_command_record', 'pointer_index_scope', 'pointer_index_order', 'store_value_lifetime', 'store_result_reuse', 'assignment_chain_lifetime', 'shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'float_call_staging', 'inline_scalar_chain', 'paired_operand_reuse', 'cfg_equal_value_alias', 'cfg_equal_value_increment', 'equal_value_increment', 'loop_weight_scope', 'switch_constant_dispatch', 'decision_address_bundle', 'qualified_selector_load', 'selector_address_staging', 'single_case_switch', 'halfword_arithmetic', 'branch_pointer_lifetimes', 'branch_pointer_declarations', 'typed_clamp_load', 'typed_pointer_load', 'loop_update_order', 'pointer_argument_alias', 'allocation_schedule_bridge', 'allocation_initialization_order', 'byte_fill_constant', 'spill_declaration_order', 'record_value_forwarding', 'narrow_definition_lifetime', 'sink_zero_initialization', 'structured_backedge', 'pointer_call_staging', 'pointer_load_inline', 'word_zero_test_reuse', 'pointer_capture_reuse', 'pointer_local_reuse', 'independent_field_store_order', 'paired_pointer_declarations', 'copy_register_reuse', 'constant_index_temporary', 'balanced_call_references', 'balanced_pointer_calls', 'call_return_normalization', 'split_narrow_lifetime', 'combined_call_lifetimes', 'branch_tail_lifetimes', 'staged_call_arguments', 'single_field_record', 'record_access_shape', 'split_call_result', 'direct_mask_condition', 'inline_scalar_shape', 'expression_placement', 'scalar_schedule_order', 'combined_scheduling', 'direct_compound_store', 'address_association', 'masked_value_reuse', 'paired_integer_lifetimes', 'split_integer_phase', 'cfg_integer_lifetimes', 'constant_register_reuse', 'last_shift_reuse', 'unsigned_assignment_reuse', 'table_slot_lifetime', 'store_address_lifetime', 'pointer_cast_inline', 'comparison_reuse', 'macro_byte_address_order', 'integer_address_order',
            'comparison_operand_order', 'integer_product_order', 'arithmetic_operand_order', 'typed_temporary', 'value_lifetime', 'split_integer_lifetime', 'merge_integer_lifetimes',
            'split_scalar_lifetime', 'array_index_width', 'local_width', 'declaration_order',
            'case_temporary_merge', 'case_temporary_split', 'phantom_wide_local')
    from . import frame_plateau
    if diagnosis['frame_only_gap'] or frame_plateau.gap(rows,'mips'):
        preferred = ('callee_buffer_extent','callee_array_extent','halfword_store_normalization','spill_normalization','split_narrow_lifetime','call_return_normalization')
    elif diagnosis['return_copy_rotation']:
        preferred = ('prepared_return_scope','prepared_call_scope','assigned_call_scope','call_one_shot_scope')
    elif diagnosis['register_cycles']:
        preferred = ('later_local_reuse','pointer_integer_phase_union','paired_integer_lifetimes','split_integer_lifetime',
                     'merge_integer_lifetimes','block_accumulator_lifetime','local_declaration_order') + register_priority
    elif diagnosis['register_chains'] and diagnosis['register_only']:
        preferred = ('pointer_capture_reuse', 'pointer_local_reuse','pointer_load_inline','pointer_argument_alias','paired_pointer_declarations',
                     'copy_register_reuse','split_integer_lifetime','merge_integer_lifetimes','local_declaration_order') + register_priority
    elif focus=='control' and (diagnosis['branch_target'] or
            (diagnosis['delay_slot'] and not diagnosis['scheduling'])):
        preferred = ('switch_constant_dispatch', 'unsigned_countdown_guard','structured_backedge','guard_forward_jump','branch_orientation','loop_update_order',
                     'goto_leaf','shared_return_tail','redundant_loop_guard','combined_scheduling')
    elif paired_loads(rows):
        preferred = ('narrow_carrier_sequence','narrow_load_subtraction','one_shot_scope','paired_load_order','scalar_schedule_order','local_declaration_order','small_helper_expansion','combined_scheduling')
    elif markers and markers <= {'s'}:
        preferred = ('argument_home_slot', 'callee_array_extent', 'modular_guard_width', 'local_width', 'spill_normalization', 'unused_scalar_local',
                     'local_storage', 'parameter_storage', 'local_declaration_order', 'stack_padding')
    elif frame_gap(rows):
        preferred = ('argument_home_slot', 'callee_array_extent', 'spill_normalization', 'split_narrow_lifetime', 'call_return_normalization',
                     'stack_address_temporary', 'combined_scheduling', 'stack_padding')
    elif diagnosis['register_only'] or diagnosis['copy_near_miss']:
        preferred = ('cfg_equal_value_alias','later_local_reuse','later_operation_reuse','later_call_reuse','branch_local_reuse','cfg_equal_value_increment','equal_value_increment','loop_weight_scope',
                     'shared_branch_locals','paired_operand_reuse','allocation_schedule_bridge','copy_register_reuse','masked_value_reuse') + register_priority
    elif diagnosis['scheduling']:
        preferred = ('record_layout_sequence', 'branch_packing_tail', 'frame_branch_tail_sequence', 'inferred_frame_reservation', 'record_lifetime_sequence', 'pointer_result_reuse', 'coordinated_field_store_order', 'narrow_store_sequence', 'dead_assignment_narrow_capture', 'narrow_field_update_fold', 'independent_field_store_order', 'inline_byte_address_bundle', 'inline_byte_address', 'debug_branch_scope', 'loaded_byte_compound', 'unsigned_byte_increment','embedded_call_assignment','inline_call_count','typed_pointer_snapshot','call_one_shot_scope', 'debug_initialization_group', 'debug_constant_initialization', 'typed_global_identity', 'block_accumulator_lifetime', 'shared_branch_locals', 'loop_exit_value', 'success_pointer_update', 'affine_counter_guard', 'affine_counter', 'initialization_cluster','allocation_initialization_order','byte_fill_constant','scalar_schedule_order','combined_scheduling')
    elif diagnosis['allocation_schedule']:
        preferred = ('record_layout_sequence', 'branch_packing_tail', 'frame_branch_tail_sequence', 'inferred_frame_reservation', 'record_lifetime_sequence', 'pointer_result_reuse', 'coordinated_field_store_order', 'narrow_store_sequence', 'dead_assignment_narrow_capture', 'narrow_field_update_fold', 'independent_field_store_order', 'inline_byte_address_bundle', 'inline_byte_address', 'debug_branch_scope', 'loaded_byte_compound', 'unsigned_byte_increment','narrow_call_destination','branch_scalar_lifetime','inline_call_count','embedded_call_assignment','typed_pointer_snapshot','call_one_shot_scope', 'debug_initialization_group', 'paired_operand_reuse', 'affine_counter_guard', 'affine_counter',
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
        preferred = ('typed_global_identity', 'independent_address_materialization', 'switch_constant_dispatch', 'decision_address_bundle', 'single_case_switch', 'halfword_arithmetic', 'shared_flag_guard', 'count_lifetime_bundle', 'count_load_qualification', 'count_phase_lifetime', 'normalization_scope', 'target_call_argument', 'known_address_alias', 'single_field_record', 'target_local_bundle', 'structured_backedge', 'loop_invariant_placement', 'combined_scheduling', 'shared_return_tail', 'redundant_loop_guard', 'call_initialization', 'address_association', 'paired_load_order', 'pointer_before_store', 'guard_forward_jump', 'branch_call', 'goto_leaf', 'switch_layout',
                     'branch_orientation', 'loop_induction', 'callee_parameter_width', 'call_result_inline', 'parameter_width')
    elif 'i' in markers:
        preferred = ('switch_constant_dispatch', 'known_address_alias', 'target_call_argument', 'target_local_bundle', 'local_widening', 'macro_byte_address_order', 'integer_address_order', 'signedness', 'narrowing', 'constant_local')
    else:
        preferred=()
    preferred=(('debug_conversion_tail',) if focus=='control' else ())+(('delayed_narrow_capture','shared_shift_mask') if focus in {'register','call'} or diagnosis['scheduling'] else ())+('plain_float_slots','return_contract','narrow_expression_inline')+preferred
    if diagnosis['register_only']:
        preferred = ('dead_copy_source','divisor_normalization_copy', 'divisor_value_scope', 'divisor_flag_split', 'regional_load_reuse') + preferred
    elif diagnosis['scheduling'] or diagnosis['delay_slot']:
        preferred = ('debug_statement_boundary',) + preferred
    preferred=('hazard_statement_lines','known_zero_mask','post_call_narrow_capture')+preferred
    if diagnosis['register_only'] or diagnosis['scheduling'] or diagnosis['allocation_schedule']:
        preferred=('inline_bit_predicate','masked_role_sequence','staged_bit_predicate','operation_scope_merge','operation_scope_unwrap','operation_scope_depth')+preferred

    # These families only exist after a narrow target/source guard. Generic delay-slot
    # or register-shape heuristics must not spend the whole budget before that operation.
    preferred=GUARDED+('m2c_refinement',)+STRUCTURAL+('mapped_spill_storage','mapped_register_lifetime')+preferred
    if diagnosis['register_only']:
        preferred=GUARDED+('scratch_role_reuse','parameter_carriers')+preferred
    elif diagnosis['scheduling']:
        preferred=GUARDED+('narrow_load_subtraction','one_shot_scope')+preferred
    if focus == 'scheduling':
        preferred = GUARDED + ('revisit_one_shot_scope', 'aggregate_call_preparation',
            'call_preparation_bundle', 'aggregate_pointer_dispatch', 'debug_statement_boundary',
            'scalar_schedule_order') + preferred
    from .snapshot_branch_repairs import missing_masks
    if missing_masks(rows):
        preferred=GUARDED+('narrow_snapshot_branches','local_width','inline_scalar_shape')+preferred
    focused = tuple(family for topic in matching_topics(rows) for family in TOPIC_REPAIRS[topic])
    preferred = focused + preferred
    ranked=sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))
    from .trial_evidence import order as evidence_order
    return evidence_order(ranked,rows,evidence=diagnosis)


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


def remember(net, fn, scope, baseline, observations):
    """Keep distinct scored trials for this exact parent and build, across workers."""
    if not baseline or not baseline.get('rows') or baseline.get('score') is None or not scope:
        return
    evidence = []
    for family, source, measured in observations:
        if (source == baseline.get('source') or not measured or measured.get('cached')
                or not measured.get('rows') or measured.get('score') is None):
            continue
        if baseline.get('input_scope') and measured.get('input_scope') and baseline['input_scope'] != measured['input_scope']:
            continue
        same = baseline['score'] == measured['score'] and baseline['rows'] == measured['rows']
        before = baseline.get('output_fingerprint')
        after = measured.get('output_fingerprint')
        if before and after:
            same = same and before == after
        outcome = 'same:' if same else 'changed:'
        evidence.append((fn['id'], scope, family, outcome + hashlib.sha256(source.encode()).hexdigest(),
                         fn['id'], scope, family, outcome + '%', 2 if same else 1))
    if evidence:
        from . import trial_bookkeeping
        sql = """INSERT OR IGNORE INTO agent_family_targets (function_id,scope,family,target_hash)
                 SELECT ?,?,?,? WHERE (SELECT COUNT(*) FROM agent_family_targets
                   WHERE function_id=? AND scope=? AND family=? AND target_hash LIKE ?) < ?"""
        trial_bookkeeping.submit(net.project_store(fn['project_id']), [(sql, row) for row in evidence])


def no_effect(net, fn, scope):
    """Two different measured edits with no changed output are enough to lower priority."""
    if not scope:
        return set()
    from . import trial_bookkeeping

    store = net.project_store(fn['project_id'])
    trial_bookkeeping.flush(store)
    return {row['family'] for row in store.all(
        """SELECT family FROM agent_family_targets WHERE function_id=? AND scope=? GROUP BY family
           HAVING SUM(target_hash LIKE 'same:%') >= 2 AND SUM(target_hash LIKE 'changed:%') = 0""",
        fn['id'], scope)}


def demote(net, fn, scope, families):
    ineffective = no_effect(net, fn, scope)
    return sorted(families, key=lambda family: family in ineffective)


def effect_scope(net, fn, source):
    """Family demotion belongs to this exact parent, not all future source shapes."""
    from .agent_tokens import target_scope
    key = hashlib.sha256((target_scope(net, fn, '') + source).encode()).hexdigest()
    return 'repair-effect:' + key


def private_abi_order(families, arch, asm, rows):
    """Defer ordinary entry-signature searches when the measured mismatch needs caller context."""
    from .. import arch as arches
    reader=arches.get(arch).source_private_abi
    if not reader or not reader(asm,rows):return list(families)
    ordinary={'runtime_scalar_signature','parameter_width','parameter_storage','float_argument_bits'}
    return sorted(families,key=lambda family:family in ordinary)
