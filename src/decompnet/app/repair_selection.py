"""Resumable proposal pages and bounded, evidence-led repair combinations."""
import hashlib


class Page(list):
    def __init__(self, items=(), *, more=False):
        super().__init__(items)
        self.more = more


FOLLOWUP_WINDOW = 16

COMPLEMENTS = {
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
    'loop_weight_scope': ('cfg_equal_value_increment', 'equal_value_increment'),
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
