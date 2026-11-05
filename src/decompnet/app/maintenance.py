"""One heavy maintenance lane, with bounded cooperative yields to foreground interaction."""
import threading
import time
from contextlib import contextmanager

_lane = threading.RLock()
_local = threading.local()
_last_ui = float('-inf')


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
    _local.active = True
    try:
        yield True
    finally:
        _local.active = previous
        _lane.release()


def checkpoint():
    """Only maintenance threads yield; foreground callers and write transactions are not delayed."""
    stop = getattr(_local, 'cancel', None)
    if stop is not None and stop.is_set():raise Cancelled("Startup task stopped")
    if not getattr(_local, 'active', False):
        return
    now = time.monotonic()
    if now - getattr(_local, 'yielded', float('-inf')) < .05:
        return
    # Continuous UI polling must not starve saving/reuse indefinitely.
    deadline = now + .2
    while time.monotonic() < deadline and time.monotonic() - _last_ui < .15:
        time.sleep(.01)
    time.sleep(.01 if stop is not None else .001)
    _local.yielded = time.monotonic()
