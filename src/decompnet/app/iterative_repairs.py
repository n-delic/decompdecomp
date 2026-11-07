"""A persistent, bounded beam of local repairs; only the normal judge accepts matches."""

import hashlib
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

from ..network import NetworkError
from ..project import compiler_for
from . import (
    allocation_evidence,
    first_difference,
    loop_repair_lane,
    paired_local_repairs,
    region_repairs,
    repair_effects,
    repair_selection,
    repair_sequence_lane,
    residual_followups,
    search_frontier,
    workers,
)

_PHASE_LOCKS_GUARD = threading.Lock()
WIDTH, DEPTH, PER_FUNCTION = 3, 4, 8
DETOUR_RATIO = 1.25
REGION_DETOUR_RATIO = 8
REGION_COMBINATIONS = 3  # children share the ordinary compile/time budget


def regional_detour(node, report, arch=None):
    """Spend one existing frontier slot on a real regional recovery, with no code growth.

    Scalar ratios can reject a recovered heading when allocation gets worse elsewhere.
    Bound this larger detour separately; neither this nor displayed rows accept a match.
    """
    score, rows = report.get('score'), report.get('rows') or []
    if not isinstance(score,(int,float)) or not 0<score<=node['ceiling']*REGION_DETOUR_RATIO:
        return False
    if any(not isinstance(r,(list,tuple)) or len(r)!=3 or not all(isinstance(v,str) for v in r) for r in rows):
        return False
    if not rows or sum(bool(r[1]) for r in rows)!=sum(bool(r[2]) for r in rows):
        return False
    return region_repairs.gain(node.get('region_baseline'),region_repairs.snapshot(rows,arch))>0


def allocation_detour(node, report, arch=None):
    """One scope step may worsen the score before a complementary lifetime repair."""
    if (node.get('path') or [''])[-1] not in {'loop_weight_scope','scalar_storage_reuse','row_number_staging','explicit_loop_backedge'}:return False
    score=report.get('score')
    before=allocation_evidence.diagnose(node.get('baseline_rows'),arch)
    after=allocation_evidence.diagnose(report.get('rows'),arch)
    return bool(isinstance(score,(int,float)) and 0<score<=node['ceiling']*2
                and before['register_only'] and after['target_instructions']
                and abs(after['candidate_instructions']-after['target_instructions'])<=1
                and before['target_frame']==before['candidate_frame']==after['candidate_frame']
                and allocation_evidence.signature(node.get('baseline_rows'),arch)!=allocation_evidence.signature(report.get('rows'),arch))


def retention_evidence(node, report, arch=None):
    """Ordinary search admission, shared with offline sequence audits; never a match judge."""
    score = report.get('score')
    repaired = search_frontier.mismatch_positions(node.get('baseline_rows')) - search_frontier.mismatch_positions(report.get('rows'))
    pattern = bool(repaired) and isinstance(score,(int,float)) and score<=node['ceiling']*2
    regional = regional_detour(node,report,arch)
    allocation = allocation_detour(node,report,arch)
    loop = loop_repair_lane.eligible(node,report,arch)
    structural = (isinstance(score,(int,float)) and score<=node['ceiling']*2
                  and search_frontier.structure_recovery(node.get('baseline_rows'),report.get('rows'),arch))
    eligible = (isinstance(score, (int, float)) and 0 < score and (score <= node['ceiling'] * DETOUR_RATIO or regional or allocation or structural or pattern or loop)
                and (loop or pattern or regional or score < node['ceiling'] or not search_frontier.unhelpful_detour(arch, node.get('baseline_rows'), report.get('rows') or [])))
    return dict(eligible=bool(report.get('matched') or eligible),score=score,pattern=pattern,regional=regional,
                allocation=allocation,loop=loop,structural=structural,repaired=repaired)


def expandable(parent, arch=None):
    return parent['depth']<DEPTH or repair_sequence_lane.eligible(parent,DEPTH,arch)


