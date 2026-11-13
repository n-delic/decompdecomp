"""Native recursive project locks with bounded, redacted diagnostics for long waits.

The monitor observes waits; it never acquires the project lock, changes its deadline,
or retains frames, project names, request arguments or source content.
"""
import itertools
import logging
import os
import threading
import time
import weakref
from dataclasses import dataclass, replace

from .view_snapshots import _owner_stack

WAIT_SECONDS = 5
LOG_SECONDS = 60
MAX_WAITERS = 256
_numbers = itertools.count(1)
_PHASES = frozenset(('Reading saved files', 'Comparing saved files', 'Saving verified progress',
                     'Preparing saved verification', 'Compiling saved functions', 'Waiting for an active draft',
                     'Verifying saved functions', 'Reading saved function records', 'Compiling changed saved inputs',
                     'Verification paused'))


@dataclass
class Owner:
    thread: int
    native_thread: int
    since: float
    depth: int = 1
    phase: str = 'project work'


@dataclass
class _Wait:
    lock: weakref.ReferenceType
    thread: int
    native_thread: int
    since: float
    due: float


class _Monitor:
    """One sleeping observer per process, not one timer/thread per blocked call."""
    def __init__(self):
        self.condition = threading.Condition()
        self.waiters = {}
        self.worker = None
        self.start_error_at = float('-inf')

    def add(self, lock):
        now = time.monotonic()
        waiter = _Wait(weakref.ref(lock), threading.get_ident(), threading.get_native_id(), now, now+WAIT_SECONDS)
        key = object()
        with self.condition:
            if len(self.waiters) >= MAX_WAITERS:return None
            if self.worker is None and now-self.start_error_at < LOG_SECONDS:return None
            self.waiters[key] = waiter
            if self.worker is None:
                worker = threading.Thread(target=self.run, name='project-lock-diagnostics', daemon=True)
                try:worker.start()
                except (RuntimeError, OSError) as error:
                    # Optional diagnostics must not replace a native lock acquisition
                    # when the host cannot create another thread. Report that failure.
                    self.waiters.pop(key)
                    if now-self.start_error_at >= LOG_SECONDS:
                        self.start_error_at = now
                        logging.getLogger('decompdecomp').warning(
                            'Project lock diagnostic monitor unavailable: %s', type(error).__name__)
                    return None
                self.worker = worker
            self.condition.notify()
        return key

    def remove(self, key):
        with self.condition:
            self.waiters.pop(key, None)
            self.condition.notify()

    def due(self, now):
        """Called under condition; return work without holding it while logging."""
        ready = []
        for key, waiter in self.waiters.items():
            if waiter.due <= now:
                waiter.due = now+LOG_SECONDS
                ready.append((key, waiter))
        return ready

    def report(self, key, waiter, now):
        with self.condition:
            if key not in self.waiters:return
            lock = waiter.lock()
            sample = lock.sample(waiter, now) if lock is not None else None
        if sample is not None:
            logging.getLogger('decompdecomp').warning(
                'Project lock wait pid=%d lock=%d wait=%.3fs waiter=%d waiter_native=%d '
                'owner=%d owner_native=%d held=%.3fs depth=%d phase=%s owner_stack=%s waiter_stack=%s', *sample)

    def run(self):
        while True:
            with self.condition:
                now = time.monotonic()
                ready = self.due(now)
                if not ready:
                    delay = max(0, min(w.due for w in self.waiters.values())-now) if self.waiters else None
                    self.condition.wait(delay)
                    continue
            for key, waiter in ready:self.report(key, waiter, now)


_monitor = _Monitor()


class ProjectLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._metadata = threading.Lock()
        self._owner = None
        self.number = next(_numbers)
        self._logged_at = float('-inf')

    def owner(self):
        with self._metadata:return replace(self._owner) if self._owner is not None else None

    def set_phase(self, phase):
        # UI progress can contain function names. Store only known constant labels,
        # and never let a waiter or a saved-judge child relabel another thread's lock.
        if type(phase) is not str or phase not in _PHASES:return
        with self._metadata:
            if self._owner is not None and self._owner.thread == threading.get_ident():
                self._owner.phase = phase

    def _acquired(self, depth=1, phase='project work'):
        ident = threading.get_ident()
        with self._metadata:
            if self._owner is not None and self._owner.thread == ident:
                self._owner.depth += depth
            else:
                self._owner = Owner(ident, threading.get_native_id(), time.monotonic(), depth, phase)

    def acquire(self, blocking=True, timeout=-1):
        # Avoid starting the observer or touching its registry for uncontended and
        # recursive context-manager entries. Explicit API arguments still go through
        # the native implementation's own validation and timeout semantics.
        if blocking is False or self._lock._is_owned():
            acquired = self._lock.acquire(blocking, timeout)
            if acquired:self._acquired()
            return acquired
        if blocking is True and type(timeout) in (int, float) and timeout == -1:
            if self._lock.acquire(False):
                self._acquired()
                return True
        key = _monitor.add(self)
        try:
            acquired = self._lock.acquire(blocking, timeout)
            if acquired:self._acquired()
            return acquired
        finally:_monitor.remove(key)

    def release(self):
        with self._metadata:
            self._lock.release()  # Invalid/non-owner releases must not change metadata.
            self._owner.depth -= 1
            if not self._owner.depth:self._owner = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()

    def _is_owned(self):
        return self._lock._is_owned()

    def _release_save(self):
        # Condition.wait releases *all* recursive acquisitions. Keep its opaque
        # native token intact; no assumption about the token's platform layout.
        with self._metadata:
            state = self._lock._release_save()
            owner, self._owner = self._owner, None
            return state, owner.depth, owner.phase

    def _acquire_restore(self, saved):
        state, depth, phase = saved
        key = _monitor.add(self)
        try:
            self._lock._acquire_restore(state)
            self._acquired(depth, phase)
        finally:_monitor.remove(key)

    def sample(self, waiter, now):
        with self._metadata:
            owner = replace(self._owner) if self._owner is not None else None
            if owner is None or owner.thread == waiter.thread or now-self._logged_at < LOG_SECONDS:return
            self._logged_at = now
            # Capture while release/ownership transfer is fenced by metadata: the
            # frame names must describe this owner, not its work after unlocking.
            return (os.getpid(), self.number, max(0, now-waiter.since), waiter.thread, waiter.native_thread,
                    owner.thread, owner.native_thread, max(0, now-owner.since), owner.depth, owner.phase,
                    _owner_stack(owner.thread), _owner_stack(waiter.thread))
