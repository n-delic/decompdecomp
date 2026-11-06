"""Bounded batching of advisory miss statistics; verified game work is never queued here."""
import functools
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('trial_bookkeeping', default=None)


class Batch:
    def __init__(self):
        self.owner = threading.get_ident()
        self.store = None
        self.pending = []
        self.bytes = 0
        self.values = {}
        self.started = time.monotonic()

    def flush(self):
        if not self.pending:return
        with self.store.tx() as db:
            for sql, args in self.pending:db.execute(sql, args)
        self.pending.clear()
        self.values.clear()
        self.bytes = 0
        self.started = time.monotonic()


def current():
    value = _current.get()
    return value if value is not None and value.owner == threading.get_ident() else None


def flush(store=None):
    value = current()
    if value is not None and (store is None or value.store is store):value.flush()


def lookup(store, key):
    value = current()
    return value.values.get(key) if value is not None and value.store is store else None


def submit(store, statements, *, immediate=False, memo=None):
    value = current()
    if value is None:
        with store.tx() as db:
            for sql, args in statements:db.execute(sql, args)
        return
    if value.store is not store:
        value.flush();value.store = store
    size = sum(len(sql.encode()) + sum(len(arg.encode()) if isinstance(arg, str) else
               len(arg) if isinstance(arg, bytes) else 8 for arg in args) for sql, args in statements)
    if value.pending and value.bytes + size > 1024 * 1024:value.flush()
    value.pending.extend(statements)
    if memo is not None:value.values[memo[0]] = memo[1]
    value.bytes += size
    if immediate or value.bytes >= 1024 * 1024 or len(value.pending) >= 64 or time.monotonic()-value.started >= .5:value.flush()


@contextmanager
def scope():
    if current() is not None:
        yield;return
    value = Batch();token = _current.set(value)
    try:
        yield
    except BaseException:
        try:value.flush()
        except Exception:
            logging.getLogger('decompdecomp').exception('Could not flush advisory trial statistics while ending a search')
        raise
    else:value.flush()
    finally:_current.reset(token)


def buffered(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        with scope():return function(*args, **kwargs)
    return run