def children(state, parent, variants, *, priority=0, arch=None):
    """Keep the pending wave, but reserve a tiny burst for a structurally promising two-step repair."""
    if not expandable(parent,arch) or parent['source'] in state.setdefault('expanded', []):
        return 0
    report = {**parent['report'], 'repair_path': parent.get('path', [])}
    proposals = variants(parent['source'], report)
    proposals = residual_followups.order(proposals,parent.get('baseline_rows'),report.get('rows'),arch)
    loan=parent['depth']>=DEPTH
    if loan:proposals=repair_sequence_lane.select(state,parent,proposals)
    deferred = state.setdefault('deferred', [])
    if getattr(proposals, 'more', False):
        if not any(p['source'] == parent['source'] for p in deferred):
            deferred.append(parent)
    else:
        state['expanded'].append(parent['source'])
    new = []
    for proposal in proposals:
        family, source = proposal if isinstance(proposal, tuple) else ('local_repair', proposal)
        if source in state['seen']:
            continue
        state['seen'].append(source)
        new.append(dict(source=source, depth=parent['depth'] + 1,
                        ceiling=min(parent['ceiling'], parent['score']),
                        region_baseline=parent.get('regions'), baseline_rows=parent['report'].get('rows'),
                        path=parent.get('path', [])+[family], parent_source=parent['source'],
                        parent_output=output_key(parent['report']), parent_score=parent['score'],
                        unchanged_steps=parent.get('unchanged_steps', 0),
                        productive_steps=parent.get('productive_steps',0),sequence_loan=loan))
    # Finish up to three direct siblings before an immediate residual burst. The measured
    # short donor lane already recovers exact results; follow-ups must not interrupt it.
    siblings=[]
    if priority and residual_followups.kind(parent.get('baseline_rows'),report.get('rows'),arch):
        siblings=[n for n in state['pending'] if n.get('parent_source')==parent.get('parent_source')
                  and n.get('path')==parent.get('path')][:3]
    state['pending'] = siblings+new[:priority]+[n for n in state['pending'] if n not in siblings]+new[priority:]
    repair_effects.rotate(state)
    return len(new)


def advance(state, evaluate, variants, stopped, *, arch=None, stop_on_match=True, header=''):
    """Resume a bounded beam with one reserved slot for a temporarily worse source shape.

    Detours spend the same phase budget and never replace saved best C. A later repair may need
    an address or scope shape that initially makes register allocation worse.
    """
    if not state['pending'] and state.get('deferred') and not stopped():
        expand_wave(state, variants, arch)
    while state['pending'] and not stopped():
        node = state['pending'][0]
        report = evaluate(node['source'])
        if report is None:  # busy work stays retryable
            return False
        state['pending'].pop(0)
        if isinstance(report.get('score'), (int, float)) and not report.get('cached'):
            repair_effects.observe(state, node, output_key(report),score=report.get('score'),parent_score=node.get('parent_score'),rows=report.get('rows'))
        counts = state.setdefault('counts', {})
        if node.get('loop_probe'):counts['loop_evaluations']=counts.get('loop_evaluations',0)+1
        if node.get('sequence_loan'):counts['sequence_evaluations']=counts.get('sequence_evaluations',0)+1
        if (node.get('path') or [''])[-1] in {'paired_local','paired_inert'}:
            counts['paired_evaluations']=counts.get('paired_evaluations',0)+1
        outcome = 'matched' if report.get('matched') else 'compile_failed' if report.get('score') is None else 'scored'
        counts[outcome] = counts.get(outcome, 0) + 1
        repair_effects.rotate(state)
        if report.get('matched'):
            state['reason'] = 'matched'
            state['winning_path'] = state.get('winning_path', node.get('path', []))
            exact = state.setdefault('exact', [])
            if len(exact) < WIDTH:
                exact.append({k: node[k] for k in ('source', 'path', 'depth') if k in node})
            if stop_on_match:
                return True
            # Benchmark mode retains successful alternatives, but never mutates an adopted
            # live function. Exact nodes need no descendants; finish the remaining frontier.
            if not state['pending']:
                expand_wave(state, variants, arch)
            continue
        paired_local_repairs.offer(state,node,report,arch,header)
        if not report.get('cached'):
            from .inert_pair_search import offer
            for pair in offer(state,node.get('parent_source',''),node['source'],(node.get('path') or [''])[-1],
                              report.get('score'),node.get('parent_score'),node.get('parent_output'),output_key(report),
                              header=header,path=(node.get('path') or [])[:-1]):
                state['pending'].insert(min(3,len(state['pending'])),dict(pair,depth=node['depth']+1,
                    ceiling=node['ceiling'],parent_source=node.get('parent_source'),parent_output=node.get('parent_output'),
                    parent_score=node.get('parent_score'),baseline_rows=node.get('baseline_rows'),
                    region_baseline=node.get('region_baseline')))

        admission=retention_evidence(node,report,arch)
        score,pattern,regional,allocation,loop,structural,repaired=(admission[k] for k in
            ('score','pattern','regional','allocation','loop','structural','repaired'))
        if admission['eligible']:
            # Keep two source shapes per identical output at each depth: a later source edit can distinguish them.
            # This only prunes search branches, never supplies a match verdict.
            output = output_key(report)
            outputs = state.setdefault('outputs', {})
            unchanged = node.get('unchanged_steps', 0)+1 if output is not None and output == node.get('parent_output') else 0
            node['unchanged_steps'] = unchanged
            if output is not None and node.get('parent_output') is not None and output!=node['parent_output'] and score<node['ceiling']:
                node['productive_steps']=node.get('productive_steps',0)+1
            if unchanged:
                counts['unchanged_output'] = counts.get('unchanged_output', 0)+1
            if output is None or outputs.get(str(node['depth'])+':'+output, 0) < 2:
                regions = region_repairs.snapshot(report.get('rows'),arch)
                state['next'].append({**node, 'score': score, 'report': report, 'regions': regions, 'output':output,
                                      'region_gain': region_repairs.gain(node.get('region_baseline'), regions),
                                      'repaired':sorted(repaired) if pattern else []})
                if loop:loop_repair_lane.offer(state,state['next'][-1],variants,arch)
                if (pattern and residual_followups.kind(node.get('baseline_rows'),report.get('rows'),arch)
                        and state.get('residual_bursts',0)<residual_followups.BURSTS):
                    if children(state,state['next'][-1],variants,priority=residual_followups.PROBES,arch=arch):
                        state['residual_bursts']=state.get('residual_bursts',0)+1
                if allocation and expandable(state['next'][-1],arch) and state.get('allocation_bursts',0)<3:
                    if children(state,state['next'][-1],variants,priority=2,arch=arch):
                        state['allocation_bursts']=state.get('allocation_bursts',0)+1
                state['next'][-1]['region_damage'] = region_repairs.damage(node.get('region_baseline'), regions)
                if (score < node['ceiling'] and node.get('path') and node.get('baseline_rows')
                        and report.get('rows') and expandable(state['next'][-1],arch)
                        and search_frontier.structure(report['rows']) >= search_frontier.structure(node['baseline_rows'])
                        and not state['next'][-1]['region_damage']
                        and state.get('productive_bursts', 0) < 3):
                    if children(state, state['next'][-1], variants, priority=2,arch=arch):
                        state['productive_bursts'] = state.get('productive_bursts', 0)+1
                # A measured structure improvement earns two immediate combination probes, not
                # an unbounded recursive search. The original wave remains queued and resumable.
                if (node.get('baseline_rows') and report.get('rows') and expandable(state['next'][-1],arch)
                        and score <= node['ceiling'] and state.get('bridge_bursts', 0) < 3
                        and search_frontier.structure(report['rows']) < search_frontier.structure(node['baseline_rows'])
                        and not state['next'][-1]['region_damage']):
                    if children(state, state['next'][-1], variants, priority=2,arch=arch):
                        state['bridge_bursts'] = state.get('bridge_bursts', 0)+1
                # Flat scores often hide the first half of a combined repair. Reserve
                # one child probe for two distinct families, while preserving the wave.
                family = (node.get('path') or [None])[-1]
                bridges = state.setdefault('plateau_families', [])
                if (state.get('explore_plateaus') and node['depth'] == 1 and score == node['ceiling']
                        and family and family not in bridges and len(bridges) < 2):
                    if children(state, state['next'][-1], variants, priority=1,arch=arch):
                        bridges.append(family)
                if output is not None:
                    key = str(node['depth'])+':'+output
                    outputs[key] = outputs.get(key, 0) + 1
            else:
                counts['equivalent_output'] = counts.get('equivalent_output', 0) + 1
        measured=sum(counts.get(k,0) for k in ('matched','scored','compile_failed'))
        if (state.get('deferred') and measured%8==0 and state.get('inert_page_advances',0)<4
                and repair_effects.needs_page(state) and not stopped()):
            parent=state['deferred'].pop(0)
            children(state,parent,variants,priority=1,arch=arch)
            state['inert_page_advances']=state.get('inert_page_advances',0)+1
        if not state['pending']:
            expand_wave(state, variants, arch)
    state['reason'] = ('budget_or_stop' if state['pending'] else 'pending_proposal_pages' if state.get('deferred') else 'depth_limit' if state.get('counts', {}).get('depth_limit')
                       else 'supported_variants_exhausted')
    return bool(state.get('exact'))


