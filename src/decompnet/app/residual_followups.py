"""Prioritize the new residual after a measured local recovery; never change adoption rules."""
from . import allocation_evidence, repair_selection, search_frontier

BURSTS = 3
PROBES = 2
FAMILIES = {
    'scheduling': ('delayed_narrow_capture','debug_conversion_tail','shared_shift_mask','plain_float_slots','return_contract','narrow_expression_inline','embedded_call_assignment','inline_call_count','typed_pointer_snapshot','call_one_shot_scope','debug_initialization_group','debug_constant_initialization','allocation_initialization_order','scalar_schedule_order','paired_load_order',
                   'combined_scheduling','local_declaration_order','declaration_order','byte_fill_constant'),
    'allocation': ('delayed_narrow_capture','shared_shift_mask','return_contract','narrow_expression_inline','plain_float_slots','narrow_call_destination','branch_scalar_lifetime','later_local_reuse','later_operation_reuse','later_call_reuse','branch_local_reuse',
                   'split_integer_phase','split_integer_lifetime','merge_integer_lifetimes','loop_weight_scope'),
}


def kind(before, after, arch=None):
    """Only complete, same-length rows with an actual repaired target location earn a follow-up."""
    if not before or not after or not (search_frontier.mismatch_positions(before)-search_frontier.mismatch_positions(after)):
        return None
    diagnosis=allocation_evidence.diagnose(after,arch)
    if not diagnosis['target_instructions'] or diagnosis['target_instructions']!=diagnosis['candidate_instructions']:
        return None
    if diagnosis['target_frame']!=diagnosis['candidate_frame']:
        return None
    if diagnosis['register_only']:return 'allocation'
    if diagnosis['scheduling']:return 'scheduling'
    return None


def order(proposals, before, after, arch=None):
    category=kind(before,after,arch)
    if not category:return proposals
    preferred=FAMILIES[category]
    items=sorted(proposals,key=lambda p:preferred.index(p[0]) if isinstance(p,tuple) and p[0] in preferred else len(preferred))
    return repair_selection.Page(items,more=proposals.more) if hasattr(proposals,'more') else items


def select(proposals, before, after, arch=None):
    category=kind(before,after,arch)
    if not category:return []
    return [p for p in order(proposals,before,after,arch) if p[0] in FAMILIES[category]][:PROBES]
