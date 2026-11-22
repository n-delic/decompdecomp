"""Route a measured MIPS mismatch to a few source hypotheses."""

import re

from decomprepair import (
    aligned_allocator_request,
    aligned_stack_read,
    allocation_repairs,
    balanced_clamp_argument_stage,
    balanced_clamp_predicates,
    balanced_float_predicate_return,
    balanced_predicate_projection,
    balanced_projection_scale,
    balanced_signed_selector_increment,
    boolean_guard_operands,
    branch_tail_distribution,
    byte_cursor_postincrements,
    clamp_return_local,
    clamp_saved_argument_lifetime,
    clamped_field_reload,
    conditional_field_subtract,
    conditional_smoothing_gain,
    counted_switch_advance,
    direct_mapped_address,
    epsilon_predicate_order,
    field_copy_before_clear,
    field_snapshot_copies,
    float_clamp_scale_stage,
    float_delta_load_order,
    float_intermediate_repairs,
    float_matrix_identity,
    global_pointer_local_repairs,
    guard_inversion_repairs,
    guard_load_order,
    guard_reset_scheduling,
    halfword_before_float_store,
    indexed_field_cursor,
    kmc_cast_repairs,
    likely_branch_cursor,
    local_inline_repairs,
    loop_counter_roles_repairs,
    loop_sentinel_repairs,
    loop_storage_shapes,
    masked_slot_dispatch,
    measured_address_repairs,
    measured_after_call_reload,
    measured_be16_cursor,
    measured_branch_halfword_store,
    measured_byte_clamp,
    measured_byte_clear_base,
    measured_byte_global_branch,
    measured_callback_loop,
    measured_callback_repairs,
    measured_carried_loop_test,
    measured_command_record_cursor,
    measured_control_flow,
    measured_cursor_loop_tail,
    measured_cursor_publish,
    measured_cursor_schedule,
    measured_event_frame_capture,
    measured_expression_repairs,
    measured_flag_reload,
    measured_float_argument_pair,
    measured_float_grid_stores,
    measured_forwarded_counter,
    measured_forwarded_pointer,
    measured_global_offset_reuse,
    measured_global_rebase,
    measured_guarded_flag_value,
    measured_guarded_index,
    measured_halfword_index,
    measured_halfword_stores,
    measured_halfword_switch,
    measured_ido_byte_cursor,
    measured_ido_callback_offset,
    measured_ido_index_update,
    measured_immediate_repairs,
    measured_indexed_clear,
    measured_indexed_record_layout,
    measured_indirect_command_records,
    measured_indirect_self_call,
    measured_interpolation_calls,
    measured_kmc_addresses,
    measured_kmc_boolean_loop_flag,
    measured_kmc_branch_pointer,
    measured_kmc_byte_and_message,
    measured_kmc_byte_cursor_return,
    measured_kmc_byte_global_pointer,
    measured_kmc_byte_index_local,
    measured_kmc_byte_operations,
    measured_kmc_byte_or,
    measured_kmc_byte_stride,
    measured_kmc_call_counter,
    measured_kmc_call_setup,
    measured_kmc_call_stores,
    measured_kmc_carriers,
    measured_kmc_command,
    measured_kmc_cursor_switch,
    measured_kmc_event_field_order,
    measured_kmc_event_record,
    measured_kmc_event_timestamp,
    measured_kmc_field_pointer,
    measured_kmc_flag_update,
    measured_kmc_flow,
    measured_kmc_global_stores,
    measured_kmc_halfword_count_loop,
    measured_kmc_halfword_flag_table,
    measured_kmc_indexed_byte_table,
    measured_kmc_indirect_byte_table,
    measured_kmc_inferred_table_widths,
    measured_kmc_input,
    measured_kmc_invariant_row,
    measured_kmc_lifetimes,
    measured_kmc_list_append,
    measured_kmc_list_count,
    measured_kmc_masked_rank_counter,
    measured_kmc_message_thread,
    measured_kmc_null_table_loop,
    measured_kmc_packed_bits,
    measured_kmc_pointer_table_guard,
    measured_kmc_record_reads,
    measured_kmc_relocated_fields,
    measured_kmc_relocated_pool,
    measured_kmc_repeated_reads,
    measured_kmc_scalar_shapes,
    measured_kmc_second_call_argument,
    measured_kmc_split_table_index,
    measured_kmc_stack_and_call,
    measured_kmc_stack_pointer,
    measured_kmc_two_byte_reader,
    measured_kmc_unsigned_indexed_byte,
    measured_kmc_varbyte_accumulator,
    measured_kmc_varbyte_initialization,
    measured_kmc_variable_byte_cursor,
    measured_kmc_vector_square,
    measured_kmc_wrappers,
    measured_lifetime_shapes,
    measured_list_head_insert,
    measured_list_selection,
    measured_loop_search_target,
    measured_masked_halfword_read,
    measured_matrix_products,
    measured_memory_order,
    measured_mmio_polling,
    measured_narrow_use,
    measured_nibble_tables,
    measured_null_iteration_store,
    measured_packing_stage,
    measured_pointer_displacement,
    measured_pointer_scale,
    measured_record_byte_offset,
    measured_reset_loops,
    measured_ring_global_capture,
    measured_saved_global,
    measured_scalar_updates,
    measured_signed_offset_helper,
    measured_square_loads,
    measured_stack_records,
    measured_storage_shapes,
    measured_temporary_repairs,
    measured_thread_addresses,
    measured_varbyte_lookup,
    measured_zero_record_pairs,
    ordered_halfword_updates,
    oscillator_phase_hold,
    packed_matrix_conversion,
    passthrough_argument_repairs,
    projection_argument_before_store,
    projection_gain,
    quadratic_sign_holder,
    rejected_branch_default,
    relocated_byte_table,
    relocated_global_update,
    relocated_pointer_reloads,
    sampled_phase_axis_stage,
    saved_range_probe,
    scaled_blend_input,
    search_exit_loop_repairs,
    shared_projection_lifetime,
    shared_return_repairs,
    short_leaf_drafts,
    smoothing_call_before_read,
    split_dispatch_zero_case,
    stack_byte_init_repairs,
    stack_fill_drafts,
    stack_header_buffer,
    stack_read_retry,
    switch_pointer_calls,
    symbol_width_repairs,
    target_string_repairs,
    targeted_switch_labels,
    trig_axis_reuse,
    uncached_read_repairs,
    wide_update_before_flags,
)
from decomprepair import (
    observed_arithmetic_repairs as arithmetic,
)
from decomprepair import (
    observed_flow_repairs as flow,
)
from decomprepair import (
    observed_storage_repairs as storage,
)