def expand_wave(state, variants, arch):
    """Retain numeric and structural alternatives between bounded search waves."""
    deferred = state.pop('deferred', [])
    for parent in deferred:
        children(state, parent, variants,arch=arch)
    ranked = sorted(state['next'], key=lambda n: (*region_repairs.priority(n)[:2], n.get('unchanged_steps', 0), search_frontier.structure(n['report'].get('rows') or []),
                                                    stack_distance(n['report'].get('rows') or []), n['score']))
    ordinary = [n for n in ranked if n['score'] <= n['ceiling']]
    detours = sorted((n for n in ranked if n['score'] > n['ceiling']),
                     key=lambda n: (-n.get('region_gain', 0), n['score']))
    numeric = min(ordinary, key=lambda n: n['score']) if ordinary else None
    retained = [numeric] if numeric else []
    shapes = {allocation_evidence.signature(n['report'].get('rows'),arch) for n in retained}
    for candidate in ordinary:
        shape = allocation_evidence.signature(candidate['report'].get('rows'),arch)
        if shape not in shapes:
            retained.append(candidate);shapes.add(shape)
    retained += [n for n in ordinary if n not in retained]
    structural = [n for n in ranked if n['score']<=n['ceiling']*2 and
                  search_frontier.structure_recovery(n.get('baseline_rows'),n['report'].get('rows'),arch)][:1]
    repaired = sorted((n for n in ranked if n.get('repaired')),
                      key=lambda n:(-len(n['repaired']),n.get('region_damage',0),n['score']))[:1]
    regional = search_frontier.regional_choices(state['next'])[:1]
    # Keep the numeric winner, a regionally recovered detour and one distinct sibling.
    fallback = retained[:WIDTH-1]+detours[:1] if detours else retained[:WIDTH]
    # Reserve a slot for a different measured output at equal score, even when the
    # coarse allocation signature is identical. Keep two source shapes per output above.
    diverse=[]
    if numeric and state.get('output_diversity',True):
        output=output_key(numeric['report'])
        diverse=[n for n in ordinary if n['score']==numeric['score'] and output is not None
                 and output_key(n['report']) is not None and output_key(n['report'])!=output][:1]
    from ..diff_summary import summarize
    def position_rank(node):
        count=summarize(node['report'].get('rows') or [])['position_differences']
        return (count if count is not None else float('inf'),node['score'])
    positioned=sorted(ordinary,key=position_rank)[:1]
    bridges=[n for n in ranked if n['score']<=n['ceiling']*2 and
             search_frontier.allocation_bridge(n.get('baseline_rows'),n['report'].get('rows'),arch)][:1]
    best = list(dict.fromkeys(n['source'] for n in retained[:1]+bridges+positioned+repaired+structural+regional+diverse+fallback))[:WIDTH]
    by_source = {n['source']:n for n in ranked}
    best = [by_source[source] for source in best]
    state['next'] = []
    recovered = [n for n in best if n.get('region_gain',0)>0]
    burst_parent = min(recovered,key=region_repairs.priority) if recovered else None
    for parent in best:
        if not expandable(parent,arch):
            state.setdefault('counts', {})['depth_limit'] = state.get('counts', {}).get('depth_limit', 0) + 1
            continue
        # Probe the best recovered region first, after comparing the entire wave.
        burst = (parent is burst_parent and
                 state.get('regional_bursts', 0)<REGION_COMBINATIONS)
        if children(state, parent, variants, priority=2 if burst else 0,arch=arch) and burst:
            state['regional_bursts'] = state.get('regional_bursts', 0)+1


