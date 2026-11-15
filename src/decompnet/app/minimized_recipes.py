"""Minimise verified repairs into narrow expression hypotheses; validate independent targets before learning."""
import hashlib
import json

from . import expression_recipes, repair_ablation


def propose(before, after, preserves, *, trials=12, stopped=lambda:False):
    if stopped() or preserves(before) or not preserves(after):
        return {'skipped':'Need distinct failed and output-preserving successful controls.'}
    minimized=repair_ablation.minimize(before,after,preserves,trials=trials,stopped=stopped)
    # A budget cutoff is not proof of minimality. Independently verify the emitted subset.
    if stopped() or not preserves(minimized['source']):
        return {'skipped':'The minimized candidate was not freshly verified.','minimization':minimized}
    recipe=expression_recipes.template(before,minimized['source'])
    return dict(minimization=minimized,recipe=recipe,
                verdict='Verified frozen-output subset; not proven globally minimal, semantic necessity, or held-out transfer.')


def validate(recipe, cases, *, training_group, profile, budget=8, stopped=lambda:False):
    """Each held-out case provides the ordinary exact judge and its immutable target group."""
    if type(budget)!=int or not 1<=budget<=128:raise ValueError('Use 1–128 held-out compiler trials.')
    if not recipe or not training_group:return dict(approved=False,reason='Recipe and training target group required.',trials=[])
    records=[];seen=set();compiles=0
    for case in cases:
        group=case['group']
        if not group or group==training_group or group in seen:
            return dict(approved=False,reason='Use unique targets disjoint from training.',trials=records)
        seen.add(group)
        candidate=expression_recipes.apply(case['source'],recipe)
        if candidate is None:continue
        if stopped() or compiles+2>budget:break
        baseline=case['judge'](case['source']);compiles+=1
        if not isinstance(baseline,dict) or baseline.get('matched') is not False or baseline.get('score') is None:
            return dict(approved=False,reason='Need a scored, currently unmatched held-out baseline.',trials=records,compiles=compiles)
        if stopped():break
        verdict=case['judge'](candidate);compiles+=1
        exact=isinstance(verdict,dict) and verdict.get('matched') is True
        records.append(dict(group=group,matched=exact,baseline_matched=False))
        if not exact:return dict(approved=False,reason='Held-out destination did not match.',trials=records,compiles=compiles)
    return dict(approved=bool(records) and not stopped(),trials=records,compiles=compiles,profile=profile,
                recipe_sha256=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest(),
                reason='Ordinary held-out exact verdicts only; later recipients must still be judged.')


def learn(net, profile, before, minimized, validation):
    """Explicit reviewed import only; an agent explanation or isolated successful rewrite is insufficient."""
    recipe=expression_recipes.template(before,minimized)
    if (not recipe or validation.get('profile')!=profile
            or validation.get('recipe_sha256')!=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
            or not validation.get('approved') or not validation.get('trials') or not all(t.get('matched') is True and t.get('baseline_matched') is False for t in validation['trials'])):
        return False
    expression_recipes.learn(net,profile,before,minimized)
    return True