from ..compiler import MAX_SOURCE_BYTES
from . import (
    allocation_evidence,
    allocation_search,
    measured_mips_repairs,
    operand_and_edge_search,
    permuter_focus,
    stack_layout,
    stack_padding_repairs,
    target_conversion_diff_repairs,
    target_edge_diff_repairs,
    target_guard_width_diff_repairs,
    target_inline_storage_diff_repairs,
    target_memory_diff_repairs,
    target_memory_schedule_diff_repairs,
    target_schedule_diff_repairs,
)

REGISTER = r'\$[a-z0-9]+'
IMMEDIATE = r'(?:0x[0-9a-f]+|[1-9][0-9]*|0)'
ADDRESS = rf'(?:-?{IMMEDIATE})?\(({REGISTER})\)'
BRANCH = re.compile(r'b(?:eq|ne|gez|gtz|lez|ltz)(?:z|l|zl)?\s')
DOUBLE_COMPARE = re.compile(r'c\.(?:le|lt)\.d\s')
FLOAT_MEMORY = re.compile(rf'(?P<op>[ls][wd]c1)\s+{REGISTER},\s*(?P<addr>{ADDRESS})')
INDIRECT_LOAD = re.compile(rf'lw\s+{REGISTER},\s*(?:0x0|0)?\((?!\$sp\b){REGISTER}\)')


def _instructions(rows, side):
    return [(row[0] != ' ', row[side]) for row in rows if row[side]]


def _released_reload(code):
    for (store_diff, store), (load_diff, load) in zip(code, code[1:]):
        stored, loaded = FLOAT_MEMORY.fullmatch(store), FLOAT_MEMORY.fullmatch(load)
        if (stored and loaded and (store_diff or load_diff) and stored['op'].startswith('s')
                and 'l'+stored['op'][1:] == loaded['op'] and stored['addr'] == loaded['addr']
                and '($sp)' not in loaded['addr']):
            yield 4 if stored['op'] == 'swc1' else 8


def _entry_read(code):
    for index, (changed, text) in enumerate(code):
        if not INDIRECT_LOAD.fullmatch(text):
            continue
        for next_changed, address in code[index+1:index+4]:
            if (changed or next_changed) and address.startswith('lui '):
                return True
            if BRANCH.match(address) or re.match(r'j\w*\s', address):
                break
    return False


