"""Opt-in request diagnostics: numeric durations only, never SQL, paths or game data."""
import logging
import os
import time
from contextvars import ContextVar

_current = ContextVar('request_timing', default=None)


def sql_read(seconds):
    current = _current.get()
    if current is not None:
        current['sql'] += seconds
        current['queries'] += 1


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
        [('queue', queue), ('work', built-started), ('sql', metrics['sql']), ('encode', encoded-built)])
    logging.getLogger('decompdecomp').info('UI timing %s queue=%.1fms work=%.1fms sql=%.1fms queries=%d encode=%.1fms',
        label, queue*1000, (built-started)*1000, metrics['sql']*1000, metrics['queries'], (encoded-built)*1000)
    return result