def stack_distance(rows):
    """Advisory distance breaks flat-score frame ties, never a match verdict."""
    from .permuter_focus import stack_distance as distance
    return distance(rows)


def repair_rules():
    from ..claim_stalls import file_digest
    from ..claim_stalls import repair_rules as rules
    from . import repair_ranking
    return rules() + [('repair_ranking', file_digest(Path(repair_ranking.__file__)))]


def output_key(report):
    if 'output_fingerprint' in report:
        return report['output_fingerprint']  # explicitly unknown formats must not borrow masked relocations
    if report.get('candidate_target') is None:
        return None  # displayed rows alone are insufficient evidence of identical compiler output
    return hashlib.sha256(repr((report['candidate_target'], report.get('candidate_relocs'))).encode()).hexdigest()


def targeted_pool(source, report, fn, arch, flags, header='', tables=(), callees='', *, focused=True, entry_scope=None):
    from . import (
        draft_repairs,
        independent_address_repairs,
        normalization_repairs,
        stack_padding_repairs,
        value_trace_repairs,
    )
    labels = {}
    proposals = draft_repairs.variants(source, fn['name'], arch, fn['asm'] or '', flags, labels, header=header, limit=None, tables=tables,callees=callees,entry_scope=entry_scope)
    from . import hazard_line_repairs
    hazards=hazard_line_repairs.variants(source,fn['name'],header,arch,report.get('rows') or [])
    independent=independent_address_repairs.variants(source,fn['name'],arch,report.get('rows') or [],header)
    from . import targeted_storage_repairs
    storage=targeted_storage_repairs.variants(source,fn['name'],header,arch,report.get('rows') or [],report.get('repair_locations'))
    from . import narrow_load_subtraction, parameter_carriers
    carriers=parameter_carriers.variants(source,fn["name"],header,arch)
    load_subtractions=narrow_load_subtraction.variants(source,fn["name"],header,arch)
    from . import scalar_storage_reuse
    reuse=scalar_storage_reuse.variants(source,fn['name'],header,arch,report.get('repair_locations'))
    from . import loop_storage_shapes
    loops=loop_storage_shapes.variants(source,fn['name'],header,arch,report.get('repair_locations'))
    from . import scan_load_repairs
    scans=scan_load_repairs.variants(source,fn['name'],header,arch,report.get('rows') or [],report.get('repair_locations'))
    operand_focused = carriers + load_subtractions + loops + reuse + scans + storage + hazards + independent + draft_repairs.affine_operand_repairs.operand_variants(source, fn['name'], report.get('rows') or [])
    proposals = list(dict.fromkeys([text for _, text in operand_focused]+proposals))
    labels.update({text: family for family, text in operand_focused})
    for family,text in value_trace_repairs.variants(source,fn['name'],report.get('value_trace'),report.get('rows'),header):
        proposals.append(text);labels[text]=family
    for text in stack_padding_repairs.grow(source, fn['name'], arch, report.get('rows') or []):
        proposals.append(text)
        labels[text] = 'stack_padding'
    if arch == 'mips':
        for text in normalization_repairs.frame_variants(source, fn['name'], report.get('rows') or []):
            proposals.append(text)
            labels[text] = 'spill_normalization'
    families = region_repairs.order(list(dict.fromkeys(labels[p] for p in proposals)), report.get('rows') or [])
    families = repair_selection.order(families, report.get('repair_path', []))
    pools = [[p for p in proposals if labels[p]==family] for family in families]
    result = list(dict.fromkeys(p for row in zip_longest(*pools) for p in row if p is not None))
    tagged=[(labels[p],p) for p in result]
    from . import allocation_search
    lane = allocation_search.proposals(source,fn['name'],arch,report.get('rows'),result,labels,
                                       {'value_trace':report.get('value_trace')},header)
    tagged = list(dict.fromkeys(lane+tagged))
    from . import repair_localisation
    tagged = repair_localisation.proposals(source,tagged,report.get("repair_locations"))
    # Retain established operand-supported focus. Broad reordering needs held-out replay evidence.
    use_focus=focused and bool(operand_focused or report.get('broad_focus_approved'))
    tagged=first_difference.order(source,tagged,report.get('rows')) if use_focus else tagged
    near_loops=loop_storage_shapes.variants(source,fn['name'],header,arch,report.get('repair_locations'),mapped_only=True)
    # A small source-mapped prefix avoids an unrelated loop consuming the first
    # compile. Broad families remain in the page and the ordinary judge is unchanged.
    from .repair_ranking import GUARDED
    guarded=[item for item in tagged if item[0] in GUARDED]
    diagnosis=allocation_evidence.diagnose(report.get('rows'),arch)
    # Source-token proximity can demote a coordinated storage cycle because its
    # edits span both arms. Reserve a finite lane after operand/localisation ranking.
    preferred=('parameter_carriers',) if diagnosis['register_only'] else (
        ('narrow_load_subtraction','one_shot_scope') if diagnosis['scheduling'] else ())
    storage_lane=[item for item in tagged if item[0] in preferred][:12]
    return list(dict.fromkeys(guarded+near_loops[:3]+storage_lane+tagged))


