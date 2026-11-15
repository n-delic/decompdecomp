"""Purpose-owned worker groups with cooperative cancellation and live thread identity.

A group (Executor) is one operation's queue and width; the threads it runs on are borrowed from thread_lanes and
outlive it, so an operation no longer starts and ends threads of its own (see thread_lanes for why).
"""
import collections
import contextvars
import os
import threading
import time
import uuid
import weakref
from concurrent.futures import Executor as _BaseExecutor
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar

from .. import compiler
from ..network import NetworkError
from . import thread_lanes

_OWNER = ContextVar('purpose_work_owner', default=None)
_CHECK = ContextVar('purpose_work_check', default=None)
_LOCK = threading.Lock()
GROUP_IDLE_SECONDS = 5  # an idle group keeps its threads this long for its next work, then returns them
EXIT_CHECK_SECONDS = .5  # an idle group notices the interpreter's exit this soon
_ACTIVE = {}


@contextmanager
def owner(project, stop, check=None):
    token = _OWNER.set((project or '', stop, check))
    try:yield
    finally:_OWNER.reset(token)


class Executor(_BaseExecutor):
    """One purpose's work, at most `max_workers` at a time on the shared lane's threads; Stop cancels queued work
    and compiler children. Used like a ThreadPoolExecutor (submit, map, shutdown, `with`)."""
    def __init__(self, max_workers=None, *, purpose, project='', stop=None, always_run=False, stop_note='',
                 thread_name_prefix='', initializer=None, initargs=()):
        if max_workers is None:max_workers = min(32, (os.cpu_count() or 1)+4)  # ThreadPoolExecutor's default
        if max_workers <= 0:raise ValueError('max_workers must be greater than 0')
        self._max_workers = max_workers
        inherited = _OWNER.get()
        self.project = project or (inherited[0] if inherited else '')
        self.stop_owner = stop or (inherited[1] if inherited else None)
        self.check_owner = inherited[2] if inherited else None
        self.stop_note = stop_note
        self.purpose = purpose
        self.key = uuid.uuid4().hex
        self.cancelled = threading.Event()
        self.always_run = always_run
        self.pending = 0
        self.thread_ids = set()
        self.futures = set()
        self._thread_name_prefix = thread_name_prefix or purpose.lower().replace(' ', '-')
        self._lane = thread_lanes.lane(initializer, initargs)
        lock = threading.Lock()
        self._state = threading.Condition(lock)  # the queue and the counts below
        self._work_ready = threading.Condition(lock)  # an idle runner takes new work
        self._finished = threading.Condition(lock)  # a runner returned its thread
        self._queue = collections.deque()
        self._runners = 0
        self._idle_runners = 0
        self._runner_threads = set()
        self._free_numbers = []  # thread name numbers, reused as a ThreadPoolExecutor's names are
        self._next_number = 0
        self._shutdown = False

    def _enqueue(self, fn):
        future = Future()
        with self._state:
            if self._shutdown:raise RuntimeError('cannot schedule new futures after shutdown')
            self._queue.append((future, fn))
            # An idle runner first, as a ThreadPoolExecutor reuses an idle thread: a group's work stays on at
            # most `max_workers` threads, which per-thread state (a proposal worker process each) relies on.
            start = self._idle_runners < len(self._queue) and self._runners < self._max_workers
            if start:self._runners += 1
            else:self._work_ready.notify()
        if start:
            try:self._lane.dispatch(self._run_queue)
            except BaseException:
                with self._state:
                    self._runners -= 1
                    orphaned = not self._runners and (future, fn) in self._queue
                    if orphaned:self._queue.remove((future, fn))
                    self._finished.notify_all()
                if orphaned:raise
        return future

    def _next(self):
        """The next queued work, waiting a while for some, or None when this runner should return its thread."""
        with self._state:
            deadline = None
            while not self._queue:
                if self._shutdown or thread_lanes.exiting():return None
                now = time.monotonic()
                deadline = deadline or now + GROUP_IDLE_SECONDS
                if now >= deadline:return None
                self._idle_runners += 1
                try:self._work_ready.wait(min(deadline - now, EXIT_CHECK_SECONDS))
                finally:self._idle_runners -= 1
            return self._queue.popleft()

    def _run_queue(self):
        """One of this group's `max_workers` runners: work through the queue on a borrowed thread, then return it."""
        me = threading.current_thread()
        with self._state:
            number = min(self._free_numbers) if self._free_numbers else self._next_number
            if self._free_numbers:self._free_numbers.remove(number)
            else:self._next_number += 1
            self._runner_threads.add(me)
        me.name = f'{self._thread_name_prefix}_{number}'
        try:
            while (item := self._next()) is not None:
                future, fn = item
                if not future.set_running_or_notify_cancel():continue
                background = getattr(compiler._SCHEDULING, 'background', False)
                try:
                    # An empty context, as a new thread's: nothing one operation set leaks into the next.
                    result = contextvars.Context().run(fn)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
                finally:
                    compiler._SCHEDULING.background = background
                    del future, fn, item
        finally:
            with self._state:
                self._runners -= 1
                self._runner_threads.discard(me)
                self._free_numbers.append(number)
                self._finished.notify_all()

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._state:
            self._shutdown = True
            cancelled = [future for future, _ in self._queue] if cancel_futures else []
            if cancel_futures:self._queue.clear()
            self._work_ready.notify_all()
        for future in cancelled:future.cancel()
        if wait:
            with self._state:
                # From one of its own tasks a group waits for its other runners only (it can't wait for itself).
                own = 1 if threading.current_thread() in self._runner_threads else 0
                while self._runners > own:self._finished.wait()

    def check_cancel(self):
        if self.check_owner:self.check_owner()
        if self.cancelled.is_set():raise NetworkError(self.purpose+' stopped.')

    def stop(self, *, propagate=True):
        self.cancelled.set()
        if propagate and self.stop_owner:self.stop_owner()
        with _LOCK:futures = list(self.futures)
        if not self.always_run:
            for future in futures:future.cancel()

    def submit(self, fn, /, *args, **kwargs):
        with _LOCK:
            self.pending += 1
            _ACTIVE[self.key] = self
        def run():
            tid = threading.get_native_id()
            with _LOCK:self.thread_ids.add(tid)
            token = _CHECK.set(self.check_cancel)
            try:
                with owner(self.project, self.stop, self.check_cancel), compiler.cancellable(self.check_cancel):
                    if not self.always_run:self.check_cancel()
                    result = fn(*args, **kwargs)
                    self.check_cancel()
                    return result
            finally:
                _CHECK.reset(token)
                with _LOCK:self.thread_ids.discard(tid)
        def done(future):
            with _LOCK:
                self.futures.discard(future)
                self.pending -= 1
                if not self.pending:_ACTIVE.pop(self.key, None)
        try:future = self._enqueue(run)
        except BaseException:
            with _LOCK:
                self.pending -= 1
                if not self.pending:_ACTIVE.pop(self.key, None)
            raise
        with _LOCK:self.futures.add(future)
        future.add_done_callback(done)
        return future


