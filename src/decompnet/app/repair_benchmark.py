"""Bounded, multi-step frozen-output replay using the autopilot beam, without adopting game C."""
import hashlib
import time
from collections import Counter
from itertools import zip_longest

from . import iterative_repairs


def run(source, baseline_score, evaluate, generate, *, trials, deadline, check_cancel=lambda: None,
        progress=lambda value: None, collect_exact=False):
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
        for family, candidate in generate(parent):
            pools.setdefault(family, []).append((family, candidate))
        return [item for row in zip_longest(*pools.values()) for item in row if item is not None]

    def stopped():
        check_cancel()
        return len(results) >= trials or time.monotonic() >= deadline
    matched = iterative_repairs.advance(state, judge, variants, stopped, stop_on_match=not collect_exact)
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
            'search_counts': state.get('counts', {}), 'regional_combination_bursts':state.get('regional_bursts',0)}


def batch(cases, evaluate, generate, *, trials=32, seconds=30, check_cancel=lambda: None):
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
                     lambda s, case=case: generate(case, s), trials=trials, deadline=started+seconds,
                     check_cancel=check_cancel, collect_exact=True)
        reports.append({'case': case['scope'], **result})
    return {'cases': reports, 'functions': len(reports),
            'matched_functions': sum(r['first_exact_trial'] is not None and not r['baseline_exact'] for r in reports),
            'improved_functions': sum(isinstance(r['best_score'], (int,float)) and isinstance(r['baseline_score'], (int,float))
                                      and r['best_score'] < r['baseline_score'] for r in reports),
            'candidate_compiles': sum(r['candidate_compiles'] for r in reports),
            'elapsed_trial_seconds': sum(r['elapsed_seconds'] for r in reports),
            'scope': 'Frozen unresolved inputs; equal per-function budgets; copied evaluator; no live adoption.'}
