"""Bounded CPU follow-ups from a measured private candidate, cached with that candidate's inputs."""
from . import cpu_source_repairs, cpu_switch_repairs

MAX_PAIRS=6


def proposals(net, proj, fn, source, header, measurement):
    from .. import arch as arches
    from .measured_source_repairs import narrowing_casts, statement_lines
    arch=arches.get(proj['arch'])
    rows=measurement.get('rows',[])
    from .source_symbol_recovery import variants as symbol_trials
    aligned = symbol_trials(source,header,measurement)
    if measurement.get('helper_aliases'):return aligned
    from .source_context_diagnostics import identity
    from .source_lead_analysis import compare
    # Spend broad permutation work on better-supported leads first. A mismatch is
    # not a rejected mapping; changed context or a new mapping remains retryable.
    if identity(compare(rows,proj['arch'])):return aligned
    from .retail_context_repairs import commented_conditions, draft_prefix, pointer_lifetimes
    proof=arch.source_pointer_lifetime(fn['asm'] or '') if arch.source_pointer_lifetime else []
    result=draft_prefix(source,fn['name'],header,dict(fn).get('best_source'),rows,proj['arch'])
    from .typed_draft_repairs import variants as typed_fields
    result=aligned+[p for _,s,h in result for p in typed_fields(s,fn['name'],h)]+result
    result+=pointer_lifetimes(source,fn['name'],header,proof,rows)
    import re
    result+=commented_conditions(source,header,re.findall(r'\bD_[0-9A-Fa-f]{8}\b',fn['asm'] or ''))
    from . import hazard_line_repairs
    result += [(family,s,header) for family,s in hazard_line_repairs.variants(source,fn['name'],header,proj['arch'],rows)]
    result+=statement_lines(source,fn['name'],header,rows)
    if arch.source_extra_narrowing:result+=narrowing_casts(source,header,arch.source_extra_narrowing(rows))
    result+=cpu_source_repairs.variants(source,header,proj['arch'],measurement.get('rows',[]),measurement.get('lines',{}))
    from .retail_source_repairs import data_bindings, terminal_branch
    if hasattr(net,"symbols"):
        result += data_bindings(source,header,measurement.get("rows",[]),net.symbols(proj["id"]).resolve)
        from .retail_context_repairs import array_indices
        result += array_indices(source,header,rows,net.symbols(proj['id']).resolve)
    from .. import arch as arches
    reader=arches.get(proj["arch"]).source_tail_branch
    if reader:
        result += terminal_branch(source,fn["name"],header,reader(fn["asm"] or ""),measurement.get("rows",[]))
    if hasattr(net,'reader') and hasattr(net,'symbols'):
        from .scalar_pool_recovery import variants as scalar_pools
        result += scalar_pools(source,header,fn['asm'] or '',net.reader(proj,fn),net.symbols(proj['id']).resolve,
                               'big' if proj['big_endian'] else 'little')[:2]
    from .switch_tables import read
    tables=read(net,proj,fn) if hasattr(net,'reader') else []
    result+=cpu_switch_repairs.variants(source,fn['name'],header,tables,cpu_switch_repairs.read_blocks(net,proj,fn,tables))
    # Share the existing bounded stack/declaration/helper families, not a second random search.
    from .source_context_diagnostics import route
    if route(fn['asm'] or '',rows,proj['arch']):
        # Keep targeted repairs; ordinary shapes cannot model private callee clobbers.
        return list(dict.fromkeys(result))[:MAX_PAIRS]
    rows=measurement.get('rows',[])
    marks={r[0] for r in rows if r[0]!=' '}
    if marks and marks<={'r','s'}:
        from .draft_repairs import variants
        labels={}
        candidates=variants(source,fn['name'],proj['arch'],(fn['asm'] or ''),
                            labels=labels,header=header,limit=40)
        wanted={'local_declaration_order','spill_declaration_order','stack_padding','small_helper_expansion'}
        result += [('cpu-'+labels[s],s,header) for s in candidates if labels.get(s) in wanted][:2]
    return list(dict.fromkeys(result))[:MAX_PAIRS]


def extend(net, proj, fn, variants, origins, source, header, measurement, lead):
    # A small total queue and depth cap keep combined layout/constant hypotheses bounded.
    depth=lead.get('cpu_depth',0)
    if depth>=2:return
    count=sum(bool(meta.get('cpu_depth')) for meta in origins.values())
    for family,s,h in proposals(net,proj,fn,source,header,measurement):
        pair=(s,h)
        if pair in variants:continue
        if count>=MAX_PAIRS:return
        if family in ('cpu-aligned-symbols','cpu-helper-symbol-context') and (source,header) in variants:
            variants.insert(variants.index((source,header))+1,pair)
        else:variants.append(pair)
        origins[pair]={**lead,'family':family,'cpu_depth':depth+1};count+=1


def trial_key(state, source, header, cfg):
    import hashlib
    return 'local-source-v14:'+hashlib.sha256(repr((state,source,header,cfg)).encode()).hexdigest()


def pending(net, fn, state, cfg, variants, origins, exclude):
    """Let pending structural CPU trials run before spending the pass on broader flag profiles."""
    return any(pair!=exclude and origins.get(pair,{}).get('family','').startswith('cpu-')
               and not net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',
                                     fn['id'],trial_key(state,*pair,cfg)) for pair in variants)