def snapshot():
    with _LOCK:
        return [dict(kind='purpose', key=work.key, title=work.purpose, project=work.project,
                     state='stopping' if work.cancelled.is_set() else 'running',
                     detail=f'{len(work.thread_ids)} working · {work.pending-len(work.thread_ids)} queued'+(' · '+work.stop_note if work.stop_note else ''),
                     stoppable=True, cpu_percent=None, thread_ids=list(work.thread_ids))
                for work in _ACTIVE.values()]


def stop(key):
    with _LOCK:work = _ACTIVE.get(key)
    if work is None:raise NetworkError('This work has already finished. Refresh the list.')
    work.stop()


def start(purpose, project, fn, *args, stop=None):
    """One independent automatic task, releasing its purpose-owned thread on completion."""
    from .workers import lower_priority
    pool = Executor(1, purpose=purpose, project=project, stop=stop, always_run=True, initializer=lower_priority)
    try:future = pool.submit(fn, *args)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    future.add_done_callback(lambda _:pool.shutdown(wait=False))
    return future


def checkpoint():
    check = _CHECK.get()
    if check:check()


def shutdown():
    """Signal active work without changing the user's saved background preferences."""
    with _LOCK:groups = list(_ACTIVE.values())
    for group in groups:group.stop(propagate=False)


class Groups:
    """Lifecycle owner for independent verification operations, never a shared executor."""
    def __init__(self, width):
        self.width = width
        self.groups = weakref.WeakSet()
        self.lock = threading.Lock()
        self.closed = False

    def group(self, purpose, project):
        with self.lock:
            if self.closed:raise NetworkError('Verification is shutting down.')
            work = Executor(self.width, purpose=purpose, project=project)
            self.groups.add(work)
            return work

    def shutdown(self, wait=True):
        with self.lock:
            self.closed = True
            groups = list(self.groups)
        for work in groups:
            work.stop(propagate=False)
            work.shutdown(wait=wait, cancel_futures=True)
