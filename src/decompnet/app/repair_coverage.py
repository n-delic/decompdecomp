"""Verify that a small installed repair can solve the captured failed attempt."""

import hashlib
import time
from collections import defaultdict, deque
from itertools import chain, islice
from pathlib import Path

from .. import scoring, toolchains
from ..compiler import compile_source
from ..project import compiler_config
from ..repair_changes import TOKEN, without_comments
from ..repair_comparison import complete_snapshot, differences
from ..repair_feedback import _settings
from . import draft_repairs, repair_generation, repair_ranking
from .engine.judging import search_evaluation
from .function_names import canonical_source

TRIALS = 6
SECONDS = 6
TYPE_FAMILIES = ('symbol_access_width', 'field_width', 'narrow_widening', 'local_widening',
                 'parameter_width', 'float_prototype')


def proposals(source, name, arch, asm, flags, header, profile, rows, category, winner):
    labels = {}
    candidates = draft_repairs.variants(source, name, arch, asm, flags, labels, header=header, limit=None,
                                        entry_scope=draft_repairs.promoted_entry_repairs.profile_scope(arch, profile))
    pools = defaultdict(deque)
    exact_source = TOKEN.findall(without_comments(winner))
    for candidate in candidates:
        if candidate != source and labels.get(candidate):
            # The winning source only orders installed-rule proposals. It cannot
            # supply a candidate or bypass the linker.
            if TOKEN.findall(without_comments(candidate)) == exact_source:
                yield labels[candidate], candidate
                continue
            pools[labels[candidate]].append(candidate)
    order = repair_ranking.order(list(pools), rows)
    if category == 'types':
        order = sorted(order, key=lambda family: family not in TYPE_FAMILIES)
    # Give each likely family one trial before spending another trial on the same rule.
    while order:
        for family in order:
            yield family, pools[family].popleft()
        order = [family for family in order if pools[family]]


