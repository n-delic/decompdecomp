"""One candidate's reusable read-only parsing/fingerprints, isolated to its worker thread."""
import functools
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO

_current = ContextVar('candidate_work', default=None)


class Work:
    def __init__(self):
        self.owner = threading.get_ident()
        self.memo = {}
        self.objects = {}
        self.object_bytes = 0


def current():
    value = _current.get()
    return value if value is not None and value.owner == threading.get_ident() else None


@contextmanager
def scope():
    if current() is not None:
        yield;return
    token = _current.set(Work())
    try:yield
    finally:_current.reset(token)


def trial(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        with scope():return function(*args, **kwargs)
    return run


def elf(data, factory):
    value = current()
    if value is None:return factory(BytesIO(data))
    snapshot = bytes(data)
    key = (factory, snapshot)
    if key in value.objects:return value.objects[key]
    result = factory(BytesIO(snapshot))
    # The original and a C++ name-normalized object can coexist, never unbounded payloads.
    if len(value.objects) < 4 and value.object_bytes + len(data) <= 16 * 1024 * 1024:
        value.objects[key] = result
        value.object_bytes += len(data)
    return result


def remember(key, build):
    value = current()
    if value is None:return build()
    if key not in value.memo:
        result = build()
        if len(value.memo) < 32:value.memo[key] = result
        return result
    return value.memo[key]
