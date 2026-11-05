"""One heavy maintenance lane, with bounded cooperative yields to foreground interaction."""
import threading
import time
from contextlib import contextmanager

_lane = threading.RLock()
_local = threading.local()
_last_ui = float('-inf')
_cpu_limit = 0


def configure_cpu(value):
    """0 is unlimited; 100 is one CPU core, measured with this thread's CPU clock."""
    global _cpu_limit
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("Enter a whole CPU percentage from 1 to 100, or 0 for unlimited.")
    _cpu_limit = value


def _pace_cpu():
    if not _cpu_limit:
        _local.cpu_sample = None
        return
    sample = getattr(_local, 'cpu_sample', None)
    now, cpu = time.monotonic(), time.thread_time()
    if sample is None or sample[2] != _cpu_limit:
        _local.cpu_sample = (now, cpu, _cpu_limit)
        return
    wall_start, cpu_start, limit = sample
    # The lane serializes these tasks, so a thread budget is also the shared maintenance
    # budget. Count CPU only: disk waits must not earn an unbounded future CPU burst.
    debt = (cpu - cpu_start) * 100 / limit - (now - wall_start)
    while debt > .001 and _cpu_limit == limit:
        stop = getattr(_local, 'cancel', None) or getattr(_local, 'pacing_stop', None)
        if stop is not None and stop.is_set():
            raise Cancelled("Startup task stopped")
        time.sleep(min(debt, .05))
        now = time.monotonic()
        debt = (time.thread_time() - cpu_start) * 100 / limit - (now - wall_start)
    _local.cpu_sample = (time.monotonic(), time.thread_time(), _cpu_limit)


class Cancelled(RuntimeError):
    """A maintenance task stops only at a cooperative safe checkpoint."""


@contextmanager
def cancellation(stop):
    previous = getattr(_local, 'cancel', None)
    _local.cancel = stop
    try:
        yield
    finally:
        _local.cancel = previous


def touch():
    global _last_ui
    _last_ui = time.monotonic()


@contextmanager
def slot(stop=None):
    acquired = False
    while not acquired and (stop is None or not stop.is_set()):
        acquired = _lane.acquire(timeout=.1)
    if not acquired:
        yield False
        return
    previous = getattr(_local, 'active', False)
    previous_stop = getattr(_local, 'pacing_stop', None)
    _local.pacing_stop = stop or previous_stop
    _local.active = True
    if not previous:
        _local.cpu_sample = (time.monotonic(), time.thread_time(), _cpu_limit)
    try:
        yield True
    finally:
        _local.active = previous
        _local.pacing_stop = previous_stop
        _lane.release()


def checkpoint():
    """Only maintenance threads yield; foreground callers and write transactions are not delayed."""
    stop = getattr(_local, 'cancel', None)
    if stop is not None and stop.is_set():raise Cancelled("Startup task stopped")
    if not getattr(_local, 'active', False):
        return
    _pace_cpu()
    now = time.monotonic()
    if now - getattr(_local, 'yielded', float('-inf')) < .05:
        return
    # Continuous UI polling must not starve saving/reuse indefinitely.
    deadline = now + .2
    while time.monotonic() < deadline and time.monotonic() - _last_ui < .15:
        time.sleep(.01)
    time.sleep(.01 if stop is not None else .001)
    _local.yielded = time.monotonic()