def verify(feedback, example):
    """Return a family only when the existing read-only ROM judge passes its candidate."""
    evidence = example['evidence']
    before, after = evidence.get('before') or {}, evidence['after']
    settings = after.get('settings') or {}
    if (not complete_snapshot(settings) or differences(before.get('settings'), settings)
            or before.get('attempt_id') is None or not 0 < (before.get('score') or 0)
            or before.get('source_sha256') == after.get('source_sha256')):
        return None
    source, header = before.get('source'), before.get('header')
    if (not source or header is None or header != after.get('header') or len(source) > 64 * 1024
            or not 0 < (evidence.get('instructions') or 0) <= 256
            or hashlib.sha256(source.encode()).hexdigest() != before.get('source_sha256')
            or hashlib.sha256(header.encode()).hexdigest() != settings['context_sha256']):
        return None
    if not after.get('source') or hashlib.sha256(after['source'].encode()).hexdigest() != after['source_sha256']:
        return None
    net = feedback.net
    fn = feedback.store.one('SELECT * FROM functions WHERE id=?', example['function_id'])
    proj = feedback.store.one('SELECT * FROM projects WHERE id=?', example['project_id'])
    if (not fn or not proj or fn['project_id'] != proj['id'] or fn['name'] != evidence['function']
            or fn['vram'] != evidence['vram'] or not fn['target']
            or hashlib.sha256(fn['target']).hexdigest() != evidence['target_sha256']):
        return None
    live = _settings(proj, fn)
    if any(live.get(key) != settings.get(key) for key in ('mode', 'platform', 'rom', 'big_endian', 'gp')):
        return None
    profile = settings['compiler']
    if (set(profile) - {'toolchain', 'flags', 'timeout'} or not toolchains.installed(profile['toolchain'])):
        return None
    invocation = compiler_config(Path(proj['root']), {'compiler': profile})
    if list(invocation.cmd) != settings['effective_command']:
        return None
    deadline = time.monotonic() + SECONDS
    timeout = invocation.timeout

    def evaluate(candidate):
        invocation.timeout = min(timeout, max(0.01, deadline - time.monotonic()))
        built = compile_source(invocation, header, canonical_source(fn, candidate))
        if not built.ok or getattr(built, 'language', None) != settings['language']:
            return None, None
        with search_evaluation():
            obj, matched, _ = net._evaluate(fn, proj, built.obj)
        return obj, matched

    baseline, matched = evaluate(source)
    if baseline is None or matched is not False or time.monotonic() >= deadline:
        return None
    rows = repair_ranking.instruction_rows(scoring.diff(net._target_obj(fn, proj), baseline).rows)
    compiler = profile['toolchain']
    if proj['arch'] == 'mips' and compiler == 'gcc-2.7.2-kmc':
        from decomprepair import (
            aligned_stack_read,
            kmc_cast_repairs,
            kmc_operand_repairs,
            kmc_type_repairs,
            likely_branch_cursor,
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
            packed_matrix_conversion,
            passthrough_argument_repairs,
            relocated_byte_table,
            relocated_global_update,
            uncached_read_repairs,
        )

        from . import stack_padding_repairs

        symbols = net.symbols(proj['id']) if hasattr(net,'symbols') else None
        focused = stack_padding_repairs.layout_candidates(source, fn['name'], header, proj['arch'],
                                                         rows, symbols, profile['flags'])
        if candidate := uncached_read_repairs.rewrite(source, fn['name'], fn['asm'] or '',
                                                      symbols.resolve if symbols else None):
            focused.append(('measured_uncached_word_read', candidate))
        focused += measured_control_flow.variants(source, fn['name'], rows)
        focused += measured_memory_order.variants(source, fn['name'], rows)
        focused += measured_expression_repairs.variants(source, fn['name'], header, rows)
        focused += measured_temporary_repairs.variants(source, fn['name'], rows)
        if candidate := measured_callback_loop.rewrite(source, fn['name'], fn['asm'] or '', header,
                                                       symbols.resolve if symbols else None):
            focused.append(('measured_callback_loop', candidate))
        if candidate := measured_mmio_polling.rewrite(source, fn['name'], fn['asm'] or '', header):
            focused.append(('measured_mmio_polling', candidate))
        if candidate := likely_branch_cursor.rewrite(source, fn['name'], fn['asm'] or '', header):
            focused.append(('likely_branch_cursor', candidate))
        if candidate := relocated_byte_table.rewrite(source, fn['name'], fn['asm'] or '', header,
                                                     symbols.resolve if symbols else None):
            focused.append(('relocated_byte_table', candidate))
        if candidate := relocated_global_update.rewrite(source, fn['name'], fn['asm'] or '', header,
                                                        symbols.resolve if symbols else None):
            focused.append(('relocated_global_update', candidate))
        if candidate := packed_matrix_conversion.rewrite(source, fn['name'], fn['asm'] or '', header):
            focused.append(('packed_matrix_conversion', candidate))
        if candidate := aligned_stack_read.rewrite(source, fn['name'], fn['asm'] or ''):
            focused.append(('aligned_stack_read', candidate))
        if candidate := passthrough_argument_repairs.missing_entry_argument(source, fn['name'], fn['asm'] or '', header):
            focused.append(('entry_wrapper_argument', candidate))
        if candidate := loop_storage_shapes.measured_copy_setup(source, fn['name'], fn['asm'] or '', header):
            focused.append(('measured_copy_loop_setup', candidate))
        focused += measured_callback_repairs.variants(source, fn['name'], rows)
        focused += measured_address_repairs.variants(source, fn['name'], header, rows, symbols)
        focused += measured_pointer_scale.variants(source, fn['name'], header, rows)
        focused += measured_immediate_repairs.variants(source, fn['name'], rows)
        focused += kmc_cast_repairs.variants(source, fn['name'], header, rows, profile['flags'])
        focused += kmc_type_repairs.variants(source, fn['name'], header, rows)
        focused += kmc_operand_repairs.variants(source, fn['name'], header, rows)
    else:
        focused = []

    def generated():
        scope = ('coverage', evidence['target_sha256'], settings['context_sha256'], tuple(profile['flags']))
        families = draft_repairs.AGENT_FAMILIES + TYPE_FAMILIES
        with repair_generation.session(net, scope, min(deadline, time.monotonic() + 2), families):
            batch = list(islice(proposals(source, fn['name'], proj['arch'], evidence.get('target_asm') or '',
                                          profile['flags'], header, profile, rows, example['category'], after['source']),
                                TRIALS))
        yield from batch

    candidates = chain(focused, generated())
    for family, candidate in islice(candidates, TRIALS):
        if time.monotonic() >= deadline:
            break
        _, matched = evaluate(candidate)
        if matched is True:
            # Function splits or renames during this background check invalidate its target.
            current = feedback.store.one('SELECT target,relocs,meta,vram,name FROM functions WHERE id=?', fn['id'])
            project = feedback.store.one('SELECT config,root FROM projects WHERE id=?', proj['id'])
            if (current and all(current[key] == fn[key] for key in current.keys())
                    and project and project['config'] == proj['config'] and project['root'] == proj['root']):
                return family
            break
    return None
