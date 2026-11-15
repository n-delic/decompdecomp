"""Independent similar-source budget, shared across an entire automatic reuse wave.

Forward progress, cancellation and ordinary cross-game counters to the owning job;
keep this phase's limits separate so earlier searches cannot starve it.
"""
import time

from . import free_candidates, similar_matches


class Budget:
    def __init__(self, parent, seconds, attempts):
        object.__setattr__(self, '_parent', parent)
        object.__setattr__(self, '_local', {})
        free_candidates.budget(self, seconds, attempts)
        self._local.update(cross_started=time.monotonic(), cross_seconds=seconds)

    def __getattr__(self, name):
        if name.startswith('reuse_') or name in ('cross_started', 'cross_seconds'):
            try:
                return self._local[name]
            except KeyError:
                raise AttributeError(name) from None
        return getattr(self._parent, name)

    def __setattr__(self, name, value):
        if name.startswith('reuse_'):
            self._local[name] = value
        else:
            if name == 'progress':
                value.update(attempts=self.reuse_attempts, attempt_limit=self.reuse_limit,
                             confirmed=self.reuse_matched, second_limit=self.cross_seconds)
            setattr(self._parent, name, value)


def run(twins, slug, job):
    phase = getattr(job, '_similar_budget', None)
    if phase is None:
        phase = Budget(job, similar_matches.MAX_SECONDS, similar_matches.MAX_ATTEMPTS)
        job._similar_budget = phase
    try:
        return similar_matches.run(twins, slug, phase)
    finally:
        job.similar_attempts = phase.reuse_attempts
        job.similar_matched = phase.reuse_matched
        job.similar_bytes = phase.reuse_bytes


def totals(job):
    return tuple(getattr(job, a, 0) + getattr(job, b, 0) for a, b in
                 [('reuse_attempts', 'similar_attempts'), ('reuse_matched', 'similar_matched'),
                  ('reuse_bytes', 'similar_bytes')])
