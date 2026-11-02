"""Applicable local families share one source snapshot, batch budget and ordinary judge."""
import hashlib
import json
import re
import time

from ..network import NetworkError
from ..project import compiler_for
from . import (
    adaptive_repairs,
    address_association_repairs,
    agent_evidence,
    agent_targets,
    agent_tokens,
    branch_layout_repairs,
    call_initialization_repairs,
    callee_prototype_repairs,
    captured_repairs,
    draft_repairs,
    evidence_repairs,
    expression_recipes,
    expression_repairs,
    focused_lifetime_repairs,
    integer_lifetime_repairs,
    lifetime_repairs,
    load_pair_repairs,
    local_widening_repairs,
    local_width_repairs,
    masked_reuse_repairs,
    mmio_repairs,
    pattern_repairs,
    pointer_cast_repairs,
    register_repairs,
    register_reuse_repairs,
    repair_ranking,
    repair_sequences,
    stack_layout,
    stack_padding_repairs,
    store_address_repairs,
    switch_tables,
)

EXPERIMENTAL = ('experimental_bitfield_frame',)
FAMILIES = focused_lifetime_repairs.FAMILIES + ('learned_expression', 'address_association', 'call_initialization', *EXPERIMENTAL, 'masked_value_reuse', 'cfg_integer_lifetimes', 'table_slot_lifetime', 'unsigned_assignment_reuse', 'guard_forward_jump', 'constant_register_reuse', 'copy_register_reuse', 'last_shift_reuse', 'signedness','declaration_order','typed_temporary','branch_orientation','narrowing','source_lines',
            'address_temporary','constant_local','comparison_load','stack_array','loop_induction',
            'parameter_width','value_lifetime','sum_shape','call_result_inline','comparison_reuse','integer_address_order','mmio_address','call_arity','struct_byte_offset','switch_call','paired_load_order','array_index_width','split_scalar_lifetime','pointer_before_store','store_address_lifetime','stack_padding','local_width','pointer_cast_inline',
            'local_widening','callee_parameter_width','callee_return_width','split_integer_lifetime','merge_integer_lifetimes','branch_call','goto_leaf','switch_layout') + draft_repairs.AGENT_FAMILIES


def run(net, agent, slug, function, revision, families=None, max_trials=6, *, seconds=60, provenance_kind='agent batch'):
    if isinstance(max_trials,bool) or not isinstance(max_trials,int) or not 1<=max_trials<=6:
        raise NetworkError('Use max_trials from 1 to 6.')
    if families is not None and (not isinstance(families,list) or not families or len(families)>len(FAMILIES)
                                 or any(f not in FAMILIES for f in families)):
        raise NetworkError('Choose supported local families: '+', '.join(FAMILIES))
    requested = list(dict.fromkeys(families or [f for f in FAMILIES if f not in EXPERIMENTAL]))
    deadline=time.monotonic()+seconds
    fn = net._function(function,slug)
    with net.attempt_turn(agent,fn):
        source,actual = agent_tokens.candidate(net,agent,slug,function)
        if actual!=revision:
            raise agent_tokens.stale(net,agent,fn,revision,source,actual)
        checked = agent_evidence.preflight(net,agent,slug,fn,source)
        if checked and checked.get('error'):
            return agent_evidence.blocked_message(checked, actual)
        if checked and checked.get('exact'):
            from .tools import _compile
            return _compile(net,agent,slug,function,source)
        if time.monotonic()>=deadline:
            return 'Local repair time budget reached during baseline checking; no variants submitted.'
        pools = available(net,agent,slug,fn,source,revision,requested)
        effect_scope=repair_ranking.effect_scope(net,fn,source)
        selected=[];seen=set()
        while len(selected)<max_trials and any(pools.values()):
            for family,pool in pools.items():
                if pool and len(selected)<max_trials:
                    candidate=pool.pop(0)
                    if candidate not in seen:
                        selected.append((family,candidate));seen.add(candidate)
        if not selected:
            return f'No untried applicable local repairs. Revision {actual}; no compile run.'
        scope=agent_tokens.target_scope(net,fn,revision)
        baseline=checked or getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
        if baseline and baseline.get('source')!=source:
            baseline=None
        if checked and checked.get('score') is not None:
            if not hasattr(net, '_agent_outcomes'):
                net._agent_outcomes={}
            net._agent_outcomes[agent.id,fn['id']]=checked
        recipe_profile=repair_sequences.profile(net,net._project(slug),fn)
        policy_profile=adaptive_repairs.profile(net,net._project(slug),fn)
        from .permuter_focus import classify
        focus=classify((baseline or {}).get('rows') or [])
        remaining=deadline-time.monotonic()
        if remaining<=0:return 'Local repair time budget reached during proposal generation; no variants submitted.'
        result=agent_tokens.variants(net,agent,slug,function,revision,[{'old':source,'new':s} for _,s in selected], 'local repairs',
                                     seconds=remaining,provenance_kind=provenance_kind,variant_families=[family for family,_ in selected])
        observations=getattr(net,'_local_trial_observations',{}).get((agent.id,fn['id']),[])
        for (family,_), measured in zip(selected,observations):
            repair_ranking.remember(net,effect_scope,family,baseline,measured)
            if measured and not measured.get('cached'):
                adaptive_repairs.record(net,policy_profile,focus,family,(baseline or {}).get('score'),
                                        measured.get('score'),measured.get('elapsed_ms',0))
        for (family,candidate),measured in zip(selected,observations):
            if not measured or measured.get('score') is None:
                continue
            with net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                           (fn['id'],scope,'local:'+family,hashlib.sha256(candidate.encode()).hexdigest()))
        current=net._function(function,slug)
        if (current['status']=='matched' and current['best_source'] in [candidate for _,candidate in selected]
                and recipe_profile==repair_sequences.profile(net,net._project(slug),fn)):
            expression_recipes.learn(net,recipe_profile,source,current['best_source'])
        return 'Local families: '+', '.join(f'{i}: {f}' for i,(f,_) in enumerate(selected,1))+'\n'+result


