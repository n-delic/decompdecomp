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
from . import allocation_evidence, region_repairs, search_frontier, workers

WIDTH, DEPTH, PER_FUNCTION = 3, 4, 8
DETOUR_RATIO = 1.25
REGION_DETOUR_RATIO = 8
REGION_COMBINATIONS = 3  # children share the ordinary compile/time budget


def regional_detour(node, report):
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
    return region_repairs.gain(node.get('region_baseline'),region_repairs.snapshot(rows))>0


def children(state, parent, variants, *, priority=0):
    """Keep the pending wave, but reserve a tiny burst for a structurally promising two-step repair."""
    if parent['depth'] >= DEPTH or parent['source'] in state.setdefault('expanded', []):
        return 0
    state['expanded'].append(parent['source'])
    new = []
    for proposal in variants(parent['source'], parent['report']):
        family, source = proposal if isinstance(proposal, tuple) else ('local_repair', proposal)
        if source in state['seen']:
            continue
        state['seen'].append(source)
        new.append(dict(source=source, depth=parent['depth'] + 1,
                        ceiling=min(parent['ceiling'], parent['score']),
                        region_baseline=parent.get('regions'), baseline_rows=parent['report'].get('rows'),
                        path=parent.get('path', [])+[family], parent_source=parent['source'],
                        parent_output=output_key(parent['report']),
                        unchanged_steps=parent.get('unchanged_steps', 0)))
    state['pending'] = new[:priority]+state['pending']+new[priority:]
    return len(new)


def advance(state, evaluate, variants, stopped, *, arch=None, stop_on_match=True):
    """Resume a bounded beam with one reserved slot for a temporarily worse source shape.

    Detours spend the same phase budget and never replace saved best C. A later repair may need
    an address or scope shape that initially makes register allocation worse.
    """
    while state['pending'] and not stopped():
        node = state['pending'][0]
        report = evaluate(node['source'])
        if report is None:  # busy work stays retryable
            return False
        state['pending'].pop(0)
        counts = state.setdefault('counts', {})
        outcome = 'matched' if report.get('matched') else 'compile_failed' if report.get('score') is None else 'scored'
        counts[outcome] = counts.get(outcome, 0) + 1
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
        score = report.get('score')
        regional = regional_detour(node,report)
        if (isinstance(score, (int, float)) and 0 < score and (score <= node['ceiling'] * DETOUR_RATIO or regional)
                and (regional or score < node['ceiling'] or not search_frontier.unhelpful_detour(arch, node.get('baseline_rows'), report.get('rows') or []))):
            # Keep two source shapes per identical output at each depth: a later source edit can distinguish them.
            # This only prunes search branches, never supplies a match verdict.
            output = output_key(report)
            outputs = state.setdefault('outputs', {})
            unchanged = node.get('unchanged_steps', 0)+1 if output is not None and output == node.get('parent_output') else 0
            node['unchanged_steps'] = unchanged
            if unchanged:
                counts['unchanged_output'] = counts.get('unchanged_output', 0)+1
            if output is None or outputs.get(str(node['depth'])+':'+output, 0) < 2:
                regions = region_repairs.snapshot(report.get('rows'))
                state['next'].append({**node, 'score': score, 'report': report, 'regions': regions,
                                      'region_gain': region_repairs.gain(node.get('region_baseline'), regions)})
                state['next'][-1]['region_damage'] = region_repairs.damage(node.get('region_baseline'), regions)
                # A measured structure improvement earns two immediate combination probes, not
                # an unbounded recursive search. The original wave remains queued and resumable.
                if (node.get('baseline_rows') and report.get('rows') and node['depth'] < DEPTH
                        and score <= node['ceiling'] and state.get('bridge_bursts', 0) < 3
                        and search_frontier.structure(report['rows']) < search_frontier.structure(node['baseline_rows'])
                        and not state['next'][-1]['region_damage']):
                    if children(state, state['next'][-1], variants, priority=2):
                        state['bridge_bursts'] = state.get('bridge_bursts', 0)+1
                # Flat scores often hide the first half of a combined repair. Reserve
                # one child probe for two distinct families, while preserving the wave.
                family = (node.get('path') or [None])[-1]
                bridges = state.setdefault('plateau_families', [])
                if (state.get('explore_plateaus') and node['depth'] == 1 and score == node['ceiling']
                        and family and family not in bridges and len(bridges) < 2):
                    if children(state, state['next'][-1], variants, priority=1):
                        bridges.append(family)
                if output is not None:
                    key = str(node['depth'])+':'+output
                    outputs[key] = outputs.get(key, 0) + 1
            else:
                counts['equivalent_output'] = counts.get('equivalent_output', 0) + 1
        if not state['pending']:
            expand_wave(state, variants, arch)
    state['reason'] = ('budget_or_stop' if state['pending'] else 'depth_limit' if state.get('counts', {}).get('depth_limit')
                       else 'supported_variants_exhausted')
    return bool(state.get('exact'))