def _delay_values(code):
    for (branch_diff, branch), (init_diff, init) in zip(code, code[1:]):
        if (branch_diff or init_diff) and BRANCH.match(branch):
            if re.fullmatch(rf'move\s+(?!\$zero\b){REGISTER},\s*\$zero', init):
                yield 0
            elif hit := re.fullmatch(rf'(?:addiu|ori)\s+(?!\$zero\b){REGISTER},\s*\$zero,\s*({IMMEDIATE})', init):
                yield int(hit[1],0)


def _has_double_conversion(code):
    for i, (_, compare) in enumerate(code):
        if not DOUBLE_COMPARE.match(compare):
            continue
        window = code[max(0,i-6):i+24]
        if (any(changed for changed,_ in window)
                and any(re.match(r'trunc\.w\.d\s', op) for _,op in window)
                and any(re.fullmatch(rf'lui\s+{REGISTER},\s*(?:0x8000|32768)', op) for _,op in window)
                and any(re.match(r'or\s', op) for _,op in window)):
            return True
    return False


def _division_pairs(code):
    """Read small literal dividers or simple multiply-high reciprocal dividers."""
    constants = {}
    divisors = []
    for index, (changed, line) in enumerate(code):
        opcode, _, text = line.partition(' ')
        args = [field.strip() for field in text.split(',')]
        rd = args[0]
        value = None
        if BRANCH.match(line) or opcode in {'b','bc1f','bc1t','j','jr','jal','jalr'}:
            constants.clear()
        if opcode in {'li','lui'} and len(args) == 2 and re.fullmatch(IMMEDIATE,args[1]):
            value = int(args[1],0) << (16 if opcode == 'lui' else 0)
        elif opcode in {'ori','addiu'} and len(args) == 3 and re.fullmatch(IMMEDIATE,args[2]):
            base = constants.get(args[1], (0,False) if args[1]=='$zero' else (None,False))
            if base[0] is not None:
                imm = int(args[2],0)
                if opcode == 'addiu':
                    imm -= 0x10000 if imm >= 0x8000 else 0
                value = (base[0] | imm) if opcode == 'ori' else (base[0]+imm) & 0xffffffff
                changed |= base[1]
        elif opcode == 'divu' and len(args) in (2,3):
            constant = constants.get(args[-1])
            # Some renderers show the literal immediate for a divider.
            if constant is None and re.fullmatch(IMMEDIATE,args[-1]):
                constant = (int(args[-1],0),False)
            if constant and 1 < constant[0] <= 65535:
                divisors.append((constant[0], changed or constant[1]))
        elif opcode == 'multu' and len(args) == 2:
            operands = [constants[r] for r in args if r in constants]
            following = code[index+1:index+3]
            if len(operands) == 1 and len(following) == 2:
                magic, magic_changed = operands[0]
                mfhi = re.fullmatch(rf'mfhi\s+({REGISTER})',following[0][1])
                shift = re.fullmatch(rf'srl\s+{REGISTER},\s*({REGISTER}),\s*({IMMEDIATE})',following[1][1])
                if mfhi and shift and mfhi[1] == shift[1] and magic and 0 < int(shift[2],0) < 32:
                    numerator = 1 << (32+int(shift[2],0))
                    divisor = (numerator+magic-1)//magic
                    if 1 < divisor <= 65535 and (numerator+divisor-1)//divisor == magic:
                        divisors.append((divisor,changed or magic_changed or any(flag for flag,_ in following)))
        if value is not None:
            constants[rd] = (value,changed)
        elif opcode not in {'divu','multu','mult','sw','sh','sb','swc1','sdc1'}:
            constants.pop(rd,None)
    return list(dict.fromkeys((first,second) for i,(first,a) in enumerate(divisors[:16])
                              for second,b in divisors[i+1:i+17] if a or b))


