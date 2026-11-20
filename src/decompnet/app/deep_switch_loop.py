"""Budgeted switch and record repairs guided by linked target control flow."""
import hashlib
import time

from decomprepair import switch_loop_repairs

from ..repair_feedback import trial
from . import region_repairs, repair_ranking, search_frontier

MAX_TRIALS = 24
MAX_DEPTH = 6
MAX_PAIRS = 2


def run(search, agent, proj, fn, source, rows, header, best, settings, allowance, deadline):
    """Spend part of the local budget and retain regional wins for a second edit."""
    flags = settings['flags'].split() if isinstance(settings['flags'], str) else settings['flags']
    if proj['arch'] != 'mips' or not settings['toolchain'].startswith('gcc-') or not any(
            flag.startswith('-g') and flag != '-g0' for flag in flags):
        return best, source, 0
    limit = min(MAX_TRIALS, allowance)
    attempts = 0
    queue = [(source, rows, best, ())]
    own_seen = set()
    name = fn['name']
    lane = getattr(search, 'parallel_lane', None)
    while queue and attempts < limit:
        parent, parent_rows, parent_score, path = queue.pop(0)
        if len(path) >= MAX_DEPTH:
            continue
        before = region_repairs.snapshot(parent_rows, 'mips', symbol=name)
        if not before or before['scheme'] != 'control-flow':
            continue
        proposals = switch_loop_repairs.variants(parent, name, header, include_exact_only=True)
        search.log.check_cancel()
        # An exact-only byte-store experiment has two inputs. Try it before the
        # standalone counter, which could otherwise hide its pattern by winning.
        proposals.sort(key=lambda entry: entry[0] != switch_loop_repairs.EXACT)
        alternatives = []
        pending = [(family, candidate, (family,)) for family, candidate in proposals]
        pair_count = 0
        while pending:
            family, candidate, steps = pending.pop(0)
            if attempts >= limit or time.monotonic() >= deadline:
                return best, source, attempts
            if candidate in own_seen or candidate in search.local_seen:
                continue
            if lane is not None and int(hashlib.sha256(candidate.encode()).hexdigest(),16)%lane.count != lane.index:
                continue
            exact_only = family == switch_loop_repairs.EXACT
            own_seen.add(candidate)
            if not exact_only:
                search.local_seen.add(candidate)
            attempts += 1
            search.efficiency.generated(family, candidate)
            search._parent_rows = parent_rows
            try:
                with trial({
                    'kind':'deep local repair', 'family':family, 'lineage':[*path, *steps],
                    'base_source_sha256':hashlib.sha256(parent.encode()).hexdigest(), 'exact_only':exact_only,
                }):
                    score = search._score(agent, proj, fn, candidate, best, None)
            finally:
                del search._parent_rows
            if score == 0:
                search.log(f'Exact match found with {family}.')
                return 0, candidate, attempts
            if score is None:
                continue
            candidate_rows = (search._cached_rows if search._trial_reused else
                              repair_ranking.evidence(search.net,fn['id'],agent.id))
            step = (candidate, candidate_rows, score, path+steps)
            if score < best:
                best, source = score, candidate
                fresh = search.net._function(name,proj['slug'])
                if fresh['status'] != 'open' or fresh['best_source'] != source:
                    return best, source, attempts
                fn = fresh
                queue.insert(0, step)
                break
            if score > parent_score*2:
                continue
            after = region_repairs.snapshot(candidate_rows, 'mips', symbol=name)
            gain = region_repairs.gain(before,after)
            if gain:
                recovered = _recovered(before, after)
                if len(steps) == 1 and pair_count < MAX_PAIRS and recovered:
                    for prior in alternatives:
                        if prior['family'] == family or not prior['recovered'] or recovered & prior['recovered']:
                            continue
                        combined = switch_loop_repairs.combine(parent, prior['step'][0], candidate, family, name, header)
                        if combined and combined not in own_seen:
                            pending.insert(0, ('paired_switch_loop', combined, (prior['family'], family)))
                            pair_count += 1
                            break
                alternatives.append({
                    'step':step, 'score':score, 'regions':after, 'region_gain':gain,
                    'damage':region_repairs.damage(before,after),
                    'family':family, 'recovered':recovered if len(steps) == 1 else set(),
                })
        # These alternatives may repair a block despite a worse global score.
        # Expand a small number of distinct regions, under the same allowance.
        for alternative in search_frontier.regional_choices(alternatives):
            queue.append(alternative['step'])
        queue = queue[:1+search_frontier.REGIONAL_SLOTS]
    return best, source, attempts


def _recovered(before, after):
    return {block for block,loss in before['losses'].items() if loss > after['losses'].get(block,0)}