def expand_wave(state, variants, arch):
    """Retain numeric and structural alternatives between bounded search waves."""
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
    regional = search_frontier.regional_choices(state['next'])[:1]
    # Keep the numeric winner, a regionally recovered detour and one distinct sibling.
    fallback = retained[:WIDTH-1]+detours[:1] if detours else retained[:WIDTH]
    best = list(dict.fromkeys(n['source'] for n in retained[:1]+regional+fallback))[:WIDTH]
    by_source = {n['source']:n for n in ranked}
    best = [by_source[source] for source in best]
    state['next'] = []
    recovered = [n for n in best if n.get('region_gain',0)>0]
    burst_parent = min(recovered,key=region_repairs.priority) if recovered else None
    for parent in best:
        if parent['depth'] >= DEPTH:
            state.setdefault('counts', {})['depth_limit'] = state.get('counts', {}).get('depth_limit', 0) + 1
            continue
        # Probe the best recovered region first, after comparing the entire wave.
        burst = (parent is burst_parent and
                 state.get('regional_bursts', 0)<REGION_COMBINATIONS)
        if children(state, parent, variants, priority=2 if burst else 0) and burst:
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
    if report.get('candidate_target') is None:
        return None  # displayed rows alone are insufficient evidence of identical compiler output
    return hashlib.sha256(repr((report['candidate_target'], report.get('candidate_relocs'))).encode()).hexdigest()


def targeted_variants(source, report, fn, arch, flags, header='', tables=(), callees='', *, tagged=False):
    from . import draft_repairs, normalization_repairs, stack_padding_repairs
    labels = {}
    proposals = draft_repairs.variants(source, fn['name'], arch, fn['asm'] or '', flags, labels, header=header, limit=None, tables=tables,callees=callees)
    for text in stack_padding_repairs.grow(source, fn['name'], arch, report.get('rows') or []):
        proposals.append(text)
        labels[text] = 'stack_padding'
    if arch == 'mips':
        for text in normalization_repairs.frame_variants(source, fn['name'], report.get('rows') or []):
            proposals.append(text)
            labels[text] = 'spill_normalization'
    families = region_repairs.order(list(dict.fromkeys(labels[p] for p in proposals)), report.get('rows') or [])
    pools = [[p for p in proposals if labels[p]==family] for family in families]
    result = [p for row in zip_longest(*pools) for p in row if p is not None][:20]
    return [(labels[p], p) for p in result] if tagged else result