def proposals(source, name, header, arch, toolchain, rows, asm, jump_tables=(), *, flags=(), resolve=None, big_endian=False, read_data=None, symbols=None):
    if arch != 'mips':
        return []
    result = targeted_switch_labels.variants(source,name,jump_tables) if jump_tables else []
    if not toolchain.startswith(('gcc-2.7.2-kmc','gcc-egcs','ido-5.3','ido-7.1')) or not rows:
        return result
    rows = [row for row in permuter_focus.normalized(rows)
            if isinstance(row,(tuple,list)) and len(row)==3 and all(isinstance(field,str) for field in row)]
    if not any(marker != ' ' for marker,_,_ in rows):
        return result
    result += measured_lifetime_shapes.variants(source, name, header, toolchain, rows)
    result += measured_scalar_updates.variants(source, name, header, toolchain, rows, asm)
    if toolchain == 'gcc-2.7.2-kmc':
        result += boolean_guard_operands.variants(source, name, rows, header)
        result += balanced_projection_scale.variants(source, name, rows, header=header)
        result += balanced_predicate_projection.variants(source, name, rows, header=header)
        result += epsilon_predicate_order.staged_variants(source, name, rows, header=header)
        result += conditional_field_subtract.variants(source, name, rows, header=header)
        result += sampled_phase_axis_stage.variants(source, name, rows, header=header)
        result += shared_projection_lifetime.variants(source, name, rows, header=header)
        result += allocation_repairs.measured_slot_orders(source, name, rows, header)
        result += allocation_repairs.measured_array_orders(source, name, rows, header)
        result += guard_load_order.variants(source, name, rows, header)
        result += branch_tail_distribution.measured_state_store_arms(source, name, rows, header)
        result += global_pointer_local_repairs.measured_table_compare_pointer(source, name, rows, header)
        result += local_inline_repairs.measured_record_copy_inline(source, name, rows, header)
        result += clamp_saved_argument_lifetime.variants(source, name, rows, header=header)
        result += balanced_clamp_argument_stage.variants(source, name, rows, header=header)
        result += balanced_clamp_predicates.variants(source, name, rows, header=header, max_bytes=MAX_SOURCE_BYTES)
        result += balanced_clamp_predicates.split_register_variants(source, name, rows, header=header)
        result += trig_axis_reuse.variants(source, name, rows, header=header)
        result += projection_gain.variants(source, name, rows, header=header)
        result += quadratic_sign_holder.variants(source, name, rows, header=header)
        result += quadratic_sign_holder.arm_variants(source, name, rows, header=header)
        result += smoothing_call_before_read.variants(source, name, rows, header=header)
        result += conditional_smoothing_gain.variants(source, name, rows, header=header)
        result += scaled_blend_input.variants(source, name, rows, header=header)
        result += projection_argument_before_store.variants(source, name, rows, header=header)
        result += balanced_signed_selector_increment.variants(source, name, rows, header=header)
        result += balanced_float_predicate_return.variants(source, name, rows, header=header)
        result += clamped_field_reload.variants(source, name, rows, header=header)
        result += saved_range_probe.variants(source, name, rows, header=header)
        result += float_clamp_scale_stage.variants(source, name, rows, header=header)
        result += wide_update_before_flags.variants(source, name, rows, header=header)
        result += field_copy_before_clear.variants(source, name, rows, header=header)
        result += clamp_return_local.variants(source, name, rows, header=header)
        result += float_delta_load_order.variants(source, name, rows, header=header)
        result += counted_switch_advance.variants(source, name, rows, header=header)
        result += oscillator_phase_hold.variants(source, name, rows, header=header)
        result += oscillator_phase_hold.product_variants(source, name, rows, header=header)
        result += halfword_before_float_store.variants(source, name, rows, header=header)
        result += rejected_branch_default.variants(source, name, rows, header=header)
        result += measured_kmc_stack_pointer.variants(source, name, rows)
        result += measured_cursor_schedule.variants(source, name, header, rows, asm or '')
        result += measured_thread_addresses.variants(source, name, header, rows, resolve)
        result += measured_kmc_addresses.variants(source, name, header, rows, flags)
        result += measured_kmc_byte_stride.variants(source, name, header, rows)
        result += measured_kmc_branch_pointer.variants(source, name, header, rows)
        result += measured_kmc_byte_and_message.variants(source, name, header, rows)
        result += measured_kmc_byte_global_pointer.variants(source, name, header, rows)
        result += measured_kmc_byte_index_local.variants(source, name, header, rows)
        result += measured_kmc_byte_operations.variants(source, name, header, rows, resolve=resolve)
        result += measured_kmc_call_counter.variants(source, name, header, rows)
        result += measured_kmc_call_setup.variants(source, name, header, rows)
        result += measured_kmc_call_stores.variants(source, name, header, rows, resolve)
        result += measured_carried_loop_test.variants(source, name, header, rows)
        result += measured_byte_clamp.variants(source, name, header, rows)
        result += measured_varbyte_lookup.variants(source, name, header, rows, asm or '')
        result += measured_indexed_clear.variants(source, name, header, rows)
        result += measured_global_offset_reuse.variants(source, name, header, rows, asm or '')
        result += measured_list_selection.variants(source, name, header, rows)
        result += measured_signed_offset_helper.variants(source, name, header, rows)
        result += measured_indirect_command_records.variants(source, name, header, rows)
        result += measured_event_frame_capture.variants(source, name, header, rows)
        result += measured_command_record_cursor.variants(source, name, header, rows)
        result += measured_ring_global_capture.variants(source, name, header, rows)
        result += measured_nibble_tables.variants(source, name, header, rows)
        result += measured_indexed_record_layout.variants(source, name, header, rows)
        result += measured_record_byte_offset.variants(source, name, header, rows)
        result += measured_list_head_insert.variants(source, name, header, rows)
        result += measured_be16_cursor.variants(source, name, header, rows)
        result += measured_forwarded_counter.variants(source, name, header, rows, asm or '')
        result += measured_flag_reload.variants(source, name, header, rows)
        result += measured_guarded_flag_value.variants(source, name, header, rows)
        result += measured_byte_global_branch.variants(source, name, header, rows)
        result += measured_byte_clear_base.variants(source, name, header, rows)
        result += measured_float_argument_pair.variants(source, name, header, rows)
        result += measured_guarded_index.variants(source, name, header, rows)
        result += measured_reset_loops.variants(source, name, header, rows)
        result += measured_interpolation_calls.variants(source, name, header, rows)
        result += measured_matrix_products.variants(source, name, header, rows)
        result += measured_after_call_reload.variants(source, name, header, asm or '', resolve)
        result += measured_indirect_self_call.variants(source, name, header, rows, asm or '', resolve)
        result += measured_kmc_command.variants(source, name, header, rows)
        result += measured_kmc_carriers.variants(source, name, header, rows)
        result += measured_kmc_cursor_switch.variants(source, name, header, rows)
        result += measured_kmc_field_pointer.variants(source, name, header, rows)
        result += measured_masked_halfword_read.variants(source, name, header, rows)
        result += measured_branch_halfword_store.variants(source, name, header, rows)
        result += measured_kmc_event_field_order.variants(source, name, header, rows)
        result += measured_kmc_event_timestamp.variants(source, name, header, rows)
        result += measured_kmc_halfword_flag_table.variants(source, name, header, rows)
        result += measured_kmc_halfword_count_loop.variants(source, name, header, rows)
        result += measured_kmc_indexed_byte_table.variants(source, name, header, rows)
        result += measured_kmc_inferred_table_widths.variants(source, name, header, rows)
        result += measured_kmc_indirect_byte_table.variants(source, name, header, rows)
        result += measured_kmc_invariant_row.variants(source, name, header, rows)
        result += measured_kmc_list_count.variants(source, name, header, rows)
        result += measured_square_loads.variants(source, name, header, rows)
        result += measured_kmc_list_append.variants(source, name, header, rows)
        result += measured_kmc_null_table_loop.variants(source, name, header, rows)
        result += measured_kmc_byte_cursor_return.variants(source, name, header, rows)
        result += measured_kmc_varbyte_accumulator.variants(source, name, header, rows)
        result += measured_kmc_varbyte_initialization.variants(source, name, header, rows)
        result += measured_kmc_boolean_loop_flag.variants(source, name, header, rows)
        result += measured_kmc_masked_rank_counter.variants(source, name, header, rows)
        result += measured_kmc_unsigned_indexed_byte.variants(source, name, header, rows, asm)
        result += measured_kmc_event_record.variants(source, name, header, rows)
        result += measured_kmc_flag_update.variants(source, name, header, rows)
        result += measured_kmc_global_stores.variants(source, name, header, rows, resolve)
        result += measured_kmc_flow.variants(source, name, header, rows)
        result += measured_kmc_input.variants(source, name, header, rows, resolve)
        result += measured_halfword_switch.variants(source, name, header, rows)
        result += measured_halfword_index.variants(source, name, header, rows, asm)
        result += measured_halfword_stores.variants(source, name, rows, resolve)
        result += measured_kmc_repeated_reads.variants(source, name, header, rows, resolve)
        result += measured_kmc_record_reads.variants(source, name, header, rows, resolve)
        result += measured_kmc_relocated_pool.variants(source, name, header, rows, resolve)
        result += measured_kmc_relocated_fields.variants(source, name, header, rows, resolve)
        result += measured_kmc_split_table_index.variants(source, name, header, rows)
        result += measured_kmc_stack_and_call.variants(source, name, header, rows, resolve)
        result += measured_kmc_scalar_shapes.variants(source, name, header, rows, resolve=resolve, flags=flags)
        result += measured_kmc_second_call_argument.variants(source, name, header, rows)
        result += measured_kmc_lifetimes.variants(source, name, header, rows)
        result += measured_kmc_message_thread.variants(source, name, header, rows)
        result += measured_kmc_packed_bits.variants(source, name, header, rows)
        result += measured_kmc_pointer_table_guard.variants(source, name, header, rows)
        result += measured_kmc_two_byte_reader.variants(source, name, header, rows)
        result += measured_kmc_variable_byte_cursor.variants(source, name, header, rows)
        result += measured_kmc_byte_or.variants(source, name, header, rows)
        result += measured_kmc_vector_square.variants(source, name, header, rows)
        result += measured_kmc_wrappers.variants(source, name, header, rows)
        result += measured_stack_records.variants(source, name, header, rows)
    if toolchain.startswith('ido-7.1'):
        result += measured_ido_byte_cursor.variants(source, name, header, rows)
        result += measured_ido_index_update.variants(source, name, header, rows)
        result += measured_ido_callback_offset.variants(source, name, header, rows)
        # IDO 7.1 keeps the word of a local nothing reads: the frame's growth says how many, its declaration where
        # (measured on a bit-stream reader; ido-5.3 is left out until a match there shows the same)
        result += stack_padding_repairs.frame_word_candidates(source, name, arch, rows)
        return result
    target, candidate = (_instructions(rows, side) for side in (1,2))
    if toolchain.startswith('gcc-egcs'):
        if allocation_search.floating_only(rows, arch):
            result += arithmetic.squared_terms(source,name,header)
        return result

    result += target_memory_schedule_diff_repairs.proposals(source,name,header,toolchain,rows,target,candidate,asm,
                                                             big_endian=big_endian,flags=flags)
    if toolchain.startswith('gcc-2.7.2-kmc'):
        result += measured_storage_shapes.variants(source, name, header, rows, asm, big_endian=big_endian)
        result += masked_slot_dispatch.variants(source, name, asm or '', header,
                                                read_data=read_data, big_endian=big_endian)
        result += switch_pointer_calls.variants(source, name, asm or '', header,
                                                read_data=read_data, big_endian=big_endian)
        result += short_leaf_drafts.variants(source, name, asm, header)
        result += target_inline_storage_diff_repairs.proposals(source,name,header,rows,target,candidate,asm,
                                                             read_data=read_data,big_endian=big_endian)
        result += stack_padding_repairs.layout_candidates(source,name,header,arch,rows,symbols,flags,asm=asm)
        if candidate_source := uncached_read_repairs.rewrite(source, name, asm or '', resolve):
            result.append(('measured_uncached_word_read', candidate_source))
        result += measured_control_flow.variants(source,name,rows)
        result += measured_cursor_loop_tail.variants(source, name, rows)
        result += measured_cursor_publish.variants(source, name, rows)
        result += shared_return_repairs.result_copy_variants(source, name, rows)
        result += guard_reset_scheduling.variants(source, name, header, rows)
        result += global_pointer_local_repairs.scoped_field_reads(source, name, rows)
        result += measured_global_rebase.variants(source, name, header, rows, resolve=resolve)
        result += measured_zero_record_pairs.variants(source, name, header, rows, resolve=resolve)
        result += measured_packing_stage.variants(source, name, header, rows)
        result += measured_loop_search_target.variants(source, name, header, rows)
        result += measured_saved_global.variants(source, name, header, rows, resolve=resolve)
        result += measured_memory_order.variants(source,name,rows,resolve=resolve,header=header)
        result += measured_memory_order.staged_field_masks(source, name, rows, header)
        result += search_exit_loop_repairs.sentinel_byte_guard_variants(source, name, rows)
        result += loop_counter_roles_repairs.guarded_reuse_variants(source, name, rows)
        result += measured_float_grid_stores.variants(source, name, rows)
        result += measured_forwarded_pointer.variants(source, name, header, asm or '')
        result += float_intermediate_repairs.repeated_products(source, name, rows)
        result += measured_expression_repairs.variants(source,name,header,rows)
        result += stack_byte_init_repairs.variants(source, name, header, rows)
        result += measured_temporary_repairs.variants(source,name,rows)
        if candidate_source := measured_callback_loop.rewrite(source, name, asm or '', header, resolve):
            result.append(('measured_callback_loop', candidate_source))
        if candidate_source := measured_mmio_polling.rewrite(source, name, asm or '', header):
            result.append(('measured_mmio_polling', candidate_source))
        if candidate_source := likely_branch_cursor.rewrite(source, name, asm or '', header):
            result.append(('likely_branch_cursor', candidate_source))
        if candidate_source := relocated_byte_table.rewrite(source, name, asm or '', header, resolve):
            result.append(('relocated_byte_table', candidate_source))
        if candidate_source := relocated_global_update.rewrite(source, name, asm or '', header, resolve):
            result.append(('relocated_global_update', candidate_source))
        if any(marker != ' ' and op.startswith(('lbu ', 'sb ')) for marker, op, _ in rows):
            if candidate_source := relocated_global_update.rewrite_branch_byte(source, name, asm or '', header, resolve):
                result.append(('relocated_branch_byte', candidate_source))
        if candidate_source := relocated_global_update.rewrite_countdown(source, name, asm or '', header, resolve):
            result.append(('relocated_countdown', candidate_source))
        if candidate_source := relocated_global_update.rewrite_increment(source, name, asm or '', header, resolve):
            result.append(('relocated_global_increment', candidate_source))
        if candidate_source := relocated_global_update.rewrite_hoisted_word_read(source, name, rows, asm or '', header, resolve):
            result.append(('relocated_hoisted_word_read', candidate_source))
        result += relocated_pointer_reloads.variants(source, name, header, rows, asm or '', resolve, flags)
        if candidate_source := stack_fill_drafts.rewrite(source, name, asm or '', header):
            result.append(('descending_stack_fill', candidate_source))
        if candidate_source := stack_fill_drafts.rewrite_ascending(source, name, asm or '', header):
            result.append(('ascending_stack_fill', candidate_source))
        if candidate_source := stack_header_buffer.rewrite(source, name, asm or '', header):
            result.append(('stack_header_buffer', candidate_source))
        if candidate_source := relocated_global_update.rewrite_reload_wrapper(source, name, asm or '', header, resolve):
            result.append(('direct_global_reload', candidate_source))
        if candidate_source := stack_read_retry.rewrite(source, name, asm or '', header):
            result.append(('stack_read_retry', candidate_source))
        if candidate_source := stack_read_retry.rewrite_inline(source, name, asm or '', header):
            result.append(('stack_read_inline_compare', candidate_source))
        if candidate_source := float_matrix_identity.rewrite(source, name, asm or '', header):
            result.append(('float_matrix_identity', candidate_source))
        if candidate_source := packed_matrix_conversion.rewrite(source, name, asm or '', header):
            result.append(('packed_matrix_conversion', candidate_source))
        if candidate_source := byte_cursor_postincrements.rewrite(source, name, asm or '', header):
            result.append(('byte_cursor_postincrements', candidate_source))
        if candidate_source := aligned_allocator_request.rewrite(source, name, asm or '', header):
            result.append(('aligned_allocator_request', candidate_source))
        if candidate_source := indexed_field_cursor.rewrite(source, name, asm or '', header):
            result.append(('indexed_field_cursor', candidate_source))
        if candidate_source := split_dispatch_zero_case.rewrite(source, name, asm or '', header):
            result.append(('split_dispatch_zero_case', candidate_source))
        if candidate_source := field_snapshot_copies.rewrite(source, name, asm or '', header):
            result.append(('field_snapshot_copies', candidate_source))
        if candidate_source := ordered_halfword_updates.rewrite(source, name, asm or '', header, resolve):
            result.append(('ordered_halfword_updates', candidate_source))
        if candidate_source := aligned_stack_read.rewrite(source, name, asm or ''):
            result.append(('aligned_stack_read', candidate_source))
        if candidate_source := guard_inversion_repairs.rewrite_byte_toggle(source, name, asm or '', header):
            result.append(('byte_flag_branch_orientation', candidate_source))
        if candidate_source := kmc_cast_repairs.rewrite_float_table(source, name, asm or '', header):
            result.append(('direct_float_table_load', candidate_source))
        if candidate_source := direct_mapped_address.rewrite(source, name, asm or '', header):
            result.append(('direct_mapped_address', candidate_source))
        if candidate_source := global_pointer_local_repairs.rewrite_list_transfer(source, name, asm or '', header):
            result.append(('list_node_transfer', candidate_source))
        if candidate_source := global_pointer_local_repairs.rewrite_linked_insert(source, name, asm or '', header):
            result.append(('linked_insert', candidate_source))
        if candidate_source := shared_return_repairs.rewrite_global_guard(source, name, asm or ''):
            result.append(('guarded_global_return', candidate_source))
        if candidate_source := passthrough_argument_repairs.missing_entry_argument(source, name, asm or '', header):
            result.append(('entry_wrapper_argument', candidate_source))
        result += passthrough_argument_repairs.shifted_call_variants(
            source, name, header, asm or '', rows, resolve=resolve, read_data=read_data, big_endian=big_endian)
        if candidate_source := loop_storage_shapes.measured_copy_setup(source, name, asm or '', header):
            result.append(('measured_copy_loop_setup', candidate_source))
        result += measured_callback_repairs.variants(source,name,rows)
        if candidate_source := measured_address_repairs.rewrite_byte_offsets(source, name, asm or '', header):
            result.append(('byte_scaled_global_offset', candidate_source))
        result += measured_address_repairs.variants(source,name,header,rows,symbols)
        result += measured_pointer_displacement.variants(source,name,header,rows)
        result += measured_pointer_scale.variants(source,name,header,rows)
        result += target_string_repairs.verified_variants(source, name, header, rows, read_data)
        result += measured_immediate_repairs.variants(source,name,rows)
        result += measured_narrow_use.variants(source, name, rows)
        result += symbol_width_repairs.signed_halfword_load_variants(source, name, rows)
        if big_endian:
            result += symbol_width_repairs.signed_byte_base_variants(source, name, rows, header)
        result += measured_narrow_use.boolean_byte_carriers(source, name, rows)
        result += measured_narrow_use.byte_backedge_variants(source, name, rows)
        result += measured_narrow_use.shared_halfword_index_variants(source, name, rows)
        result += kmc_cast_repairs.variants(source,name,header,rows,flags)
        result += measured_null_iteration_store.variants(source, name, rows)
        result += target_guard_width_diff_repairs.proposals(source,name,header,rows,target,candidate,asm,
                                                          resolve=resolve)
        result += target_edge_diff_repairs.proposals(source,name,header,rows,target,candidate,asm,
                                                   resolve=resolve,big_endian=big_endian,flags=flags)
        result += target_schedule_diff_repairs.proposals(source,name,header,rows,target,candidate,flags)
    result += target_conversion_diff_repairs.proposals(source,name,header,toolchain,rows,target,candidate,asm,
                                               read_data=read_data,big_endian=big_endian)
    result += target_memory_diff_repairs.proposals(source,name,header,rows,asm,target,candidate,toolchain,
                                             big_endian=big_endian)
    if toolchain.startswith('ido-5.3'):
        return result
    result += operand_and_edge_search.proposals(source,name,header,asm,target,candidate,flags,resolve)
    frame = stack_layout.diagnose(arch,rows)
    result += measured_mips_repairs.proposals(source,name,header,rows,asm,target,candidate,frame)
    widths = set(_released_reload(target)) - set(_released_reload(candidate))
    if len(widths) == 1:
        result += storage.staged_reload(source,name,header,widths.pop())
    if frame and frame['frame_delta'] == 0 and not any(frame['saved_deltas']):
        result += storage.redistribute(source,name,header,frame['local_deltas'])
    if _entry_read(target) and not _entry_read(candidate):
        result += flow.first_read(source,name,header)
    if asm and any(changed and BRANCH.match(text) for changed,text in target):
        values = loop_sentinel_repairs.target_sentinels(asm)
        if values:
            result += flow.sentinel_loop(source,name,header,values)
    values = set(_delay_values(target)) - set(_delay_values(candidate))
    if values:
        result += flow.guarded_initializer(source,name,header,values)
    if _has_double_conversion(target) and not _has_double_conversion(candidate):
        result += arithmetic.double_unsigned(source,name,header)
    if any(changed and re.match(r'(?:divu|multu|mfhi|srl)\s',text) for changed,text in target):
        pairs = _division_pairs(target)
        if pairs:
            result += arithmetic.separated_divisions(source,name,header,pairs)
    diagnosis = allocation_evidence.diagnose(rows,arch)
    if diagnosis['register_only'] and any(changed and re.match(r'(?:subu?|sltu?)\s', text) for changed,text in target):
        result += arithmetic.in_place_minmax(source,name,header)
    return result
