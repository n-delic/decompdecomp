"""Bounded numeric compiler timings; no source, paths or headers are collected."""
import functools
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .compile_sessions import current

_lock = threading.Lock()
_totals = {}
_collector = ContextVar('compile_timing_collector', default=None)
_active = ContextVar('compile_timing_active', default=frozenset())
ORIGIN = ContextVar('compile_origin', default=None)  # 'ticket' or 'batch' inside the pool's compile workers
RUN_ORIGINS = ('runs_ticket', 'runs_batch', 'runs_direct_local', 'runs_direct_random', 'runs_direct_draft',
               'runs_direct_untagged', 'runs_direct_other')


def run_origin():
    """What asked for this compiler run, as one of RUN_ORIGINS: a queued lookahead ticket, a batch of them, or a compile
    made in the search thread itself, named by the kind of trial it belongs to (no trial: a baseline, a preflight, a
    diagnostic or a recheck)."""
    if (origin := ORIGIN.get()) is not None:
        return 'runs_' + origin
    from .repair_feedback import current_trial
    kind = (current_trial() or {}).get('kind')
    return {'deep local repair': 'runs_direct_local', 'random rewrite': 'runs_direct_random',
            'deterministic draft repair': 'runs_direct_draft', None: 'runs_direct_untagged'}.get(kind, 'runs_direct_other')


def add(name, value):
    if collector := _collector.get():collector.add(name, value)
    if session := current():
        session.metrics[name] = session.metrics.get(name, 0) + value
    with _lock:
        _totals[name] = _totals.get(name, 0) + value


def snapshot():
    with _lock:return dict(_totals)


@contextmanager
def phase(name):
    start = time.monotonic()
    try:yield
    finally:add(name, time.monotonic() - start)


@contextmanager
def collect(collector, *, append=False):
    previous = _collector.get()
    if append and previous is not None:
        class Both:
            def add(self, name, value):
                previous.add(name, value)
                collector.add(name, value)
        active = Both()
    else:active = collector
    token = _collector.set(active)
    try:yield
    finally:_collector.reset(token)


class Counts:
    """A per-trial numeric collector; append it to retain outer throughput metrics."""
    def __init__(self):self.values = {}

    def add(self, name, value):self.values[name] = self.values.get(name, 0) + value


def timed(name, *, unless=()):
    """Measure inclusive work once when a generator recursively invokes the same stage."""
    def decorate(function):
        @functools.wraps(function)
        def run(*args, **kwargs):
            if name in _active.get() or _active.get().intersection(unless):
                return function(*args, **kwargs)
            token = _active.set(_active.get() | {name})
            try:
                with phase(name):
                    return function(*args, **kwargs)
            finally:
                _active.reset(token)
        return run
    return decorate
