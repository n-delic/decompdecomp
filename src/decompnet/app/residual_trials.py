"""Route focused C proposals using the linked residual of the current draft."""
import re

from decomprepair import (
    allocation_repairs,
    call_lifetime_repairs,
    residual_source_repairs,
)

from ..arch.mips_returns import instructions
from . import allocation_evidence, model_replay_evidence, region_repairs
from .cross_function_repairs import argument_reads

FAMILIES = (residual_source_repairs.ADDRESS, residual_source_repairs.BRANCH, residual_source_repairs.CALL,
            residual_source_repairs.ENTRY, residual_source_repairs.FRAME, 'residual_call_buffer')


def prologue_rotation(rows):
    """The same saved-register stores may be listed in a different order."""
    stores = [(left, right) for mark,left,right in rows if mark == 'i']
    if not 2 <= len(stores) <= 4:
        return False
    if (sorted(a for a,_ in stores) != sorted(b for _,b in stores)
            or not all(re.fullmatch(r'sw \$s[0-7], \S+\(\$sp\)', a) for a,_ in stores)):
        return False
    body = allocation_evidence.diagnose([r for r in rows if r[0] != 'i'], 'mips')
    return body['register_only'] and any(3 <= len(cycle) <= 4 and
        all(re.fullmatch(r'\$s[0-7]', reg) for reg in cycle) for cycle in body['register_cycles'])


def forwarded_pointer(net, proj, fn, source, header):
    """The first target call consumes the untouched incoming second argument."""
    decoded = instructions(net._asm_text(fn,proj))
    for text in decoded[:32]:
        if call := re.fullmatch(r'jal\s+([A-Za-z_]\w*)',text):
            name = call[1]
            break
        if model_replay_evidence._op(text) in model_replay_evidence._MIPS_BRANCHES:
            return []
        effect = model_replay_evidence._access(text,'$a1')
        if effect is None or effect[0]:
            # Floating callee-saved stores and ordinary prologue stores are
            # recognized by the common register-flow reader.
            return []
    else:
        return []
    index = decoded.index(text)
    if (index+1 >= len(decoded)
            or (effect := model_replay_evidence._access(decoded[index+1],'$a1')) is None or effect[0]):
        return []
    if not any(re.fullmatch(r'c\.\w+\.s\s+\$f(?:0|v0)\s*,.+',row)
               for row in decoded[index+2:index+6]):
        return []
    rows = net.project_store(proj['id']).all(
        'SELECT * FROM functions WHERE project_id=? AND name=? LIMIT 2',proj['id'],name)
    if len(rows) != 1 or not {'a0','a1'} <= argument_reads(net._asm_text(rows[0],proj)):
        return []
    return residual_source_repairs.forward_second_pointer(source,fn['name'],header,name)


def proposals(source, name, header, rows, *, net=None, proj=None, fn=None):
    rows = region_repairs.clean(rows)
    changed = [(i,a,b) for i,(mark,a,b) in enumerate(rows) if mark != ' ']
    if not changed:
        return []
    if len(changed) <= 3 and any(re.fullmatch(r'b(?:eq|ne)\b', a.partition(' ')[0])
            and b == a.replace(' ', 'l ', 1) and i+1 < len(rows) and rows[i+1][0] == ' '
            for i,a,b in changed):
        from decomprepair.kmc_operand_repairs import evidence

        if evidence(rows)[0]:
            return residual_source_repairs.address_order(source,name,header)
        return residual_source_repairs.zero_exit(source,name,header)
    if prologue_rotation(rows):
        forwarded = forwarded_pointer(net,proj,fn,source,header) if net else []
        return (forwarded + residual_source_repairs.entry_lifetimes(source,name,header)
                + [(residual_source_repairs.ENTRY, text) for text in
                   allocation_repairs.initialization_orders(source,name,header)[:2]])
    shape = allocation_evidence.diagnose(rows, 'mips')
    if shape['register_only'] and len(changed) <= 10 and any(
            re.fullmatch(r'jal\s+\S+', a) for _,a,_ in rows):
        return (residual_source_repairs.duplicate_call_setup(source,name,header)
                + residual_source_repairs.call_lifetimes(source,name,header))
    target, current = shape['target_frame'], shape['candidate_frame']
    if target is not None and current is not None and 8 <= current-target <= 32 and any(
            re.fullmatch(r'jal\s+\S+', a) for _,a,_ in rows):
        return residual_source_repairs.frame_address(source,name,header)
    return []


def followups(source, name, header, before, after):
    """Use a closer frame as a parent even when the overall score regressed."""
    a = allocation_evidence.diagnose(before, 'mips')
    b = allocation_evidence.diagnose(after, 'mips')
    target, old, new = a['target_frame'],a['candidate_frame'],b['candidate_frame']
    if None in (target,old,new) or target != b['target_frame'] or not target <= new < old:
        return []
    proposals = residual_source_repairs.buffer_preparation(source,name,header)
    proposals += [(family, candidate) for family,candidate in
                  call_lifetime_repairs.staged(source,name,header)][:2]
    return proposals[:2]
