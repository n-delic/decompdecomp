"""Bounded host-capacity context for failed launches, never commands or environment values."""
import errno
import logging
import os
import threading
import time

_lock = threading.Lock()
_failures = {}
_last = {}
INTERVAL = 60


def _process_limits():
    try:
        import resource
        return resource.getrlimit(resource.RLIMIT_NPROC)
    except (ImportError, AttributeError, OSError, ValueError):
        return None, None  # No process-count limit is exposed on this host.


def record(error, lane, *, active_compiles=None, compile_limit=None):
    if error.errno not in (errno.EAGAIN, errno.ENOMEM):return
    lane = lane if lane in ('compiler', 'agent') else 'process'
    key = lane, error.errno
    now = time.monotonic()
    with _lock:
        _failures[key] = _failures.get(key, 0) + 1
        if now - _last.get(key, float('-inf')) < INTERVAL:return
        count = _failures.pop(key)
        _last[key] = now
    soft, hard = _process_limits()
    logging.getLogger('decompdecomp').warning(
        'Process launch capacity failure lane=%s errno=%d pid=%d threads=%d '
        'nproc_soft=%s nproc_hard=%s active_compiles=%s compile_limit=%s failures=%d',
        lane, error.errno, os.getpid(), threading.active_count(), soft, hard,
        active_compiles, compile_limit, count)
