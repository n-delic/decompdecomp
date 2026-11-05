"""Opt-in request diagnostics: numeric durations only, never SQL, paths or game data."""
import functools
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('request_timing', default=None)


def sql_read(seconds, phases=None):
    current = _current.get()
    if current is not None:
        current['sql'] += seconds
        current['queries'] += 1
        for name, value in (phases or {}).items():
            current['sql_' + name] = current.get('sql_' + name, 0.) + value


def response(work, queued_at, encode, label):
    """Run AND encode in the request worker, keeping large JSON/gzip off the event loop."""
    if os.environ.get('DECOMP_PERF') != '1':
        return encode(work())
    started = time.monotonic()
    metrics = {'sql': 0., 'queries': 0}
    token = _current.set(metrics)
    try:
        data = work()
        built = time.monotonic()
        result = encode(data)
        encoded = time.monotonic()
    finally:
        _current.reset(token)
    queue = max(0, started - queued_at)
    result.headers['Server-Timing'] = ', '.join(f'{key};dur={value*1000:.3f}' for key, value in
        [('queue', queue), ('work', built-started), ('sql', metrics['sql']), ('encode', encoded-built)] + [(k, metrics.get(k, 0.)) for k in ('sql_connection', 'sql_lock_wait', 'sql_execute', 'sql_fetch')])
    logging.getLogger('decompdecomp').info('UI timing %s queue=%.1fms work=%.1fms sql=%.1fms queries=%d encode=%.1fms',
        label, queue*1000, (built-started)*1000, metrics['sql']*1000, metrics['queries'], (encoded-built)*1000)
    return result


@contextmanager
def capture():
    metrics={'sql':0.,'queries':0}
    token=_current.set(metrics)
    try:
        yield metrics
    finally:
        _current.reset(token)


def build(function):
    @functools.wraps(function)
    def run(*args,**kwargs):
        began=time.monotonic()
        try:
            return function(*args,**kwargs)
        finally:
            metrics=_current.get()
            if metrics is not None:
                metrics['build']=metrics.get('build',0)+time.monotonic()-began
    return run


@contextmanager
def timed_lock(lock, key):
    """Measure acquisition only, retaining the lock's ordinary context/exception behavior."""
    metrics=_current.get()
    if metrics is None:
        with lock:
            yield
        return
    began=time.monotonic()
    with lock:
        metrics[key]=metrics.get(key,0.)+max(0.,time.monotonic()-began)
        yield
