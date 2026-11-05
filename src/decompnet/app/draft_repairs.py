"""Small deterministic hypotheses before the random permuter's near-miss cutoff.

These are candidate rewrites, not equivalence proofs. Only the ordinary compiler and exact judge may keep
one. Work is restricted to the requested definition; strings, comments and preprocessor lines are opaque.
"""

import re

from .. import m2cfix, seeds
from ..forks.csource import _blank, find
from . import (
    address_association_repairs,
    affine_operand_repairs,
    allocation_repairs,
    allocator_shape_repairs,
    block_accumulator_repairs,
    branch_layout_repairs,
    branch_locals,
    branch_pointer_repairs,
    branch_scalar_repairs,
    buffer_address_repairs,
    call_assignment_repairs,
    call_count_repairs,
    call_initialization_repairs,
    call_lifetime_repairs,
    call_scope_repairs,
    callee_prototype_repairs,
    capture_followup_repairs,
    captured_control_repairs,
    captured_index_repairs,
    captured_lifetimes,
    captured_repairs,
    compiler_shape_repairs,
    conversion_scheduling_repairs,
    copy_lifetime_repairs,
    copy_shape_repairs,
    count_lifetime_repairs,
    debug_boundary_repairs,
    debug_initializer_repairs,
    decision_address_repairs,
    entry_point_repairs,
    equal_value_flow,
    evidence_repairs,
    example_lifetime_repairs,
    expression_repairs,
    field_order_repairs,
    float_intermediate_repairs,
    float_storage_repairs,
    focused_lifetime_repairs,
    formatter_repairs,
    global_identity_repairs,
    global_store_repairs,
    hardware_operation_repairs,
    helper_expansion,
    identity_lifetime_repairs,
    increment_expression_repairs,
    inferred_layout_repairs,
    initializer_clusters,
    inline_scheduling_repairs,
    integer_lifetime_repairs,
    later_local_repairs,
    lifetime_repairs,
    live_return_address_repairs,
    load_pair_repairs,
    local_widening_repairs,
    local_width_repairs,
    loop_weight_repairs,
    macro_repairs,
    masked_reuse_repairs,
    match_followup_repairs,
    match_shape_repairs,
    mmio_repairs,
    narrow_call_repairs,
    narrow_expression_repairs,
    near_match_repairs,
    near_repairs,
    normalization_repairs,
    observed_repairs,
    operand_order_repairs,
    pattern_repairs,
    phase_lifetime_repairs,
    phase_union_repairs,
    pointer_cast_repairs,
    pointer_index_scope,
    pointer_pair_repairs,
    pointer_snapshot_repairs,
    raw_address_repairs,
    recent_capture_repairs,
    record_lifetime_repairs,
    register_repairs,
    register_reuse_repairs,
    return_contract_repairs,
    scheduling_repairs,
    sdk_io_repairs,
    sdk_shape_repairs,
    shared_offset_repairs,
    stack_padding_repairs,
    stack_repairs,
    storage_scope_repairs,
    store_address_repairs,
    structure_return_repairs,
    switch_tables,
    target_local_repairs,
    value_range_repairs,
    zero_test_repairs,
)
from .repair_generation import call as _generated

AGENT_FAMILIES = live_return_address_repairs.FAMILIES + copy_lifetime_repairs.FAMILIES + (debug_boundary_repairs.FAMILY,) + conversion_scheduling_repairs.FAMILIES + (hardware_operation_repairs.FAMILY,) + (return_contract_repairs.FAMILY, float_storage_repairs.FAMILY, narrow_expression_repairs.FAMILY) + narrow_call_repairs.FAMILIES + (branch_scalar_repairs.FAMILY, call_count_repairs.FAMILY, pointer_snapshot_repairs.FAMILY,) + call_assignment_repairs.FAMILIES + (shared_offset_repairs.FAMILY, 'branch_store_tail',) + later_local_repairs.FAMILIES + call_scope_repairs.FAMILIES + debug_initializer_repairs.FAMILIES + phase_union_repairs.FAMILIES + captured_index_repairs.FAMILIES + recent_capture_repairs.FAMILIES + (identity_lifetime_repairs.FAMILY, 'typed_global_identity', 'block_accumulator_lifetime', pointer_index_scope.FAMILY,) + copy_shape_repairs.FAMILIES + focused_lifetime_repairs.FAMILIES + equal_value_flow.FAMILIES + example_lifetime_repairs.FAMILIES + affine_operand_repairs.FAMILIES + loop_weight_repairs.FAMILIES + branch_pointer_repairs.FAMILIES + capture_followup_repairs.FAMILIES + ('paired_load_order',) + allocation_repairs.FAMILIES + value_range_repairs.FAMILIES + count_lifetime_repairs.FAMILIES + helper_expansion.FAMILIES + record_lifetime_repairs.FAMILIES + raw_address_repairs.FAMILIES + zero_test_repairs.FAMILIES + pointer_pair_repairs.FAMILIES + field_order_repairs.FAMILIES + match_shape_repairs.FAMILIES + increment_expression_repairs.FAMILIES + allocator_shape_repairs.FAMILIES + buffer_address_repairs.FAMILIES + normalization_repairs.FAMILIES + call_lifetime_repairs.FAMILIES + sdk_io_repairs.FAMILIES + target_local_repairs.FAMILIES + formatter_repairs.FAMILIES + ('target_switch_order',) + inline_scheduling_repairs.FAMILIES + ('runtime_scalar_signature', 'shared_return_tail', 'direct_compound_store', 'redundant_loop_guard', 'float_argument_bits', 'arithmetic_operand_order', 'call_forwarded_arguments', 'small_copy', 'typed_record_address', 'one_shot_scope', 'one_shot_unwrap',
                  'pointer_block_scope', 'array_address_share', 'combined_integer_lifetimes', 'inferred_record_layout',
                  'float_product_intermediate','pointer_chase_reuse','independent_assignment_order',
                  'terminal_early_return','member_store_replay','scalar_global_address','split_mask_stores',
                  'split_integer_phase','paired_integer_lifetimes','initialization_cluster')

