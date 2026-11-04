"""Bounded register-cycle and local-chain lanes sharing the normal judge, full-input cache and search budget."""
import hashlib
import json

from .. import candidate_scope, toolchains
from ..project import compiler_for
from . import allocation_evidence, compiler_diagnostics, gcc_allocation, rtl_value_trace

VERSION = 'allocation-cycle-v1'
LIMIT = 24
CHAIN_LIMIT = 8


def allowance(diagnosis):
    """Local acyclic chains get a short lane; cycles retain their existing allowance."""
    if (not diagnosis["register_only"] or diagnosis["target_instructions"]!=diagnosis["candidate_instructions"]
            or diagnosis["target_frame"]!=diagnosis["candidate_frame"]):return 0
    return LIMIT if diagnosis["register_cycles"] else CHAIN_LIMIT if diagnosis["register_chains"] else 0


def key(net, proj, fn, source):
    profile = compiler_for(net.cfg(proj),fn['vram'])
    installed = toolchains.installed(profile.get('toolchain',''))
    return VERSION+':'+hashlib.sha256(repr((candidate_scope.key(net,proj,fn),source,installed)).encode()).hexdigest()


def saved(net, proj, fn, source):
    if not net.cfg(proj).get('compiler'):
        return None
    row = net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',
                        fn['id'],key(net,proj,fn,source))
    try:
        value = json.loads(row['feedback']) if row else None
        return value if isinstance(value,dict) else None
    except (ValueError,TypeError):
        return None


def capture(net, proj, fn, source, *, on_compile, rows=()):
    cached = saved(net,proj,fn,source)
    if cached is not None:
        return cached
    scope_key = key(net,proj,fn,source)
    header = net._context(proj['id'],fn['unit'])['header']
    profile = compiler_for(net.cfg(proj),fn['vram'])
    # Capture is isolated, supports only installed native GCC, and must reproduce the ordinary
    # object's allocated output. The caller charges both builds to its existing search allowance.
    result = compiler_diagnostics.diagnose(profile,header,source,fn['name'],on_compile=on_compile)
    evidence = ({'allocation':result.get('allocation',{}),'value_trace':result.get('value_trace',{})}
                if result.get('output_unchanged') and not result.get('skipped') else
                {'skipped':result.get('skipped') or 'Diagnostic output changed; evidence ignored.'})
    current_proj = net.store.one('SELECT * FROM projects WHERE id=?',proj['id'])
    current_fn = net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    if current_proj is None or current_fn is None or key(net,current_proj,current_fn,source) != scope_key:
        return {'skipped':'Compiler inputs changed during capture; evidence ignored.'}
    evidence['rows'] = list(rows)
    # Transient build errors are retryable; a supported unchanged capture or an unsupported adapter
    # can be remembered. A changed compiler installation changes the input key.
    if result.get('output_unchanged') or (result.get('skipped') and 'error' not in result):
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'],scope_key,'diagnostic',VERSION,json.dumps(evidence)))
    return evidence


def proposals(source, name, arch, rows, choices, labels, evidence=None, header=''):
    """Use conservative existing generators; no new padding identities or assembly renaming."""
    diagnosis = allocation_evidence.diagnose(rows,arch)
    budget=allowance(diagnosis)
    if not budget:
        return []
    from . import identity_lifetime_repairs, later_local_repairs, value_trace_repairs
    extra = later_local_repairs.variants(source,name,header,arch,evidence)
    extra += identity_lifetime_repairs.variants(source,name,header,arch) if arch in {'mips','ppc','x86','arm','thumb'} else []
    trace = (evidence or {}).get('value_trace',{})
    extra += value_trace_repairs.variants(source,name,trace,rows,header)
    for family,candidate in extra:
        labels[candidate] = family
    allowed = {*later_local_repairs.FAMILIES,'split_integer_phase','paired_integer_lifetimes','split_narrow_lifetime','narrow_definition_lifetime',
               'merge_integer_lifetimes','split_integer_lifetime','value_lifetime','loop_weight_scope',
               'allocation_initialization_order','local_declaration_order','declaration_order',
               'value_trace_bundle','existing_identity_lifetime','pointer_local_reuse','pointer_load_inline',
               'pointer_index_scope','pointer_argument_alias','paired_pointer_declarations','copy_register_reuse',
               'spill_declaration_order','pointer_index_order','dead_pointer_clear'}
    selected = [(labels.get(c,''),c) for c in choices if labels.get(c) in allowed]
    selected = extra+selected
    lines = rtl_value_trace.focus(trace,source,rows,header)
    if lines:
        selected.sort(key=lambda pair:not value_trace_repairs.touches(source,value_trace_repairs.changes(source,pair[1]),lines,1))
    selected=list(dict.fromkeys(selected))
    if not diagnosis['register_cycles']:
        # A scalar donor pool must not crowd the short pointer-chain lane out.
        from itertools import zip_longest
        preferred=('pointer_local_reuse','pointer_load_inline','pointer_argument_alias',
                   'paired_pointer_declarations','spill_declaration_order','pointer_index_scope',
                   'pointer_index_order','copy_register_reuse','dead_pointer_clear')
        selected.sort(key=lambda p:preferred.index(p[0]) if p[0] in preferred else len(preferred))
        pools={}
        for item in selected:pools.setdefault(item[0],[]).append(item)
        selected=[p for row in zip_longest(*pools.values()) for p in row if p]
    return selected[:budget]


def brief(net, proj, fn, source, rows):
    if (not getattr(net,'is_rom',lambda _:False)(proj) or net.fork_slug(proj['slug'])
            or not allocation_evidence.diagnose(rows,proj['arch'])['register_cycles']):
        return ''
    # Reading an agent brief never starts compiler tools.
    evidence = saved(net,proj,fn,source) or {}
    return gcc_allocation.brief(evidence.get('allocation'),allocation_evidence.diagnose(rows,proj['arch'])['register_cycles'])
