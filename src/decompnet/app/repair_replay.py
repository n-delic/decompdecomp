"""Offline regression replay of exported repair examples. No network, database writes or match adoption.

The recorded successful C is the compiler-output control, not a replacement ROM/objdiff target. Output
recovery is a regression result; it never counts as a new verified game match.
"""
import argparse
import hashlib
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from .. import consoles, scoring, toolchains
from ..compiler import compile_source
from ..elfobj import extract_function
from ..project import compiler_config
from ..repair_feedback import validated_provenance
from . import draft_repairs


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def rewrite_evidence(evidence):
    """A final rewrite name is causal evidence only with the corresponding base and candidate hashes."""
    provenance=validated_provenance(evidence.get('provenance'),(evidence.get('after') or {}).get('source_sha256')) or {}
    if provenance.get('kind')=='inherited repair context':
        return {'kind':'inherited','direct':False}
    if provenance.get('kind')!='random rewrite':
        return {'kind':provenance.get('kind','unknown'),'direct':False}
    direct=bool(provenance.get('source_sha256') and provenance.get('base_source_sha256') and
                provenance['base_source_sha256']==(evidence.get('before') or {}).get('source_sha256') and
                evidence.get('comparable_settings'))
    return {'kind':'random rewrite','family':provenance.get('family'),'lineage':provenance.get('lineage',[]),
            'direct':direct,'note':'Replay still required; the final rewrite name alone does not isolate a cause.'}


