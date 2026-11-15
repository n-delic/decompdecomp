"""Bounded recipe reduction on copied judges, with no live adoption callback."""
import hashlib
import time

from . import repair_ablation


def minimize(before, after, evaluate, *, trials=12, seconds=30, check_cancel=lambda:None):
    if type(trials) is not int or not 1<=trials<=128 or type(seconds) is not int or not 1<=seconds<=600:
        raise ValueError('Use 1–128 recipe trials and 1–600 seconds.')
    started = time.monotonic()
    check_cancel()
    checks = []
    def measure(source):
        result = evaluate(source)
        checks.append(result.get('compiler_invocations'))
        return result
    control = measure(after)
    fingerprint = control.get('output_fingerprint')
    if control.get('matched') is not True or not fingerprint:
        return {'available':False,'reason':'Recipe reduction needs an explicit exact verdict and whole allocated-output fingerprint.'}
    def stopped():
        check_cancel()
        return time.monotonic()-started>=seconds
    def preserves(source):
        check_cancel()
        result = measure(source)
        return result.get('matched') is True and result.get('output_fingerprint')==fingerprint
    reduced = repair_ablation.minimize(before,after,preserves,trials=trials,stopped=stopped)
    # The ablator retains original text when no edit is removable; never publish an
    # unverified re-tokenization as a recipe (debug line layout can change code).
    candidate = reduced.pop('source')
    if stopped() or not preserves(candidate):
        candidate = after
        reduced['retained_verified_reduction'] = False
        reduced['retained'] = list(range(reduced['edits']))
        reduced['reverted'] = 0
    else:
        reduced['retained_verified_reduction'] = candidate!=after
    reduced.pop('verdict',None)
    return dict(available=True,**reduced,candidate_checks=len(checks),
                compiler_invocations=sum(checks) if all(type(n) is int and n>=0 for n in checks) else None,
                trial_budget=trials,control_check_budget=2,before_sha256=hashlib.sha256(before.encode()).hexdigest(),source_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                original_sha256=hashlib.sha256(after.encode()).hexdigest(),elapsed_seconds=time.monotonic()-started,
                verdict='Smallest verified recipe found within this edit set and budget, not a global minimum. Exact linked/objdiff verdict and allocated output preserved; no adoption.')
