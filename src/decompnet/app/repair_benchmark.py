"""Bounded, multi-step frozen-output replay using the autopilot beam, without adopting game C."""
import hashlib
import time
from collections import Counter
from itertools import zip_longest

from . import allocation_evidence, iterative_repairs, paired_local_repairs, repair_selection


def diagnostics(report, arch=None):
    """Rendered/truncated listings cannot stand in for a complete instruction stream."""
    rows=report.get('rows')
    if rows is None or (isinstance(rows,(list,tuple)) and not rows):
        return {'available':False,'category':'unknown','reason':'No structured instruction rows supplied.'}
    if (not isinstance(rows,(list,tuple)) or any(not isinstance(r,(list,tuple)) or len(r)!=3
            or not all(isinstance(v,str) for v in r) or r[0] not in {' ','<','>','|','o','r','s','i'} for r in rows)):
        raise ValueError('Replay needs complete structured diff rows, not rendered or truncated output.')
    return {'available':True,**allocation_evidence.plan(rows,arch)}


def run(source, baseline_score, evaluate, generate, *, trials, deadline, check_cancel=lambda: None,
        progress=lambda value: None, collect_exact=False, generate_report=False, arch=None, paired_search=True, output_diversity=True, header='', layouts=()):
    """Measure rediscovery effort, including failures; evaluator must supply its exact control verdict."""
    if type(paired_search)!=bool or type(output_diversity)!=bool:raise ValueError('Search ablations must be boolean.')
    started = time.monotonic()
    state = dict(pending=[dict(source=source, depth=0, ceiling=baseline_score)], next=[], seen=[source], explore_plateaus=True, paired_search=paired_search,output_diversity=output_diversity)
    from .layout_frontiers import offer
    for layout in layouts:offer(state,layout,header)
    results = []
    from .search_efficiency import Measurements
    efficiency = Measurements()
    check_cancel()
    baseline = evaluate(source)
    reports = {source:baseline}
    best_measured = [baseline.get('score')]
    baseline_diagnostics=diagnostics(baseline,arch)

    def judge(candidate):
        check_cancel()
        # The baseline is already compiled by replay, and callers pass its frozen report.
        trial_started = time.monotonic()
        report = baseline if candidate == source else evaluate(candidate)
        reports[candidate]=report
        measured_diagnostics=diagnostics(report,arch)
        check_cancel()
        if candidate != source:
            node = state['pending'][0]
            row = {'path': node.get('path', []), 'family': node.get('path', ['baseline'])[-1],
                   'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                   'outcome': report['outcome'], 'score': report.get('score'), 'exact': bool(report.get('matched')),
                   'diagnostics':measured_diagnostics,
                   'elapsed_seconds': time.monotonic()-trial_started}
            from .region_repairs import resolution
            row['instruction_resolution']=resolution(node.get('baseline_rows'),report.get('rows'))
            results.append(row)
            efficiency.observe(row['family'],score=report.get('score'),best=best_measured[0],
                output=report.get('output_fingerprint'),granularity=report.get('output_granularity','allocated_object'),
                scope='frozen-case',exact=bool(report.get('matched')),path=row['path'],
                compiler_invocations=report.get('compiler_invocations'),
                parent_output=reports.get(node.get('parent_source',source),{}).get('output_fingerprint'),
                parent_granularity=reports.get(node.get('parent_source',source),{}).get('output_granularity','allocated_object'))
            if isinstance(report.get('score'),(int,float)) and (best_measured[0] is None or report['score']<best_measured[0]):
                best_measured[0]=report['score']
            progress({'tested': len(results), 'budget': trials, **row})
        return report

    def variants(parent, report):
        pools = {}
        proposals = generate(parent, report) if generate_report else generate(parent)
        proposals = repair_selection.Page(proposals, more=getattr(proposals, 'more',False)) if isinstance(proposals,repair_selection.Page) else list(proposals)
        for family,candidate in proposals:efficiency.generated(family,candidate)
        if isinstance(proposals, repair_selection.Page):
            return proposals  # Already ordered/paged by the live selector; do not erase weighted follow-ups.
        for family, candidate in proposals:
            pools.setdefault(family, []).append((family, candidate))
        return repair_selection.Page([item for row in zip_longest(*pools.values()) for item in row if item is not None],
                                     more=getattr(proposals, 'more', False))

    def stopped():
        check_cancel()
        return len(results) >= trials or time.monotonic() >= deadline
    matched = iterative_repairs.advance(state, judge, variants, stopped, stop_on_match=not collect_exact, arch=arch,header=header)
    reason = ('control_output_recovered' if matched else 'time_budget' if time.monotonic() >= deadline
              else 'trial_budget' if len(results) >= trials and state['pending'] else state['reason'])
    return {'baseline_score': baseline.get('score'), 'baseline_exact': bool(baseline.get('matched')),
            'best_score': min((score for score in [baseline.get('score')]+[r.get('score') for r in results]
                               if isinstance(score, (int,float))), default=None),
            'trials': results, 'summary': dict(Counter(r['outcome'] for r in results)),
            'remaining': len(state['pending']), 'reason': reason, 'elapsed_seconds': time.monotonic()-started,
            'candidate_compiles': len(results),
            'efficiency':efficiency.snapshot(),
            'first_exact_trial': next((i+1 for i, r in enumerate(results) if r['exact']), None),
            'exact_alternatives': len(state.get('exact', [])), 'winning_path': state.get('winning_path'),
            'search_counts': state.get('counts', {}), 'regional_combination_bursts':state.get('regional_bursts',0),
            'productive_followups':state.get('productive_bursts',0), 'allocation_followups':state.get('allocation_bursts',0),
            'sequence_proposals':state.get('sequence_proposals',0), 'sequence_evaluations':state.get('counts',{}).get('sequence_evaluations',0),
            'loop_proposals':state.get('loop_proposals',0),'loop_evaluations':state.get('counts',{}).get('loop_evaluations',0),
            'inert_page_advances':state.get('inert_page_advances',0),
            'no_effect_output_families':sum(n>=2 for n in state.get('output_no_effect',{}).values()),
            'no_effect_families':sum(n>=2 for n in state.get('family_no_effect',{}).values()), 'deferred_pages':len(state.get('deferred',[])), 'paired_proposals':state.get('paired_trials',0), 'paired_evaluations':state.get('counts',{}).get('paired_evaluations',0),
            'search_policy':dict(paired_search=paired_search,output_diversity=output_diversity),
            'pair_coverage':paired_local_repairs.coverage(source,header),
            'residual_diagnosis':baseline_diagnostics}


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
                     check_cancel=check_cancel, collect_exact=True, generate_report=generate_report, arch=case.get('arch'),header=case.get('header',''))
        reports.append({'case': case['scope'], 'source_sha256': hashlib.sha256(case['source'].encode()).hexdigest(), **result})
    from ..claim_stalls import repair_rules
    from .search_efficiency import combine
    return {'cases': reports, 'efficiency':combine([r['efficiency'] for r in reports]),
            'rules_sha256':hashlib.sha256(repr(repair_rules()).encode()).hexdigest(),
            'trial_budget': trials, 'seconds_budget': seconds, 'functions': len(reports),
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
                before_recovered_cases=sum(r['before_exact'] is not None for r in cases),
                after_recovered_cases=sum(r['after_exact'] is not None for r in cases),
                before_candidate_compiles=sum(r['before_compiles'] for r in cases),
                after_candidate_compiles=sum(r['after_compiles'] for r in cases),
                shared_recovery_compile_saving=sum(r['before_exact']-r['after_exact'] for r in cases
                                                  if r['before_exact'] is not None and r['after_exact'] is not None),
                score_improvements=sum(r['after_best'] < r['before_best'] for r in scored),
                score_regressions=sum(r['after_best'] > r['before_best'] for r in scored), newly_recovered=sum(r['before_exact'] is None and r['after_exact'] is not None for r in cases),
                lost_recoveries=sum(r['before_exact'] is not None and r['after_exact'] is None for r in cases),
                note='Equal-budget frozen cases; elapsed times are local observations, not guaranteed speedups.')
