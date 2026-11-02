"""Bounded, multi-step frozen-output replay using the autopilot beam, without adopting game C."""
import hashlib
import time
from collections import Counter
from itertools import zip_longest

from . import iterative_repairs, repair_selection


def run(source, baseline_score, evaluate, generate, *, trials, deadline, check_cancel=lambda: None,
        progress=lambda value: None, collect_exact=False, generate_report=False, arch=None):
    """Measure rediscovery effort, including failures; evaluator must supply its exact control verdict."""
    started = time.monotonic()
    state = dict(pending=[dict(source=source, depth=0, ceiling=baseline_score)], next=[], seen=[source], explore_plateaus=True)
    results = []
    check_cancel()
    baseline = evaluate(source)

    def judge(candidate):
        check_cancel()
        # The baseline is already compiled by replay, and callers pass its frozen report.
        trial_started = time.monotonic()
        report = baseline if candidate == source else evaluate(candidate)
        check_cancel()
        if candidate != source:
            node = state['pending'][0]
            row = {'path': node.get('path', []), 'family': node.get('path', ['baseline'])[-1],
                   'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                   'outcome': report['outcome'], 'score': report.get('score'), 'exact': bool(report.get('matched')),
                   'elapsed_seconds': time.monotonic()-trial_started}
            results.append(row)
            progress({'tested': len(results), 'budget': trials, **row})
        return report

    def variants(parent, report):
        pools = {}
        proposals = generate(parent, report) if generate_report else generate(parent)
        if isinstance(proposals, repair_selection.Page):
            return proposals  # Already ordered/paged by the live selector; do not erase weighted follow-ups.
        for family, candidate in proposals:
            pools.setdefault(family, []).append((family, candidate))
        return repair_selection.Page([item for row in zip_longest(*pools.values()) for item in row if item is not None],
                                     more=getattr(proposals, 'more', False))

    def stopped():
        check_cancel()
        return len(results) >= trials or time.monotonic() >= deadline
    matched = iterative_repairs.advance(state, judge, variants, stopped, stop_on_match=not collect_exact, arch=arch)
    reason = ('control_output_recovered' if matched else 'time_budget' if time.monotonic() >= deadline
              else 'trial_budget' if len(results) >= trials and state['pending'] else state['reason'])
    return {'baseline_score': baseline.get('score'), 'baseline_exact': bool(baseline.get('matched')),
            'best_score': min((score for score in [baseline.get('score')]+[r.get('score') for r in results]
                               if isinstance(score, (int,float))), default=None),
            'trials': results, 'summary': dict(Counter(r['outcome'] for r in results)),
            'remaining': len(state['pending']), 'reason': reason, 'elapsed_seconds': time.monotonic()-started,
            'candidate_compiles': len(results),
            'first_exact_trial': next((i+1 for i, r in enumerate(results) if r['exact']), None),
            'exact_alternatives': len(state.get('exact', [])), 'winning_path': state.get('winning_path'),
            'search_counts': state.get('counts', {}), 'regional_combination_bursts':state.get('regional_bursts',0),
            'productive_followups':state.get('productive_bursts',0), 'allocation_followups':state.get('allocation_bursts',0),
            'no_effect_families':sum(n>=2 for n in state.get('family_no_effect',{}).values()), 'deferred_pages':len(state.get('deferred',[]))}


def batch(cases, evaluate, generate, *, trials=32, seconds=30, check_cancel=lambda: None, generate_report=False):
    """Benchmark immutable unresolved cases with equal budgets and no adoption callback.

    The caller must supply a copied build/engine; never point evaluators at a live project.
    Cases carry source, score and a frozen input scope. Reports omit source and function names.
    """
    reports = []
    for case in cases:
        check_cancel()
        if case['baseline_score'] <= 0:
            raise ValueError('Benchmark cases must be unresolved with a positive baseline score.')
        started = time.monotonic()
        result = run(case['source'], case['baseline_score'], lambda s, case=case: evaluate(case, s),
                     lambda s, report=None, case=case: generate(case, s, report) if generate_report else generate(case, s),
                     trials=trials, deadline=started+seconds,
                     check_cancel=check_cancel, collect_exact=True, generate_report=generate_report, arch=case.get('arch'))
        reports.append({'case': case['scope'], 'source_sha256': hashlib.sha256(case['source'].encode()).hexdigest(), **result})
    return {'cases': reports, 'trial_budget': trials, 'seconds_budget': seconds, 'functions': len(reports),
            'matched_functions': sum(r['first_exact_trial'] is not None and not r['baseline_exact'] for r in reports),
            'improved_functions': sum(isinstance(r['best_score'], (int,float)) and isinstance(r['baseline_score'], (int,float))
                                      and r['best_score'] < r['baseline_score'] for r in reports),
            'candidate_compiles': sum(r['candidate_compiles'] for r in reports),
            'elapsed_trial_seconds': sum(r['elapsed_seconds'] for r in reports),
            'scope': 'Frozen unresolved inputs; equal per-function budgets; copied evaluator; no live adoption.'}


def compare(before, after):
    """Compare equal-budget frozen case sets, including failures; timings are observations only."""
    if any(before.get(key) != after.get(key) or before.get(key) is None
           for key in ('trial_budget', 'seconds_budget')):
        raise ValueError('Compare runs with the same recorded trial and time budgets.')
    left = {r['case']: r for r in before['cases']}
    right = {r['case']: r for r in after['cases']}
    if len(left) != len(before['cases']) or len(right) != len(after['cases']) or left.keys() != right.keys():
        raise ValueError('Compare the same unique frozen cases.')
    cases = []
    for key, old in left.items():
        new = right[key]
        if (not old.get('source_sha256') or old['source_sha256'] != new.get('source_sha256')
                or old['baseline_score'] != new['baseline_score']):
            raise ValueError('Starting source and baseline must agree for every frozen case.')
        cases.append(dict(case=key, before_exact=old['first_exact_trial'], after_exact=new['first_exact_trial'],
                          before_best=old['best_score'], after_best=new['best_score'],
                          before_compiles=old['candidate_compiles'], after_compiles=new['candidate_compiles'],
                          before_seconds=old['elapsed_seconds'], after_seconds=new['elapsed_seconds']))
    scored = [r for r in cases if isinstance(r['before_best'], (int, float)) and isinstance(r['after_best'], (int, float))]
    return dict(cases=cases, unscored_cases=len(cases)-len(scored),
                score_improvements=sum(r['after_best'] < r['before_best'] for r in scored),
                score_regressions=sum(r['after_best'] > r['before_best'] for r in scored), newly_recovered=sum(r['before_exact'] is None and r['after_exact'] is not None for r in cases),
                lost_recoveries=sum(r['before_exact'] is not None and r['after_exact'] is None for r in cases),
                note='Equal-budget frozen cases; elapsed times are local observations, not guaranteed speedups.')