def available(net, agent, slug, fn, source, revision, requested):
    proj=net._project(slug);cfg=json.loads(proj['config'])
    flags=compiler_for(cfg,fn['vram']).get('flags',[]) if not net.fork_slug(slug) else []
    debug=any(re.fullmatch(r'-g[1-3]?',flag) for flag in flags)
    pools={family:[] for family in requested}
    if 'learned_expression' in pools:
        pools['learned_expression']=expression_recipes.variants(net,repair_sequences.profile(net,proj,fn),source)
    for family in ('signedness','declaration_order'):
        if family not in pools:
            continue
        for target in agent_targets.valid_targets(source,family):
            if agent_tokens.target_tried(net,fn,revision,family,target['old']):
                continue
            for patch in agent_tokens.family_patches(family,target['old']):
                edited=agent_targets.replace_anchor(source,patch['old'],patch['new'])
                pools[family].append(edited)
    if any(f in pools for f in ('table_slot_lifetime','unsigned_assignment_reuse','guard_forward_jump')):
        ctx = net._context(proj['id'],fn['unit'])
        header = net.forks.permuter_header(slug,fn['name']) if net.fork_slug(slug) else (ctx['header'] if ctx else '')
        for family,candidate in evidence_repairs.variants(source,fn['name'],header):
            if family in pools:
                pools[family].append(candidate)
    for family,candidate in pattern_repairs.variants(source,fn['name'],debug):
        if family in pools:
            pools[family].append(candidate)
    for family,candidate in register_repairs.variants(source,fn['name'],proj['arch']):
        if family in pools:
            pools[family].append(candidate)
    for family,candidate in captured_repairs.variants(source,fn['name']):
        if family in pools:
            pools[family].append(candidate)
    for family,candidate in lifetime_repairs.variants(source,fn['name']):
        if family in pools:
            pools[family].append(candidate)
    for module in (address_association_repairs, call_initialization_repairs, masked_reuse_repairs, callee_prototype_repairs, integer_lifetime_repairs, branch_layout_repairs, register_reuse_repairs, focused_lifetime_repairs):
        for family, candidate in module.variants(source, fn['name']):
            if family in pools:
                pools[family].append(candidate)
    if 'experimental_bitfield_frame' in pools:
        pools['experimental_bitfield_frame'] = [s for _, s in stack_layout.experimental_variants(source,fn['name'])]
    if 'local_widening' in pools:
        pools['local_widening'] = local_widening_repairs.variants(source, fn['name'])
    if 'pointer_cast_inline' in pools:
        pools['pointer_cast_inline'] = pointer_cast_repairs.variants(source,fn['name'],proj['arch'])
    if 'local_width' in pools:
        pools['local_width'] = local_width_repairs.variants(source,fn['name'])
    if 'stack_padding' in pools:
        pools['stack_padding'] = stack_padding_repairs.variants(source,fn['name'])
        measured = getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
        if measured and measured.get('source') == source:
            pools['stack_padding'] += stack_padding_repairs.grow(source,fn['name'],proj['arch'],measured.get('rows') or [])
    if 'spill_normalization' in pools and proj['arch'] == 'mips':
        from . import normalization_repairs
        measured = getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
        if measured and measured.get('source') == source:
            pools['spill_normalization'] = normalization_repairs.frame_variants(source,fn['name'],measured.get('rows') or [])
    if 'store_address_lifetime' in pools:
        pools['store_address_lifetime'] = store_address_repairs.variants(source,fn['name'],proj['arch'])
    if 'paired_load_order' in pools:
        pools['paired_load_order']=load_pair_repairs.variants(source,fn['name'])
    if 'mmio_address' in pools:
        pools['mmio_address'] = mmio_repairs.variants(source,fn['name'],proj['arch'])
    if 'narrowing' in pools:
        masks=lambda text:len(re.findall(r'&\s*0x(?:[fF]{4}|[fF]{2})\b',text))
        pools['narrowing'] += [s for s in expression_repairs.variants(source,fn['name'],proj['arch']) if masks(s)<masks(source)]
    if any(family in pools for family in draft_repairs.AGENT_FAMILIES):
        ctx=net._context(proj['id'],fn['unit'])
        header=net.forks.permuter_header(slug,fn['name']) if net.fork_slug(slug) else (ctx['header'] if ctx else '')
        labels={}
        for candidate in draft_repairs.variants(source,fn['name'],proj['arch'],fn['asm'] or '',flags,labels,limit=None,header=header,tables=switch_tables.read(net,proj,fn),callees=draft_repairs.capture_followup_repairs.callee_evidence(net,proj,fn,source)):
            family=labels[candidate]
            if family in pools and family in draft_repairs.AGENT_FAMILIES:
                pools[family].append(candidate)
    from .repair_preflight import invalid_call
    pools = {f: [s for s in pool if not invalid_call(s, fn['name'])] for f, pool in pools.items()}
    durable_scope=agent_tokens.target_scope(net,fn,'')
    pools={f:[s for s in dict.fromkeys(pool) if s!=source and not agent_tokens.compiled_before(net,fn,s,durable_scope) and not agent_tokens.target_tried(net,fn,revision,'local:'+f,s)] for f,pool in pools.items()}
    ranked = repair_ranking.order(pools,repair_ranking.evidence(net,fn['id'],agent.id))
    effect_scope=repair_ranking.effect_scope(net,fn,source)
    ranked=repair_ranking.demote(net,effect_scope,ranked)
    ranked=adaptive_repairs.order(net,proj,fn,repair_ranking.evidence(net,fn['id'],agent.id),ranked)
    pools = {f:pools[f] for f in ranked}
    return pools


