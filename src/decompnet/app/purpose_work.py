"""Purpose-owned worker groups with cooperative cancellation and live thread identity."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar

from .. import compiler
from ..network import NetworkError

_OWNER = ContextVar('purpose_work_owner', default=None)
_CHECK = ContextVar('purpose_work_check', default=None)
_LOCK = threading.Lock()
_ACTIVE = {}


@contextmanager
def owner(project, stop):
    token = _OWNER.set((project or '', stop))
    try:yield
    finally:_OWNER.reset(token)


class Executor(ThreadPoolExecutor):
    """A separate pool for one purpose; Stop cancels queued work and compiler children."""
    def __init__(self, max_workers=None, *, purpose, project='', stop=None, always_run=False, **kwargs):
        inherited = _OWNER.get()
        self.project = project or (inherited[0] if inherited else '')
        self.stop_owner = stop or (inherited[1] if inherited else None)
        self.purpose = purpose
        self.key = uuid.uuid4().hex
        self.cancelled = threading.Event()
        self.always_run = always_run
        self.pending = 0
        self.thread_ids = set()
        self.futures = set()
        kwargs.setdefault('thread_name_prefix', purpose.lower().replace(' ', '-'))
        super().__init__(max_workers, **kwargs)

    def check_cancel(self):
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
                with owner(self.project, self.stop), compiler.cancellable(self.check_cancel):
                    if not self.always_run:self.check_cancel()
                    return fn(*args, **kwargs)
            finally:
                _CHECK.reset(token)
                with _LOCK:self.thread_ids.discard(tid)
        def done(future):
            with _LOCK:
                self.futures.discard(future)
                self.pending -= 1
                if not self.pending:_ACTIVE.pop(self.key, None)
        try:future = super().submit(run)
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
                     detail=f'{len(work.thread_ids)} working · {work.pending-len(work.thread_ids)} queued',
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
