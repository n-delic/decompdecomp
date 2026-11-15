"""Equal-budget recovery from independently frozen source checkpoints, without adoption.

Checkpoints of one function measure recovery effort, not independent new game matches.
The CLI uses the existing read-only exported-ROM linker and never executes build.json commands.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

from .. import compile_timing
from . import iterative_repairs, linked_repair_benchmark, repair_benchmark

TIMINGS = {'proposal_generation':'proposal_generation_seconds', 'cleanup_generation':'cleanup_generation_seconds',
           'execute':'compiler_execute_seconds','queue':'compiler_wait_seconds','setup':'compiler_setup_seconds',
           'link':'link_seconds'}


def run(cases, factory, *, trials=32, seconds=30, generate=None, minimize_trials=0, check_cancel=lambda:None):
    """Give every frozen case fresh evaluator/cache/cursors and identical search budgets."""
    if type(trials) is not int or not 1 <= trials <= 4096 or type(seconds) is not int or not 1 <= seconds <= 1800:
        raise ValueError('Use 1–4096 trials and 1–1800 seconds per checkpoint.')
    if type(minimize_trials) is not int or not 0 <= minimize_trials <= 128:
        raise ValueError('Use 0–128 additional recipe trials.')
    cases = [dict(c) for c in cases]
    ids = [c['id'] for c in cases]
    if not cases or len(set(ids)) != len(ids):
        raise ValueError('Supply distinct checkpoint ids.')
    frozen = [(case, hashlib.sha256(case['source'].encode()).hexdigest()) for case in cases]
    results = []
    for case, digest in frozen:
        check_cancel()
        counts = compile_timing.Counts()
        start = time.monotonic()
        with compile_timing.collect(counts, append=True):
            judge, context = factory(case)
            exact_inventory = {}
            exact_sources = []
            def evaluate(source, judge=judge, exact_inventory=exact_inventory, exact_sources=exact_sources):
                result = judge(source)
                if result.get('matched'):
                    exact_sources.append(source)
                    inventory = result.get('object_emission') or {}
                    exact_inventory.update({k:inventory.get(k) for k in ('available','function_count',
                        'extra_function_count','allocated_data_bytes','executable_bytes')})
                return result
            baseline = evaluate(case['source'])
            if baseline.get('score') is None or baseline.get('matched'):
                raise ValueError('Every checkpoint must compile and be unresolved under the frozen judge.')
            if case.get('expected_score', baseline['score']) != baseline['score']:
                raise ValueError('A checkpoint no longer reproduces its recorded score.')
            baseline_seconds = time.monotonic()-start
            selection, pool = {}, {}
            def proposals(source, report, case=case, context=context, selection=selection, pool=pool):
                if generate is not None:
                    return generate(case, source, report)
                return iterative_repairs.targeted_variants(source,report,
                    {'name':context['name'],'asm':context['asm']},context['arch'],context['flags'],context['header'],
                    tagged=True,selection=selection,pool_cache=pool,entry_scope=context.get('entry_scope'))
            outcome = repair_benchmark.run(case['source'],baseline['score'],evaluate,proposals,
                trials=trials,deadline=time.monotonic()+seconds,generate_report=True,
                check_cancel=check_cancel,arch=context.get('arch'),header=context.get('header',''))
        search_wall_seconds = time.monotonic()-start
        from decomprepair.call_preparation_repairs import explain
        applicability = (explain(case['source'],context['name'],context.get('header',''),context.get('arch'))
                         if context.get('name') else None)
        recipe = None
        if minimize_trials and exact_sources:
            from .repair_recipe import minimize
            recipe = minimize(case['source'],exact_sources[0],judge,trials=minimize_trials,seconds=seconds,check_cancel=check_cancel)
        macro_analysis = context.get('macro_analysis')
        if callable(macro_analysis):
            check_cancel()
            macro_analysis = macro_analysis()
        families = outcome['efficiency'].get('families',{})
        timings = {label:counts.values.get(key,0) for key,label in TIMINGS.items()}
        # Callbacks without compiler instrumentation are unknown, never claimed free.
        invocations = counts.values.get('invocations',0) if 'requests' in counts.values or 'invocations' in counts.values else None
        results.append(dict(id=case['id'],source_sha256=digest,scope=context.get('scope'),
            call_preparation_applicability=applicability,macro_analysis=macro_analysis,recipe_minimisation=recipe,
            starting_score=baseline['score'],best_score=outcome['best_score'],
            recovered=outcome['first_exact_trial'] is not None, first_exact_trial=outcome['first_exact_trial'],
            reason=outcome['reason'],exact_object_inventory=exact_inventory,candidate_checks=outcome['candidate_compiles'],compiler_invocations=invocations,
            baseline_seconds=baseline_seconds,search_seconds=outcome['elapsed_seconds'],
            wall_seconds=search_wall_seconds,timings=timings,winning_path=outcome['winning_path'],
            family_coverage={f:dict(generated=v.get('generated_candidates',0),tested=v.get('candidate_checks',0),
                improvements=v.get('improvements',0)) for f,v in families.items()},
            summary=outcome['summary']))
    from ..claim_stalls import repair_rules
    rules_sha256 = hashlib.sha256(repr(repair_rules()).encode()).hexdigest()
    return dict(checkpoints=results,rules_sha256=rules_sha256,trial_budget=trials,seconds_budget=seconds,
                recovered_checkpoints=sum(r['recovered'] for r in results),checkpoint_count=len(results),
                recipe_trial_budget=minimize_trials,model_tokens=0,scope='Independent frozen checkpoint recovery; no live adoption. Repeated checkpoints are not distinct functions.',
                timing_note='Wall time differs from summed stage/worker work. Baseline setup is separate; compiler invocations include baseline and failed compiles. Unknown instrumentation is null.')


def markdown(report):
    lines = ['# Frozen checkpoint recovery', '',
             f"Equal budget: {report['trial_budget']} candidate checks / {report['seconds_budget']} seconds per checkpoint. No AI tokens.", '',
             '| Checkpoint | Start → best score | Exact recovered | Compiler calls | Wall seconds | Proposal seconds | Compiler seconds |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for row in report['checkpoints']:
        label = str(row['id']).replace('|','/').replace('\n',' ')
        timing = row['timings']
        proposals = timing['proposal_generation_seconds']+timing['cleanup_generation_seconds']
        lines.append(f"| {label} | {row['starting_score']} → {row['best_score']} | {'yes' if row['recovered'] else 'no'} | "
                     f"{row['compiler_invocations'] if row['compiler_invocations'] is not None else 'unknown'} | {row['wall_seconds']:.2f} | "
                     f"{proposals:.2f} | {timing['compiler_execute_seconds']:.2f} |")
    for row in report['checkpoints']:
        eligibility = row.get('call_preparation_applicability')
        if eligibility:
            lines += ['', f"{row['id']} call-preparation eligibility: {eligibility['candidates']} generated; guards: "+(', '.join(eligibility['declined']) or 'none observed')+'. Generated candidates may remain untested within the search budget.']
        if row.get('macro_analysis'):
            analysis = row['macro_analysis']
            lines += ['', 'Macro analysis: '+(analysis.get('note','') if analysis.get('available') else analysis.get('reason','unavailable'))]
        if row.get('recipe_minimisation'):
            recipe = row['recipe_minimisation']
            lines += ['', 'Recipe minimisation: '+(f"{recipe['reverted']} token edits removed; {recipe['candidate_checks']} additional checks (includes control/recheck). "+recipe['verdict'] if recipe.get('available') else recipe.get('reason','unavailable'))]
    lines += ['', report['scope'], '', report['timing_note'], '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--function',required=True)
    parser.add_argument('--checkpoints',required=True,type=Path,help='Private manifest with source/header paths and SHA256 values.')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--markdown',type=Path)
    parser.add_argument('--trials',type=int,default=32)
    parser.add_argument('--seconds',type=int,default=30)
    parser.add_argument('--macro-analysis',action='store_true',help='Opt-in pinned compiler expansion for eligibility analysis only; original source is unchanged.')
    parser.add_argument('--minimize-recipe',type=int,default=0,metavar='TRIALS',help='Additional 1–128 private exact-verified edit checks after recovery; separate from search budget.')
    args = parser.parse_args()
    try:
        manifest = json.loads(args.checkpoints.read_text())
        def read(item):
            raw = (args.checkpoints.parent/item['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Frozen checkpoint/header changed; create a new manifest before replay.')
            return raw.decode('utf-8')
        header = read(manifest['header'])
        cases = [dict(id=c['id'],source=read(c),expected_score=c['expected_score']) for c in manifest['checkpoints']]
        def factory(case):
            evaluate,scope,arch,asm,_ = linked_repair_benchmark.evaluator(args.project,args.function,header)
            profile = scope['compiler']
            from .draft_repairs import promoted_entry_repairs
            macro_analysis = None
            if args.macro_analysis:
                from . import repair_macro_analysis
                def macro_analysis():
                    return repair_macro_analysis.summary(repair_macro_analysis.inspect(profile,header,case['source'],args.function,root=args.project),args.function,arch)
            return evaluate,dict(macro_analysis=macro_analysis,name=args.function,scope=scope,arch=arch,asm=asm,header=header,
                flags=profile['flags'],entry_scope=promoted_entry_repairs.profile_scope(arch,profile))
        result = run(cases,factory,trials=args.trials,seconds=args.seconds,minimize_trials=args.minimize_recipe)
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    if args.markdown:
        args.markdown.write_text(markdown(result))
    print(markdown(result))


if __name__ == '__main__':
    main()
