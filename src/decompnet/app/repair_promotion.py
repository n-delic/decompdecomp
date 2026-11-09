"""Reviewed, held-out replay gates for priority boosts, never compiler match verdicts."""
import hashlib
import json
import math

from .repair_benchmark import compare

EXPERIMENTAL = frozenset({'value_trace_bundle','verified_unit_helpers','broad_first_difference'})
SCHEMA = 'CREATE TABLE IF NOT EXISTS repair_promotions(profile TEXT,family TEXT,rules TEXT,evidence TEXT,PRIMARY KEY(profile,family))'


def evaluate(training, heldout):
    """Require comparable, disjoint target groups and an exact-throughput win in both splits."""
    groups=[];checks=[]
    for envelope in (training,heldout):
        if not all(isinstance(envelope.get(k),str) and envelope[k] for k in ('profile','family','rules')):
            raise ValueError('Replay gates need a compiler profile, isolated family and repair rules digest.')
        control,treatment=envelope['control'],envelope['treatment']
        case_groups=envelope.get('groups',{})
        if set(case_groups)!={r['case'] for r in control['cases']} or not all(isinstance(v,str) and v for v in case_groups.values()):
            raise ValueError('Every frozen case needs a target group, shared by checkpoints of one target.')
        groups.append(set(case_groups.values()))
        result=compare(control,treatment)
        for report in (control,treatment):
            for row in report['cases']:
                for k in ('baseline_score','best_score','candidate_compiles'):
                    v=row.get(k)
                    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:
                        raise ValueError('Replay scores and compile counts must be finite nonnegative numbers.')
                if row['baseline_score']<=0 or type(row['candidate_compiles'])!=int or row['candidate_compiles']>report['trial_budget']:
                    raise ValueError('Gates require unresolved baselines and compile counts inside the frozen budget.')
                trial=row.get('first_exact_trial')
                if trial is not None and (type(trial)!=int or not 1<=trial<=row['candidate_compiles'] or row['best_score']!=0):
                    raise ValueError('Exact recoveries need a measured candidate trial.')
        old=sum(r['candidate_compiles'] for r in control['cases']);new=sum(r['candidate_compiles'] for r in treatment['cases'])
        cheaper=any(r['before_exact'] is not None and r['after_exact'] is not None and r['after_exact']<r['before_exact'] for r in result['cases'])
        safe=not (result['unscored_cases'] or result['score_regressions'] or result['lost_recoveries'])
        checks.append(dict(qualifies=safe and new<=old and bool(result['newly_recovered'] or cheaper),
                           before_compiles=old,after_compiles=new,comparison=result))
    if any(training[k]!=heldout[k] for k in ('profile','family','rules')):
        raise ValueError('Training and held-out evidence must isolate the same family/profile/rules.')
    if groups[0]&groups[1]:raise ValueError('Training and held-out target groups must be disjoint.')
    return dict(promote=all(c['qualifies'] for c in checks),splits=checks,
                profile=training['profile'],family=training['family'],rules=training['rules'],
                evidence_sha256=hashlib.sha256(json.dumps([training,heldout],sort_keys=True).encode()).hexdigest(),
                note='Caller-supplied frozen compiler reports; review evaluator provenance. Scores alone and timing noise cannot promote a family.')


def install(net, training, heldout):
    """Explicit reviewed import; no automatic agent learning or source adoption."""
    decision=evaluate(training,heldout)
    with net.store.tx() as db:
        db.execute(SCHEMA)
        if decision['promote']:
            db.execute('INSERT OR REPLACE INTO repair_promotions VALUES(?,?,?,?)',
                       (decision['profile'],decision['family'],decision['rules'],json.dumps(decision)))
        else:
            db.execute('DELETE FROM repair_promotions WHERE profile=? AND family=?',(decision['profile'],decision['family']))
    return decision


def approved(net, profile, family):
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE name='repair_promotions'"):return False
    from .iterative_repairs import repair_rules
    rules=hashlib.sha256(repr(repair_rules()).encode()).hexdigest()
    return bool(net.store.one('SELECT 1 FROM repair_promotions WHERE profile=? AND family=? AND rules=?',profile,family,rules))


def cap(net, profile, factors):
    """Experimental families retain exploration but need held-out evidence for adaptive boosts."""
    return {f:min(v,1) if f in EXPERIMENTAL and v>1 and not approved(net,profile,f) else v for f,v in factors.items()}


def main():
    """Source-free review artifact; applying a decision remains an explicit developer action."""
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('training',type=Path)
    parser.add_argument('heldout',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        decision=evaluate(json.loads(args.training.read_text()),json.loads(args.heldout.read_text()))
    except (ValueError,KeyError,TypeError,OSError) as error:
        parser.error(str(error))
    text=json.dumps(decision,indent=2)
    if args.output:args.output.write_text(text+'\n')
    else:print(text)
    return 0 if decision['promote'] else 1


if __name__=='__main__':
    raise SystemExit(main())
