"""Coalesce only simultaneous identical compilation work, never judge or publish results.

Objects kept across requests and processes are compile_cache.py's, looked up by the builder. Owners keep their
normal cancellation/artifact semantics and followers receive independent result objects.
"""
import copy
import threading
import time
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from contextvars import ContextVar

_lock = threading.Lock()
_pending = {}
SHARED_WAIT_SECONDS = 300


def wait(future, check=None):
    """A sharing follower never waits forever or waits for its own compilation."""
    if future.builder_thread == threading.get_ident():
        raise RuntimeError('A shared compilation cannot wait for its own builder.')
    deadline=time.monotonic()+SHARED_WAIT_SECONDS
    while True:
        if check:check()
        try:return copy.deepcopy(future.result(timeout=min(.05,max(0,deadline-time.monotonic()))))
        except TimeoutError:
            if future.done():raise
            if time.monotonic()>=deadline:
                raise TimeoutError('A shared compilation is still running. Retry the compilation.') from None


def run(key, build):
    with _lock:
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
            future.builder_thread = threading.get_ident()
    if not owner:
        return wait(future)
    try:
        result = build()
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)


class Group:
    """Bounded successful object sharing for one explicitly owned search, never verdicts."""
    def __init__(self):
        from collections import OrderedDict
        self.lock = threading.Lock()
        self.pending = {}
        self.completed = OrderedDict()
        self.bytes = 0

    def run(self, key, build, check):
        with self.lock:
            cached = self.completed.get(key)
            if cached is not None:
                self.completed.move_to_end(key)
                from .compile_timing import add
                add('shared_cache_hits', 1)
                return copy.deepcopy(cached)
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = self.pending[key] = Future()
                future.builder_thread = threading.get_ident()
        if not owner:
            from .compile_timing import add
            add('shared_waits', 1)
            try:return wait(future,check)
            except CancelledError:
                if check:check()
                return self.run(key, build, check)
        try:
            result = build()
            size = len(result.obj or b'') + len(result.log.encode())
            with self.lock:
                if result.ok and size <= 4*1024*1024:
                    self.completed[key] = copy.deepcopy(result)
                    self.bytes += size
                    while len(self.completed)>128 or self.bytes>32*1024*1024:
                        _, old = self.completed.popitem(last=False)
                        self.bytes -= len(old.obj or b'')+len(old.log.encode())
            future.set_result(result)
            return result
        except BaseException as error:
            with self.lock:
                if self.pending.get(key) is future:self.pending.pop(key, None)
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                if self.pending.get(key) is future:self.pending.pop(key, None)


_group = ContextVar('compile_group', default=None)


def current():return _group.get()


@contextmanager
def group(value):
    token = _group.set(value)
    try:yield
    finally:_group.reset(token)