def run(twins, slug, job):
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
    symbols = [(r['name'], r['address']) for r in net.store.all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    known = {name for name, _ in symbols}
    matched = [0]
    reasons = Counter()
    diagnostics = Counter()
    rules = repair_rules()
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
                         "AND best_score>0 AND best_score<=?*MAX(1,n_insns) "
                         "ORDER BY id", proj['id'], 100 - similar_matches.REPAIR_PERCENT)
    cursor = net.store.one('SELECT function_id FROM repair_cursors WHERE project_id=?', proj['id'])
    last = cursor['function_id'] if cursor else 0
    rows.sort(key=lambda fn: (fn['id'] <= last, fn['id']))
    guard = threading.Lock()  # (the job's counters are shared by every thread's function)

    def visit(fn):
        if free_candidates.exhausted(job) or job.reuse_attempts >= limit or time.monotonic() >= deadline:
            return
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_cursors VALUES(?,?)', (proj['id'], fn['id']))
        if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
            return
        header = net._context(proj['id'], fn['unit'])['header']
        settings = compiler_for(cfg, fn['vram'])
        recipe_profile=repair_sequences.profile(net,proj,fn)
        context = similar_matches.trial_key('', header, settings, fn, ['beam-v9', candidate_scope.key(net, proj, fn), symbols, rules, expression_recipes.signature(net,recipe_profile)])
        saved = net.store.one('SELECT context,state FROM repair_frontiers WHERE function_id=?', fn['id'])
        state = json.loads(saved['state']) if saved and saved['context'] == context else None
        if state is None or fn['best_source'] not in state['seen']:
            state = dict(pending=[dict(source=fn['best_source'], depth=0, ceiling=fn['best_score'])],
                         next=[], seen=[fn['best_source']], explore_plateaus=True)
        build_scope = candidate_scope.key(net, proj, fn)
        policy_profile = adaptive_repairs.profile(net, proj, fn)
        tries = [0]  # this function's own attempts: other threads are spending the job's at the same time

        def stopped():
            return (free_candidates.exhausted(job) or job.reuse_attempts >= limit or tries[0] >= PER_FUNCTION
                    or time.monotonic() >= deadline)

        def evaluate(source):
            # Only reuse reports under the full frozen input scope, never legacy partial keys.
            for kind in ('iterative-v2',):
                key = similar_matches.trial_key(source, header, settings, fn, [kind, build_scope, symbols])
                cached = net.store.one("SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=? AND outcome='miss'",
                                       fn['id'], key)
                if cached:
                    return json.loads(cached['feedback'])
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
            if report.get('matched') and state['pending'] and state['pending'][0].get('parent_source'):
                expression_recipes.learn(net,recipe_profile,state['pending'][0]['parent_source'],source)
            outcome = 'match' if report.get('matched') else 'miss'
            path = state['pending'][0].get('path', []) if state['pending'] else []
            reason = 'iterative local repair; ' + ('byte match' if outcome == 'match' else f"score {report.get('score')}") + '; path: ' + ' → '.join(path)
            with net.store.tx() as db:
                # Unscored reports mix syntax errors with resource/tool failures. Keep
                # their audit row, but do not poison persistent candidate caching.
                if report.get('score') is not None:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                               (fn['id'], key, outcome, reason, json.dumps(report)))
                db.execute('INSERT INTO free_match_attempts(project_id,function_id,strategy,outcome,reason,created_at) VALUES(?,?,?,?,?,?)',
                           (proj['id'], fn['id'], 'iterative-repair', outcome, reason, time.time()))
            return report

        def variants(source, report):
            guided = address_repairs.variants(source, report, fn, bool(proj['big_endian']), known)
            if report.get('candidate_target'):
                compiled = dict(target=bytes.fromhex(report['candidate_target']), relocs=report.get('candidate_relocs', '[]'))
                guided += similar_matches.literal_variants(source, compiled, fn, bool(proj['big_endian']))
            from . import draft_repairs, switch_tables
            targeted = targeted_variants(source, report, fn, proj['arch'], settings.get('flags', []), header, switch_tables.read(net,proj,fn), draft_repairs.capture_followup_repairs.callee_evidence(net,proj,fn,source), tagged=True)
            families = list(dict.fromkeys(family for family, _ in targeted))
            ordered = adaptive_repairs.order(net, proj, fn, report.get('rows') or [], families)
            targeted.sort(key=lambda item: ordered.index(item[0]))
            # Interleave pools so address proposals cannot starve targeted families (or vice versa).
            learned = expression_recipes.variants(net, recipe_profile, source)
            pools = [[('address_or_literal', s) for s in guided[:4]], targeted[:8],
                     [('similar_repair', s) for s in similar_matches.repair_variants(source, report)[:4]],
                     [('learned_expression', s) for s in learned[:2]]]
            return list(dict.fromkeys(item for group in zip_longest(*pools)
                                      for item in group if item is not None))

        before_counts = Counter(state.get('counts', {}))
        if advance(state, evaluate, variants, stopped, arch=proj['arch']):
            with guard:
                matched[0] += 1
                job.reuse_matched += 1
                job.reuse_bytes += fn['size']
            net.add_note(agent, slug, 'Autopilot matched with iterative local repairs', fn['name'])
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_frontiers VALUES(?,?,?)', (fn['id'], context, json.dumps(state)))
        with guard:
            reasons[state.get('reason', 'busy_or_paused')] += 1
            diagnostics.update(Counter(state.get('counts', {}))-before_counts)

    # One thread to a function at a time (Settings: "Iterative repairs: threads"), on a pool of their own: this
    # runs on one of the shared workers, which would wait for the very threads it is waiting on.
    threads = workers.clamp(getattr(getattr(net, 'cascades', None), 'iterative_threads', 0))
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
    with net.store.tx() as db:
        db.execute('INSERT INTO iterative_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                   (proj['id'], phase.reuse_attempts, phase.reuse_matched, phase.reuse_bytes,
                    time.monotonic() - started, time.time()))
    agent, _ = net.local_agent('autopilot')
    net.add_note(agent, slug, f'Iterative search: {matched} matches, {phase.reuse_bytes} bytes, '
                 f'{phase.reuse_attempts} attempts (separate budget).')
    return matched