def summary(net, agent, slug, fn, source, revision):
    """Exact counts from the runner's remaining proposal pool; no compiler/provider calls."""
    from ..forks.project import ForkError
    try:
        scope = agent_tokens.target_scope(net,fn,'')
        failure_key=agent_evidence.scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
        failure=getattr(net,'_baseline_failures',{}).get(failure_key)
        if failure:
            latest=net.store.one('SELECT MAX(id) AS id FROM attempts WHERE function_id=?',fn['id'])['id'] or 0
            if latest <= failure.get('after_attempt',0):
                return f'Remaining local repairs [{revision}]: baseline blocked. Fix C/build inputs or redraft before variants.'
        count = net.store.one('SELECT COUNT(*) AS n FROM agent_family_targets WHERE function_id=?',fn['id'])['n']
        measured = getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
        evidence = hashlib.sha256(repr((measured.get('score'),measured.get('rows')) if measured and measured.get('source')==source else None).encode()).hexdigest()
        key = (fn['id'],agent.id,revision,scope,count,evidence)
        cache = getattr(net,'_remaining_repairs',{})
        net._remaining_repairs = cache
        if key not in cache:
            pools = available(net,agent,slug,fn,source,revision,list(FAMILIES))
            counts = [(family,len(set(pool))) for family,pool in pools.items() if pool]
            total = len(set(candidate for pool in pools.values() for candidate in pool))
            cache[key] = (total,counts)
            while len(cache)>32:
                del cache[next(iter(cache))]
        total,counts = cache[key]
        if not total:
            return f'Remaining local repairs [{revision}]: 0. Do not retry unchanged families; inspect the diff or release with notes.'
        families = ', '.join(f'{family}={count}' for family,count in counts[:4])
        suffix = f'; {len(counts)-4} more families' if len(counts)>4 else ''
        return f'Remaining local repairs [{revision}]: {total} distinct proposals; {families}{suffix}. Use try_local_repairs to run up to six.'
    except (ForkError,NetworkError,OSError,KeyError):
        return 'Remaining local repairs: unavailable until build inputs are ready; this is not proof of exhaustion.'
