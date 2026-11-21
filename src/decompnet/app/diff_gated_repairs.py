"""Route a measured MIPS mismatch to a few source hypotheses."""

import re

from decomprepair import (
    aligned_allocator_request,
    aligned_stack_read,
    byte_cursor_postincrements,
    field_snapshot_copies,
    float_matrix_identity,
    global_pointer_local_repairs,
    indexed_field_cursor,
    kmc_cast_repairs,
    likely_branch_cursor,
    loop_sentinel_repairs,
    loop_storage_shapes,
    measured_address_repairs,
    measured_callback_loop,
    measured_callback_repairs,
    measured_control_flow,
    measured_expression_repairs,
    measured_immediate_repairs,
    measured_memory_order,
    measured_mmio_polling,
    measured_pointer_scale,
    measured_temporary_repairs,
    ordered_halfword_updates,
    packed_matrix_conversion,
    passthrough_argument_repairs,
    relocated_byte_table,
    relocated_global_update,
    shared_return_repairs,
    split_dispatch_zero_case,
    stack_read_retry,
    targeted_switch_labels,
    uncached_read_repairs,
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
    if not toolchain.startswith(('gcc-2.7.2-kmc','gcc-egcs','ido-5.3')) or not rows:
        return result
    rows = [row for row in permuter_focus.normalized(rows)
            if isinstance(row,(tuple,list)) and len(row)==3 and all(isinstance(field,str) for field in row)]
    if not any(marker != ' ' for marker,_,_ in rows):
        return result
    target, candidate = (_instructions(rows, side) for side in (1,2))
    if toolchain.startswith('gcc-egcs'):
        if allocation_search.floating_only(rows, arch):
            result += arithmetic.squared_terms(source,name,header)
        return result

    result += target_memory_schedule_diff_repairs.proposals(source,name,header,toolchain,rows,target,candidate,asm,
                                                             big_endian=big_endian,flags=flags)
    if toolchain.startswith('gcc-2.7.2-kmc'):
        result += target_inline_storage_diff_repairs.proposals(source,name,header,rows,target,candidate,asm,
                                                             read_data=read_data,big_endian=big_endian)
        result += stack_padding_repairs.layout_candidates(source,name,header,arch,rows,symbols,flags)
        if candidate_source := uncached_read_repairs.rewrite(source, name, asm or '', resolve):
            result.append(('measured_uncached_word_read', candidate_source))
        result += measured_control_flow.variants(source,name,rows)
        result += measured_memory_order.variants(source,name,rows)
        result += measured_expression_repairs.variants(source,name,header,rows)
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
        if candidate_source := relocated_global_update.rewrite_countdown(source, name, asm or '', header, resolve):
            result.append(('relocated_countdown', candidate_source))
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
        if candidate_source := global_pointer_local_repairs.rewrite_linked_insert(source, name, asm or '', header):
            result.append(('linked_insert', candidate_source))
        if candidate_source := shared_return_repairs.rewrite_global_guard(source, name, asm or ''):
            result.append(('guarded_global_return', candidate_source))
        if candidate_source := passthrough_argument_repairs.missing_entry_argument(source, name, asm or '', header):
            result.append(('entry_wrapper_argument', candidate_source))
        if candidate_source := loop_storage_shapes.measured_copy_setup(source, name, asm or '', header):
            result.append(('measured_copy_loop_setup', candidate_source))
        result += measured_callback_repairs.variants(source,name,rows)
        if candidate_source := measured_address_repairs.rewrite_byte_offsets(source, name, asm or '', header):
            result.append(('byte_scaled_global_offset', candidate_source))
        result += measured_address_repairs.variants(source,name,header,rows,symbols)
        result += measured_pointer_scale.variants(source,name,header,rows)
        result += measured_immediate_repairs.variants(source,name,rows)
        result += kmc_cast_repairs.variants(source,name,header,rows,flags)
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