def replay(example, *, trials=24, seconds=120, measure=None, root=None, check_cancel=lambda:None, progress=lambda value:None, minimize=False, diagnostics=False, ablation_only=False, combinations=False):
    """Replay one frozen case; injectable measurement permits the same runner with a copied project judge."""
    if not 1 <= trials <= 128 or not 1 <= seconds <= 600:
        raise ValueError('Use 1–128 trials and 1–600 seconds per example.')
    if not isinstance(minimize,bool) or not isinstance(diagnostics,bool) or not isinstance(combinations,bool):
        raise ValueError('Replay analysis switches must be booleans.')
    if minimize and combinations:
        raise ValueError('Choose either edit minimization or combination benchmarking for this run.')
    evidence = example['evidence']
    report = {'id':example.get('id'), 'function':evidence['function'], 'trials':[], 'summary':{},
              'rewrite_evidence':rewrite_evidence(evidence)}
    before, after = evidence.get('before') or {}, evidence.get('after') or {}
    settings = after.get('settings') or {}
    for stage in (before, after):
        source, header = stage.get('source'), stage.get('header')
        if not source or header is None or not stage.get('source_sha256') or digest(source)!=stage['source_sha256']:
            return report | {'skipped':'Missing or changed source/header evidence; export full examples.'}
        expected = (stage.get('settings') or {}).get('context_sha256')
        if expected and digest(header) != expected:
            return report | {'skipped':'Captured header hash does not match.'}
    differences = {key for key in set(before.get('settings') or {}) | set(settings)
                   if (before.get('settings') or {}).get(key) != settings.get(key)}
    judge_only = differences == {'scoring_revision'} and before['header'] == after['header']
    if (not evidence.get('comparable_settings') or differences or before['header'] != after['header']) and not judge_only:
        return report | {'skipped':'Compiler/build context changed; isolate that change before source replay.'}
    if judge_only:
        report['context_note'] = ('Captured judge revisions differ. Both sources are recompiled with the same '
                                  'captured compiler and header; this compares compiler output, not historical scores or game matches.')
    profile = settings.get('compiler') or {}
    platform = consoles.get(settings.get('platform'))
    name = evidence['function']
    if measure is None:
        if settings.get('mode') != 'rom':
            return report | {'skipped':'Imported projects need a copied build and diagnostic judge; standalone exports lack their build inputs.'}
        if not profile.get('toolchain') or 'cmd' in profile or not toolchains.installed(profile['toolchain']):
            return report | {'skipped':'The captured pinned compiler must already be installed; replay never downloads tools.'}
        # Only registry compilers and flags are used; exported arbitrary commands/paths cannot run.
        cfg = compiler_config(Path(root or '.'), {'compiler':{'toolchain':profile['toolchain'], 'flags':profile.get('flags',[])}})
        def measure(source):
            result = compile_source(cfg, after['header'], source)
            if not result.ok:
                return {'error':result.log[:500]}
            obj = extract_function(result.obj, name)
            # Relocation fingerprints alone do not include the values of floating constants/strings.
            from .compiler_diagnostics import allocated_output
            data = allocated_output(result.obj)
            return {'object':obj,'data':data,'raw_obj':result.obj} if obj else {'error':'Compiled object does not define the captured function.'}
    started = time.monotonic()
    deadline = started+seconds
    check_cancel()
    baseline = measure(before['source'])
    check_cancel()
    control = measure(after['source'])
    check_cancel()
    if baseline.get('error') or control.get('error'):
        return report | {'skipped':'Baseline/control compile failed.', 'baseline':baseline.get('error'), 'control':control.get('error')}
    target = control['object']
    baseline_score = scoring.diff(target, baseline['object']).score
    report.update(baseline_score=baseline_score, verdict='compiler-output regression only; no linked/objdiff match adoption')
    if target.fingerprint() == baseline['object'].fingerprint() and baseline.get('data') == control.get('data'):
        return report | {'skipped':'Failed and successful sources produce the same normalized control output; no isolated regression.'}
    if diagnostics and time.monotonic()<deadline:
        from .compiler_diagnostics import compare, diagnose
        if baseline.get('raw_obj') and control.get('raw_obj'):
            failed = diagnose(profile,before['header'],before['source'],name,root=root or '.',baseline_obj=baseline['raw_obj'])
            check_cancel()
            successful = (diagnose(profile,after['header'],after['source'],name,root=root or '.',baseline_obj=control['raw_obj'])
                          if time.monotonic()<deadline else {'skipped':'Replay time budget reached.'})
            report['compiler_diagnostics'] = {'before':failed,'after':successful,
                'output_unchanged':bool(failed.get('output_unchanged') and successful.get('output_unchanged')),
                'phases':successful.get('phases',{}),'comparison':compare(failed,successful),'note':'At most two extra compiles comparing failed and successful captures; no source applied.'}
        else:
            report['compiler_diagnostics'] = diagnose(profile,after['header'],after['source'],name,root=root or '.')
        check_cancel()
    counts = Counter()
    if minimize:
        from .repair_ablation import minimize as ablate
        def preserves(source):
            check_cancel()
            measured = measure(source)
            return bool(not measured.get('error') and measured['object'].fingerprint()==target.fingerprint()
                        and measured.get('data')==control.get('data'))
        report['minimization'] = ablate(before['source'],after['source'],preserves,
                                       trials=trials if ablation_only else max(1,trials//2),stopped=lambda:time.monotonic()>=deadline)
        for row in report['minimization']['trials']:
            forward = row.get('direction')=='forward'
            outcome = ('subset_preserved' if row['preserves_output'] else 'subset_failed') if forward else ('edit_reverted' if row['preserves_output'] else 'edit_retained')
            report['trials'].append({'family':'captured_edit_subset' if forward else 'revert_captured_edit','outcome':outcome,**row})
            counts[outcome] += 1
    if ablation_only:
        return report | {'summary':dict(counts),'remaining':0}
    if combinations and not minimize:
        from . import repair_benchmark
        def evaluate(source):
            measured = baseline if source == before['source'] else measure(source)
            if measured.get('error'):
                return {'score': None, 'matched': False, 'outcome': 'compile_error'}
            obj = measured['object']
            diff = scoring.diff(target, obj)
            exact = obj.fingerprint() == target.fingerprint() and measured.get('data') == control.get('data')
            return {'score': diff.score, 'rows': diff.rows, 'matched': exact,
                    'candidate_target': repr((obj.fingerprint(), measured.get('data'))),
                    'outcome': 'control_output_recovered' if exact else 'improved' if diff.score < baseline_score
                    else 'neutral' if diff.score == baseline_score else 'regressed'}
        def generate(source):
            labels = {}
            candidates = draft_repairs.variants(source, name, platform.arch, evidence.get('target_asm') or '',
                                                profile.get('flags', []), labels, limit=None, header=after['header'])
            return [(labels.get(c, 'source_shape'), c) for c in candidates]
        result = repair_benchmark.run(before['source'], baseline_score, evaluate, generate, trials=trials,
                                      deadline=deadline, check_cancel=check_cancel, progress=progress)
        return report | result | {'control_compiles': 2, 'search_mode': 'bounded_combinations',
                                  'elapsed_seconds': time.monotonic()-started}
    labels = {}
    candidates = draft_repairs.variants(before['source'],name,platform.arch,evidence.get('target_asm') or '',
                                        profile.get('flags',[]),labels,limit=None,header=after['header'])
    # Round-robin families so a long first family cannot consume the whole replay allowance.
    pools = {}
    for candidate in candidates:
        pools.setdefault(labels.get(candidate,'source_shape'),[]).append(candidate)
    while any(pools.values()) and len(report['trials']) < trials and time.monotonic() < deadline:
        for family, pool in pools.items():
            if not pool or len(report['trials']) >= trials or time.monotonic() >= deadline:
                continue
            source = pool.pop(0)
            check_cancel()
            measured = measure(source)
            check_cancel()
            row = {'family':family,'source_sha256':digest(source)}
            if measured.get('error'):
                row.update(outcome='compile_error',diagnostic=measured['error'])
            else:
                obj = measured['object']
                score = scoring.diff(target,obj).score
                outcome = ('control_output_recovered' if obj.fingerprint()==target.fingerprint() and measured.get('data')==control.get('data') else
                           'improved' if score < baseline_score else 'neutral' if score==baseline_score else 'regressed')
                row.update(outcome=outcome,score=score)
            counts[row['outcome']] += 1
            report['trials'].append(row)
            progress({'tested':len(report['trials']),'budget':trials,'family':family,'outcome':row['outcome']})
    report.update(summary=dict(counts), remaining=sum(map(len,pools.values())),
                  elapsed_seconds=time.monotonic()-started, search_mode='single_step', control_compiles=2,
                  candidate_compiles=sum(counts[k] for k in ('compile_error','control_output_recovered','improved','neutral','regressed')),
                  first_exact_trial=next((i+1 for i,r in enumerate(report['trials']) if r['outcome']=='control_output_recovered'),None))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path,help='Full private repair-example export from the app')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ids',type=int,nargs='+')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--trials',type=int,default=24)
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--minimize',action='store_true',help='Use up to half the trial budget to revert captured edits while preserving the full allocated control output.')
    parser.add_argument('--diagnostics',action='store_true',help='Opt in to two additional native GCC runs and a bounded phase summary; no tool downloads.')
    parser.add_argument('--combinations',action='store_true',help='Benchmark bounded multi-step repairs, stopping at control-output recovery; reports compiles, path and elapsed time.')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Use --limit 1–100.')
    data = json.loads(args.evidence.read_text())
    examples = data.get('examples',[data])
    selected = [e for e in examples if not args.ids or e['id'] in args.ids][:args.limit]
    results = []
    with tempfile.TemporaryDirectory(prefix='decomp-repair-replay-') as root:
        for case in selected:
            try:
                results.append(replay(case,trials=args.trials,seconds=args.seconds,root=root,minimize=args.minimize,diagnostics=args.diagnostics,combinations=args.combinations))
            except (ValueError,KeyError,OSError,toolchains.ToolchainError) as error:
                results.append({'id':case.get('id'),'skipped':str(error)[:500]})
    args.output.write_text(json.dumps({'schema_version':1,'examples':results},indent=2)+'\n')
    print(f'Replayed {len(results)} examples; results saved to {args.output}. No game progress changed.')


if __name__ == '__main__':
    main()
