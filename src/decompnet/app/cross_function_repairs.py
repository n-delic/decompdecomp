"""Select bounded library repairs using evidence owned by the project."""
import re

from decomprepair import inferred_layout_repairs, matched_prototype_repairs, target_call_arity_repairs
from decomprepair.integer_repair_support import split_args, view

from .. import structs
from ..arch import mips_returns
from ..candidate_scope import callees
from . import model_replay_evidence as trace

CALL = 'cross_function_call'
RECORD = 'cross_function_record'
FAMILIES = (CALL, RECORD)


def argument_reads(asm):
    """Find arguments consumed before replacement in the callee's straight-line entry."""
    pending = set(target_call_arity_repairs.ARGREG)
    reads = set()
    for line in mips_returns.instructions(asm)[:32]:
        if trace._op(line) in trace._MIPS_BRANCHES | trace._MIPS_CALLS:
            break
        effects = {reg: trace._access(line, '$'+reg) for reg in pending}
        if None in effects.values():
            break
        reads.update(reg for reg, (_, use) in effects.items() if use)
        pending.difference_update(reg for reg, (write, use) in effects.items() if write or use)
        if not pending:
            break
    return reads


def call_variants(net, proj, fn, source, header, asm):
    """Combine target argument preparation with the selected callee's measured and C contracts."""
    preparation = {}
    for name, writes in target_call_arity_repairs.calls(asm):
        preparation.setdefault(name, []).append(writes)
    if not preparation:
        return []
    store = net.project_store(proj['id'])
    result = []
    roots = [source, *(child for _, child in target_call_arity_repairs.variants(source, fn['name'], asm))]
    for selected in callees(net, proj, fn, source, max_source_chars=16000):
        name = selected['name']
        sites = preparation.get(name)
        if not sites:
            continue
        rows = store.all('SELECT * FROM functions WHERE project_id=? AND name=? LIMIT 2', proj['id'], name)
        if len(rows) != 1 or rows[0]['id'] == fn['id']:
            continue
        callee_asm = net._asm_text(rows[0], proj)
        reads = argument_reads(callee_asm)
        if not reads or not all(reads <= writes.keys() for writes in sites):
            continue
        definition = selected['matched_source']
        result_use = mips_returns.result(callee_asm, asm, name) or {}
        original = prototype(source, name)
        if original is None:
            continue
        for root in roots:
            before = prototype(root, name)
            if before is None or (root != source and before['params'] == original['params']):
                continue
            for _, candidate in matched_prototype_repairs.variants(root, fn['name'], header, definition):
                if prototype(candidate, name)['result'] != before['result'] and not result_use.get('caller_consumes'):
                    continue
                if (CALL, candidate) not in result:
                    result.append((CALL, candidate))
                if len(result) == 3:
                    return result
    return result


def prototype(source, name):
    return next((m for m in matched_prototype_repairs.PROTO.finditer(source)
                 if m['callee'] == name), None)


def record_variants(net, proj, fn, source):
    """Prioritize typed drafts whose offsets and widths agree with independent accesses."""
    candidates = inferred_layout_repairs.variants(source, fn['name'], proj['arch'])
    if not candidates:
        return []
    part = view(source, fn['name'])
    signature = part.clean[:part.opening]
    parameters = re.search(r'\b'+re.escape(fn['name'])+r'\s*\(([^()]*)\)\s*$', signature)
    if not parameters or (found := net.structs(proj)) is None:
        return []
    accesses = inferred_layout_repairs.accesses(part.body)
    result = []
    for index, (start, end) in enumerate(split_args(parameters[1])):
        arg = re.search(r'\b([A-Za-z_]\w*)\s*$', parameters[1][start:end])
        if not arg:
            continue
        key = found.type_of((structs.ARGUMENT, fn['vram'], index))
        if key is None:
            continue
        layout = found.layout(key)
        fields = {f.offset: f for f in layout.fields}
        hits = {(typ, offset) for _, _, base, typ, offset in accesses if base == arg[1]}
        if len(hits) < 2:
            continue
        for typ, offset in hits:
            field = fields.get(offset)
            if (field is None or field.others or not field.functions-{fn['vram']}
                    or inferred_layout_repairs.CANONICAL.get(field.ctype(), field.ctype()) != typ):
                break
        else:
            prefix = '__repair_record_'+arg[1]
            result.extend((RECORD, candidate) for _, candidate in candidates
                          if re.search(r'\}\s+'+re.escape(prefix)+r'_*;', candidate))
    return result


def variants(net, proj, fn, source, header):
    if proj['arch'] != 'mips' or not getattr(net, 'is_rom', lambda _: False)(proj):
        return []
    if len(source)+len(header) > 64000 or not fn['asm']:
        return []
    return (call_variants(net, proj, fn, source, header, net._asm_text(fn, proj))
            + record_variants(net, proj, fn, source))