LIMIT = 20
_IDENT = r'[A-Za-z_]\w*'
_MEMBER = rf'{_IDENT}(?:\s*(?:->|\.)\s*{_IDENT})*'


def variants(source: str, name: str, arch: str, asm: str = '', flags=(), labels=None, *, limit=LIMIT, header='', tables=(), callees='') -> list[str]:
    span = find(source, name)
    if span is None:
        return []
    start, end = span
    clean = _blank(source)
    opening = clean.find('{', start, end)
    if opening < 0:
        return []
    # The dedicated path preserves directives and never rewrites their bodies.
    early=_generated(live_return_address_repairs.variants,source,name,arch,asm,header) + _generated(copy_lifetime_repairs.variants,source,name) + _generated(debug_boundary_repairs.variants,source,name,flags,header) + _generated(conversion_scheduling_repairs.variants,source,name,header,arch,asm,flags) + _generated(hardware_operation_repairs.variants,source,name,arch,asm) + _generated(shared_offset_repairs.variants,source,name,header) + _generated(branch_layout_repairs.store_tails,source,name,header) + _generated(captured_index_repairs.variants,source,name,header) + _generated(decision_address_repairs.variants, source,name,arch,header) + _generated(recent_capture_repairs.variants,source,name,arch,asm,header,callees)
    if '#' in clean[opening:end]:
        def generate(masked):
            inner_labels = {}
            safe_header = macro_repairs.prepare(header)[0] or ''
            choices = variants(masked,name,arch,asm,flags,inner_labels,limit=None,header=safe_header,tables=tables,callees=callees)
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
    early = (_generated(return_contract_repairs.variants,source,name,header,arch,asm)
             + _generated(float_storage_repairs.variants,source,name,header,arch,asm)
             + _generated(narrow_expression_repairs.variants,source,name,header,arch)
             + later_local_repairs.variants(source,name,header,arch) + early)
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
    for family,text in (_generated(call_scope_repairs.variants,source,name,header)
                        + _generated(debug_initializer_repairs.variants,source,name,flags,header)
                        + _generated(phase_union_repairs.variants,source,name,arch,flags,header)):
        proposals.append(text);labels[text]=family
    allocation_candidates = _generated(branch_pointer_repairs.variants, source,name,header) + _generated(allocation_repairs.variants, source, name, header)
    for family, text in allocation_candidates:
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(pointer_pair_repairs.variants, source, name, header) + _generated(field_order_repairs.variants, source, name, header) + _generated(zero_test_repairs.variants, source, name, arch, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (_generated(branch_scalar_repairs.variants,source,name,header,arch)
                         + _generated(call_count_repairs.variants,source,name,header,arch)
                         + _generated(call_assignment_repairs.variants,source,name,header,arch)
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
    for family, text in _generated(increment_expression_repairs.variants, source, name) + _generated(loop_weight_repairs.variants, source, name, header) + _generated(equal_value_flow.variants, source, name, header):
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
                         + _generated(masked_reuse_repairs.variants, source, name) + _generated(pointer_index_scope.variants, source, name, header) + _generated(focused_lifetime_repairs.variants, source, name) + _generated(register_reuse_repairs.variants, source, name) + _generated(affine_operand_repairs.variants, source, name)):
        proposals.append(text)
        labels[text] = family
    for text in _generated(mmio_repairs.variants, source, name, arch):
        proposals.append(text)
        labels[text] = 'mmio_address'
    for family, text in _generated(observed_repairs.variants, source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(near_match_repairs.variants, source, name, asm):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(captured_repairs.variants, source, name):
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
        for family, text in _generated(inline_scheduling_repairs.variants, source, name):
            proposals.append(text)
            labels[text] = family
    for family, text in _generated(allocator_shape_repairs.variants, source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in _generated(normalization_repairs.variants, source, name, header):
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
        if re.fullmatch(rf'(?:0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*|{_IDENT})', tail):
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
    from .repair_generation import ACTIVE
    from .repair_preflight import invalid_call
    for text in dict.fromkeys(proposals):
        if text == source:
            continue
        if ACTIVE.get() is None and invalid_call(text, name):
            continue
        groups.setdefault(labels[text],[]).append(text)
    # Missing argument positions and unknown-object types need refinement before speculative
    # call scopes; adding a scope family must not displace this existing short preflight lane.
    if 'm2c_refinement' in groups:
        groups = {'m2c_refinement':groups['m2c_refinement'],**groups}
    ordered = []
    while any(groups.values()):
        for items in groups.values():
            if items:
                ordered.append(items.pop(0))
    return ordered if limit is None else ordered[:limit]
