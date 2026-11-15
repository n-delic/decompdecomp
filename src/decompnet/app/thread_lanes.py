"""Long-lived worker threads that every purpose group (purpose_work.Executor) borrows, instead of each operation
starting and ending its own.

Each match search, recheck, draft refresh and verification used to make a thread pool and discard it, so the app
started threads all day; each new thread then opened its own database reading connections and warmed its own
caches before it did any work. A lane keeps finished threads waiting for the next operation for IDLE_SECONDS.

A lane never refuses work for want of a thread: with no idle thread it starts one, as a group's own pool did. A
lane with a fixed size could deadlock, because some groups' work waits for a nested group's work (a deep search
waits for its per-function searches); the width of each operation is the group's own limit (Executor.max_workers),
not the lane's. Threads differ only in what their initializer did to them (workers.lower_priority lowers a
thread's priority for good), so there is one lane per initializer.
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading

IDLE_SECONDS = 60  # how long a finished thread waits for more work before it ends

_state = threading.local()
_lanes: dict = {}
_lanes_lock = threading.Lock()
_exiting = False


def priority_lowered() -> bool:
    """Whether workers.lower_priority already ran on this thread."""
    return getattr(_state, 'lowered', False)


def exiting() -> bool:
    """Whether the interpreter is exiting (idle groups then return their threads at once)."""
    return _exiting


def mark_lowered() -> None:
    _state.lowered = True


class Lane:
    """Threads prepared by one initializer, reused across operations, started on demand without a limit."""

    def __init__(self, name, initializer=None, initargs=()):
        self.name = name
        self.initializer = initializer
        self.initargs = tuple(initargs)
        self.lock = threading.Lock()
        self.jobs = queue.SimpleQueue()
        self.idle = 0  # waiting threads not yet promised a job
        self.threads = set()
        self.numbers = itertools.count(1)

    def dispatch(self, job) -> None:
        """Run `job` (a callable that never raises) on an idle thread, else on a new one."""
        with self.lock:
            if _exiting:
                raise RuntimeError('cannot schedule new futures after interpreter shutdown')
            if self.idle:
                self.idle -= 1
                self.jobs.put(job)
                return
            thread = threading.Thread(target=self._work, args=(job,), daemon=True,
                                      name=f'{self.name}-idle_{next(self.numbers)}')
            self.threads.add(thread)
        try:
            thread.start()
        except BaseException:
            with self.lock:
                self.threads.discard(thread)
            raise

    def _work(self, job) -> None:
        me = threading.current_thread()
        idle_name = me.name
        try:
            if self.initializer is not None:
                try:
                    self.initializer(*self.initargs)
                except Exception:  # only a thread's priority: the work runs anyway, as the user asked for it
                    logging.getLogger('decompdecomp').exception('a worker thread could not be prepared')
            while job is not None:
                job()
                me.name = idle_name
                if self.initializer is None and priority_lowered():
                    return  # the work lowered this thread for good: it can't serve work that wasn't lowered
                with self.lock:
                    if _exiting:
                        return
                    self.idle += 1
                try:
                    job = self.jobs.get(timeout=IDLE_SECONDS)
                except queue.Empty:
                    with self.lock:
                        # A job may have been promised to this thread just as it timed out.
                        try:
                            job = self.jobs.get_nowait()
                        except queue.Empty:
                            self.idle -= 1
                            return
        finally:
            with self.lock:
                self.threads.discard(me)

    def stop(self) -> list:
        """Wake every idle thread to end; return the threads, for joining."""
        with self.lock:
            for _ in range(self.idle):
                self.jobs.put(None)
            self.idle = 0
            return list(self.threads)


def lane(initializer=None, initargs=()) -> Lane:
    """The shared lane for threads prepared by `initializer`."""
    key = (initializer, tuple(initargs))
    with _lanes_lock:
        found = _lanes.get(key)
        if found is None:
            name = 'worker' if initializer is None else 'worker-' + getattr(initializer, '__name__', 'prepared')
            found = _lanes[key] = Lane(name.replace('_', '-'), initializer, initargs)
        return found


def thread_count() -> int:
    """Threads the lanes hold now, working or idle (for diagnostics and tests)."""
    with _lanes_lock:
        lanes = list(_lanes.values())
    return sum(len(item.threads) for item in lanes)


def _at_exit() -> None:
    """At interpreter exit, as ThreadPoolExecutor does: no new work, idle threads end, running work finishes."""
    global _exiting
    with _lanes_lock:
        _exiting = True
        lanes = list(_lanes.values())
    for item in lanes:
        for thread in item.stop():
            if thread is not threading.current_thread():
                thread.join()


threading._register_atexit(_at_exit)
