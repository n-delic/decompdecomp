"""Resumable proposal pages and bounded, evidence-led repair combinations."""
import hashlib


class Page(list):
    def __init__(self, items=(), *, more=False):
        super().__init__(items)
        self.more = more


FOLLOWUP_WINDOW = 16

COMPLEMENTS = {
    'inline_bit_predicate': ('operation_scope_unwrap','operation_scope_merge','operation_scope_depth'),
    'staged_bit_predicate': ('masked_role_sequence','scalar_storage_reuse','operation_scope_merge'),
    'masked_role_sequence': ('inline_bit_predicate','operation_scope_depth'),
    'operation_scope_merge': ('inline_bit_predicate','staged_bit_predicate'),
    'operation_scope_unwrap': ('inline_bit_predicate','masked_role_sequence'),
    'operation_scope_depth': ('inline_bit_predicate','scalar_storage_reuse'),
    'narrow_carrier_sequence': ('parameter_carriers','narrow_load_subtraction','one_shot_scope'),
    'loop_test_snapshot': ('scalar_storage_reuse','staged_scalar_arithmetic','explicit_loop_backedge','loop_constant_snapshot'),
    'staged_scalar_arithmetic': ('targeted_debug_layout','unscaled_address_snapshot','loop_test_snapshot'),
    'unscaled_address_snapshot': ('byte_pointer_operand_order','targeted_debug_layout','loop_constant_snapshot'),
    'byte_pointer_operand_order': ('unscaled_address_snapshot','targeted_debug_layout'),
    'loop_constant_snapshot': ('explicit_loop_backedge','staged_scalar_arithmetic','byte_pointer_operand_order'),
    'effectful_dispatch_specialization': ('saved_layout_followup','targeted_debug_layout','revisit_one_shot_scope'),
    'inferred_frame_reservation': ('branch_packing_tail',),
    'branch_packing_tail': ('inferred_frame_reservation',),
    'aggregate_pointer_dispatch': ('call_preparation_bundle', 'aggregate_call_preparation', 'revisit_one_shot_scope'),
    'call_preparation_bundle': ('aggregate_pointer_dispatch', 'revisit_one_shot_scope'),
    'aggregate_call_preparation': ('revisit_one_shot_scope',),
    'pointer_result_reuse': ('coordinated_field_store_order','narrow_store_sequence'),
    'coordinated_field_store_order': ('narrow_store_sequence','pointer_result_reuse'),
    'dead_assignment_narrow_capture': ('narrow_field_update_fold','independent_field_store_order'),
    'narrow_field_update_fold': ('dead_assignment_narrow_capture','independent_field_store_order'),
    'independent_field_store_order': ('dead_assignment_narrow_capture','narrow_field_update_fold'),
    'scratch_role_reuse': ('scratch_role_reuse','scalar_storage_reuse'),
    'one_shot_scope': ('narrow_load_subtraction','parameter_carriers'),
    'narrow_load_subtraction': ('parameter_carriers','one_shot_scope'),
    'parameter_carriers': ('narrow_load_subtraction','one_shot_scope'),
    'scalar_storage_reuse': ('row_number_staging','explicit_loop_backedge','debug_constant_initialization'),
    'row_number_staging': ('branch_integer_constant','scalar_storage_reuse','explicit_loop_backedge'),
    'explicit_loop_backedge': ('loop_constant_snapshot','row_number_staging','branch_integer_constant','scalar_storage_reuse'),
    'branch_integer_constant': ('row_number_staging','pure_preheader_order'),
    'debug_constant_initialization': ('scalar_storage_reuse','row_number_staging'),
    'divisor_normalization_copy': ('dead_copy_source','divisor_flag_split'),
    'divisor_value_scope': ('dead_copy_source','divisor_flag_split'),
    'divisor_flag_split': ('dead_copy_source','divisor_normalization_copy'),
    'dead_copy_source': ('divisor_normalization_copy','divisor_value_scope','debug_statement_boundary'),
    'known_zero_mask': ('post_call_narrow_capture','hazard_statement_lines'),
    'post_call_narrow_capture': ('hazard_statement_lines','known_zero_mask'),
    'hazard_statement_lines': ('post_call_narrow_capture','known_zero_mask'),

    'live_return_address': ('live_return_address_initializers',),
    'mips_debug_break': ('shared_shift_mask','delayed_narrow_capture','debug_conversion_tail'),
    'shared_shift_mask': ('delayed_narrow_capture','debug_conversion_tail'),
    'delayed_narrow_capture': ('debug_conversion_tail','shared_shift_mask'),
    'debug_conversion_tail': ('delayed_narrow_capture','shared_shift_mask'),
    'plain_float_slots': ('return_contract','narrow_expression_inline'),
    'return_contract': ('plain_float_slots','narrow_expression_inline'),
    'narrow_expression_inline': ('return_contract','plain_float_slots'),
    'branch_scalar_lifetime': ('inline_call_count','embedded_call_assignment','typed_pointer_snapshot'),
    'inline_call_count': ('branch_scalar_lifetime','embedded_call_assignment','typed_pointer_snapshot'),
    'embedded_call_assignment': ('branch_scalar_lifetime','inline_call_count','typed_pointer_snapshot','narrow_call_destination'),
    'narrow_call_destination': ('branch_scalar_lifetime','inline_call_count','embedded_call_assignment','typed_pointer_snapshot'),
    'typed_pointer_snapshot': ('narrow_call_destination','branch_scalar_lifetime','inline_call_count','embedded_call_assignment'),
    'loop_weight_scope': ('cfg_equal_value_alias', 'cfg_equal_value_increment', 'equal_value_increment'),
    'cfg_equal_value_alias': ('loop_weight_scope',),
    'cfg_equal_value_increment': ('loop_weight_scope',),
    'equal_value_increment': ('loop_weight_scope',),
    'paired_operand_reuse': ('affine_counter_guard', 'affine_counter'),
    'affine_counter': ('paired_operand_reuse', 'affine_counter_guard'),
    'affine_counter_guard': ('paired_operand_reuse',),
}


def order(families, path):
    preferred = COMPLEMENTS.get(path[-1], ()) if path else ()
    return sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))


def page(proposals, source, cursors, limit):
    """Cursor belongs to the exact source and ordered proposal pool; outer frontier owns build scope."""
    if limit < 1:
        raise ValueError('A repair page needs a positive limit.')
    proposals = list(dict.fromkeys(proposals))
    digest = hashlib.sha256(source.encode())
    for family, candidate in proposals:
        digest.update(repr((family, hashlib.sha256(candidate.encode()).hexdigest())).encode())
    key = digest.hexdigest()
    start = cursors.get(key, 0)
    stop = min(len(proposals), start+limit)
    cursors[key] = stop
    return Page(proposals[start:stop], more=stop < len(proposals))