def targeted_variants(source, report, fn, arch, flags, header='', tables=(), callees='', *, tagged=False,
                      selection=None, limit=20, pool_cache=None, focused=True, entry_scope=None):
    key = hashlib.sha256(repr((source, report.get('rows'), report.get('repair_path'), report.get('value_trace'), report.get('broad_focus_approved'), report.get('repair_locations'), fn['name'], fn['asm'],
                              arch, flags, header, tables, callees, focused, entry_scope)).encode()).hexdigest()
    result = pool_cache.get(key) if pool_cache is not None else None
    if result is None:
        result = targeted_pool(source, report, fn, arch, flags, header, tables, callees, focused=focused, entry_scope=entry_scope)
        if pool_cache is not None:
            pool_cache[key] = result
            while len(pool_cache) > 2:
                del pool_cache[next(iter(pool_cache))]
    preferred = repair_selection.COMPLEMENTS.get((report.get('repair_path') or [''])[-1], ())
    quota = min(repair_selection.FOLLOWUP_WINDOW, max(0, limit-2))
    prefix = [item for item in result if item[0] in preferred[:1]][:quota]
    reserved = [item for item in result if preferred and item[0] not in preferred][:min(2, limit)]
    result = list(dict.fromkeys(prefix+reserved+result))
    if selection is not None:
        selected = repair_selection.page(result, source, selection, limit)
        return selected if tagged else repair_selection.Page([p for _, p in selected], more=selected.more)
    return result[:limit] if tagged else [p for _, p in result[:limit]]


def run(twins, slug, job):
    """One iterative phase per project owns its persisted frontier at a time."""
    with _PHASE_LOCKS_GUARD:
        locks=getattr(twins.net,'_iterative_phase_locks',{})
        twins.net._iterative_phase_locks=locks
        lock=locks.setdefault(slug,threading.Lock())
    if not lock.acquire(blocking=False):return 0
    try:
        return _run(twins,slug,job)
    finally:
        lock.release()


def _run(twins, slug, job):
    """Use a separate phase budget and resume after the last visited function."""
    from .. import candidate_scope
    from . import (
        adaptive_repairs,
        address_repairs,
        expression_recipes,
        free_candidates,
        repair_sequences,
        similar_matches,
    )

    net = twins.net
    proj = net.project_row(slug)
    # The current proposals use MIPS linked feedback; object projects need file-context adaptation.
    if not net.is_rom(proj) or proj['arch'] != 'mips' or net.fork_slug(slug) or slug in net.links:
        return 0
    free_candidates.budget(job)
    limit, deadline = job.reuse_limit, job.reuse_deadline
    agent, _ = net.local_agent('autopilot')
    cfg = net.cfg(proj)
    symbols = [(r['name'], r['address']) for r in net.project_store(proj['id']).all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    known = {name for name, _ in symbols}
    matched = [0]
    reasons = Counter()
    diagnostics = Counter()
    from . import prototype_reuse
    rules = repair_rules()
    rows = net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
                         "AND best_score>0 AND best_score<=?*MAX(1,n_insns) "
                         "ORDER BY id", proj['id'], 100 - similar_matches.REPAIR_PERCENT)
    cursor = net.project_store(proj['id']).one('SELECT function_id FROM repair_cursors WHERE project_id=?', proj['id'])
    last = cursor['function_id'] if cursor else 0
    if getattr(job,'finish_near',False):
        from . import finish_near
        rows=finish_near.ordered(net,proj,rows)
    else:
        rows.sort(key=lambda fn: (fn['id'] <= last, fn['id']))
    completed=[0]
    guard = threading.Lock()  # (the job's counters are shared by every thread's function)

    def visit(fn):
        if free_candidates.exhausted(job) or job.reuse_attempts >= limit or time.monotonic() >= deadline:
            return
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_cursors VALUES(?,?)', (proj['id'], fn['id']))
        if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
            return
        if getattr(job,'finish_near',False):finish_near.visited(net,proj,fn)
        header = net._context(proj['id'], fn['unit'])['header']
        settings = compiler_for(cfg, fn['vram'], fn)
        from . import verified_unit_context
        donor_signature=(prototype_reuse.signature(net,proj,fn['best_source']),
                         verified_unit_context.signature(net,proj,fn,fn['best_source']))
        recipe_profile=repair_sequences.profile(net,proj,fn)
        context = similar_matches.trial_key('', header, settings, fn, ['beam-v15', donor_signature, candidate_scope.key(net, proj, fn), symbols, rules, expression_recipes.signature(net,recipe_profile)])
        saved = net.project_store(proj['id']).one('SELECT context,state FROM repair_frontiers WHERE function_id=?', fn['id'])
        state = json.loads(saved['state']) if saved and saved['context'] == context else None
        if state is None or fn['best_source'] not in state['seen']:
            state = dict(pending=[dict(source=fn['best_source'], depth=0, ceiling=fn['best_score'])],
                         next=[], seen=[fn['best_source']], explore_plateaus=True)
        build_scope = candidate_scope.key(net, proj, fn)
        policy_profile = adaptive_repairs.profile(net, proj, fn)
        from . import repair_promotion
        broad_focus=repair_promotion.approved(net,policy_profile,'broad_first_difference')
        tries = [0]  # this function's own attempts: other threads are spending the job's at the same time

        def stopped():
            return (free_candidates.exhausted(job) or job.reuse_attempts >= limit or tries[0] >= PER_FUNCTION
                    or time.monotonic() >= deadline)

        def initial_trace(source, report):
            node=state['pending'][0] if state['pending'] else {}
            trace=state.get('value_trace') or {}
            if trace.get('source_sha256')==hashlib.sha256(source.encode()).hexdigest():
                report['value_trace']=trace
            from .permuter_focus import classify
            # A baseline trace can direct the first repair, even when no prior edit improved.
            if (not free_candidates.exhausted(job) and isinstance(report.get('score'),(int,float))
                    and report['score']>0 and getattr(job,'finish_near',False) and not node.get('parent_source') and not state.get('initial_trace_attempted')
                    and not report.get('matched') and classify(report.get('rows') or [])=='register'
                    and tries[0]+2<=PER_FUNCTION and time.monotonic()+40<deadline):
                with guard:
                    reserved=job.reuse_attempts+2<=limit
                    if reserved:job.reuse_attempts+=2
                if reserved:
                    from . import lifetime_diagnostics
                    spent=[0]
                    def trace_compile():spent[0]+=1
                    state['initial_trace_attempted']=True
                    try:
                        state['value_trace']=lifetime_diagnostics.single(settings,header,source,fn['name'],on_compile=trace_compile)
                    finally:
                        tries[0]+=spent[0]
                        with guard:job.reuse_attempts-=2-spent[0]
                    state.setdefault('counts',{})['trace_compiles']=spent[0]
                    report['value_trace']=state['value_trace']
                    state['value_trace_rows']=report.get('rows') or []
            state.setdefault('sequence_focus',repair_sequences.focus(report.get('rows'),proj['arch']))
            return report

        def evaluate(source):
            # Only reuse reports under the full frozen input scope, never legacy partial keys.
            for kind in ('iterative-v2',):
                key = similar_matches.trial_key(source, header, settings, fn, [kind, build_scope, symbols])
                cached = net.project_store(proj['id']).one("SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=? AND outcome='miss'",
                                       fn['id'], key)
                if cached:
                    return initial_trace(source,{**json.loads(cached['feedback']),'cached':True})
            key = similar_matches.trial_key(source, header, settings, fn, ['iterative-v2', build_scope, symbols])
            with guard:
                job.reuse_attempts += 1
            tries[0] += 1
            started = time.monotonic()
            try:
                report = net.try_quietly(agent, proj, fn, source)
            except NetworkError:
                return None
            from .permuter_focus import classify
            node = state['pending'][0] if state['pending'] else {}
            adaptive_repairs.record(net, policy_profile, classify(node.get('baseline_rows') or []),
                (node.get('path') or [None])[-1], node.get('ceiling'), report.get('score'),
                (time.monotonic()-started)*1000, origin='iterative repairs', exact=bool(report.get('matched')))
            path=node.get('path') or []
            if path:
                repair_sequences.observe(net,recipe_profile,state.get('sequence_focus',''),path[:-1],path[-1],
                    node.get('ceiling'),report.get('score'),node.get('parent_output'),output_key(report),exact=bool(report.get('matched')))
                if report.get('matched'):
                    repair_sequences.learn(net,recipe_profile,state.get('sequence_focus',''),path,
                        max_steps=DEPTH+repair_sequence_lane.EXTRA_DEPTH if node.get('sequence_loan') else DEPTH)
            if report.get('matched') and state['pending'] and state['pending'][0].get('parent_source'):
                expression_recipes.learn(net,recipe_profile,state['pending'][0]['parent_source'],source)
            report=initial_trace(source,report)
            # At most one pair of optional phase captures per frozen function frontier.
            # Reserve their worst-case four compiler runs from this same phase budget.
            if (not state.get('lifetime_diagnostics_attempted') and not report.get('matched')
                    and isinstance(report.get('score'), (int, float)) and report['score'] > 0
                    and report['score'] < node.get('ceiling', 0)
                    and classify(report.get('rows') or []) == 'register'
                    and node.get('parent_source') and tries[0]+4 <= PER_FUNCTION
                    and time.monotonic()+80 < deadline):
                with guard:
                    reserved = job.reuse_attempts+4 <= limit
                    if reserved:
                        job.reuse_attempts += 4
                if reserved:
                    from . import lifetime_diagnostics
                    spent = [0]
                    def diagnostic_compile():
                        spent[0] += 1
                    state['lifetime_diagnostics_attempted'] = True
                    try:
                        state['lifetime_diagnostics'] = lifetime_diagnostics.investigate(
                            settings, header, node['parent_source'], source, fn['name'],
                            on_compile=diagnostic_compile)
                    finally:
                        tries[0] += spent[0]
                        with guard:
                            job.reuse_attempts -= 4-spent[0]
                    state.setdefault('counts', {})['diagnostic_compiles'] = spent[0]
                    evidence = state['lifetime_diagnostics']
                    after=evidence.get('after',{})
                    if after.get('output_unchanged') and after.get('value_trace'):
                        report['value_trace']=after['value_trace']
                        state['value_trace']=after['value_trace']
                        state['value_trace_rows']=report.get('rows') or []
                    note = evidence.get('comparison', {}).get('lead') or evidence.get('skipped') or 'No comparable allocation evidence.'
                    changes = evidence.get('comparison', {}).get('allocation_priority_changes', [])
                    if changes:
                        note += '; '+ '; '.join('C lines '+','.join(map(str,c['source_lines']))+': '+c['next_action'] for c in changes[:2])
                    net.add_note(agent, slug, 'Optional compiler lifetime diagnostics: '+note,
                                 fn['name'])
            outcome = 'match' if report.get('matched') else 'miss'
            path = state['pending'][0].get('path', []) if state['pending'] else []
            reason = 'iterative local repair; ' + ('byte match' if outcome == 'match' else f"score {report.get('score')}") + '; path: ' + ' → '.join(path)
            with net.project_store(proj['id']).tx() as db:
                # Unscored reports mix syntax errors with resource/tool failures. Keep
                # their audit row, but do not poison persistent candidate caching.
                if report.get('score') is not None:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                               (fn['id'], key, outcome, reason, json.dumps(report)))
                db.execute('INSERT INTO free_match_attempts(project_id,function_id,strategy,outcome,reason,created_at) VALUES(?,?,?,?,?,?)',
                           (proj['id'], fn['id'], 'iterative-repair', outcome, reason, time.time()))
            return report

        pool_cache = {}  # Ephemeral, bounded proposal reuse; compiler verdict caches remain separate.
        def variants(source, report):
            from . import prototype_reuse
            shared = prototype_reuse.variants(net,proj,fn,source)
            unit_candidates=verified_unit_context.variants(net,proj,fn,source)
            report={**report,'broad_focus_approved':broad_focus}
            guided = address_repairs.variants(source, report, fn, bool(proj['big_endian']), known)
            if report.get('candidate_target'):
                compiled = dict(target=bytes.fromhex(report['candidate_target']), relocs=report.get('candidate_relocs', '[]'))
                guided += similar_matches.literal_variants(source, compiled, fn, bool(proj['big_endian']))
            from . import draft_repairs, switch_tables
            targeted = targeted_variants(source, report, fn, proj['arch'], settings.get('flags', []), header, switch_tables.read(net,proj,fn), draft_repairs.capture_followup_repairs.callee_evidence(net,proj,fn,source), tagged=True, selection=state.setdefault('proposal_cursors', {}), limit=8, pool_cache=pool_cache, entry_scope=draft_repairs.promoted_entry_repairs.scope(net,proj,fn))
            families = list(dict.fromkeys(family for family, _ in targeted))
            ordered = repair_selection.order(adaptive_repairs.order(net, proj, fn, report.get('rows') or [], families),
                                             report.get('repair_path', []))
            preferred=repair_sequences.next_families(net,recipe_profile,state.get('sequence_focus',''),report.get('repair_path',[]))
            # Alternate history-led and ordinary passes; no family is removed.
            if state.get('counts',{}).get('scored',0)%2==0:
                ordered=sorted(ordered,key=lambda f:(f not in preferred,ordered.index(f)))
            targeted.sort(key=lambda item: ordered.index(item[0]))
            # Interleave pools so address proposals cannot starve targeted families (or vice versa).
            learned = expression_recipes.variants(net, recipe_profile, source)
            from . import repair_localisation
            guided_tagged=repair_localisation.proposals(source,[('address_or_literal',s) for s in guided[:4]],report.get('repair_locations'))
            pools = [shared, guided_tagged, targeted,
                     [('similar_repair', s) for s in similar_matches.repair_variants(source, report)[:4]],
                     [('learned_expression', s) for s in learned[:2]],unit_candidates]
            return repair_selection.Page(dict.fromkeys(item for group in zip_longest(*pools)
                                      for item in group if item is not None), more=getattr(targeted, 'more', False))

        before_counts = Counter(state.get('counts', {}))
        if advance(state, evaluate, variants, stopped, arch=proj['arch'],header=header):
            with guard:
                matched[0] += 1
                job.reuse_matched += 1
                job.reuse_bytes += fn['size']
            net.add_note(agent, slug, 'Autopilot matched with iterative local repairs', fn['name'])
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_frontiers VALUES(?,?,?)', (fn['id'], context, json.dumps(state)))
        with guard:
            reasons[state.get('reason', 'busy_or_paused')] += 1
            diagnostics.update(Counter(state.get('counts', {}))-before_counts)
            completed[0]+=1
            if getattr(job,'near_progress',None):job.near_progress(completed[0],len(rows),fn['name'])

    # One thread to a function at a time (Settings: "Iterative repairs: threads"), on a pool of their own: this
    # runs on one of the shared workers, which would wait for the very threads it is waiting on.
    threads = 1 if getattr(job,'finish_near',False) else workers.clamp(getattr(getattr(net, 'cascades', None), 'iterative_threads', 0))
    with ThreadPoolExecutor(threads, thread_name_prefix='iterative-repair', initializer=workers.lower_priority) as pool:
        list(pool.map(visit, rows))
    if reasons:
        net.add_note(agent, slug, 'Iterative search function outcomes: '+', '.join(f'{k}: {v}' for k,v in sorted(reasons.items()))+
                     f". {diagnostics['compile_failed']} failed/unscored reports; {diagnostics['equivalent_output']} equivalent-output branches pruned. "
                     'Pending paths resume on the next run; these are bounded supported repairs, not all possible C.')
    return matched[0]


def optional_phase(twins, slug, parent):
    """Opt-in after normal autopilot; background cascades keep their original budget and behaviour."""
    from . import free_candidates
    from .twins import Job

    config = twins.net.cascades
    if not config.iterative_enabled or parent.stop.is_set():
        return 0
    phase = Job(stop=parent.stop)
    free_candidates.budget(phase, config.iterative_seconds, config.iterative_attempts)
    started = time.monotonic()
    matched = run(twins, slug, phase)
    net = twins.net
    proj = net.project_row(slug)
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT INTO iterative_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                   (proj['id'], phase.reuse_attempts, phase.reuse_matched, phase.reuse_bytes,
                    time.monotonic() - started, time.time()))
    agent, _ = net.local_agent('autopilot')
    net.add_note(agent, slug, f'Iterative search: {matched} matches, {phase.reuse_bytes} bytes, '
                 f'{phase.reuse_attempts} attempts (separate budget).')
    return matched
