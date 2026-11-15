"""Bounded batching of advisory miss statistics; verified game work is never queued here."""
import functools
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import groupby

from .. import compile_timing

_current = ContextVar('trial_bookkeeping', default=None)


class Batch:
    def __init__(self, maximum=False):
        self.max_bytes = (4 if maximum else 1)*1024*1024
        self.max_statements = 256 if maximum else 64
        self.seconds = 1 if maximum else .5
        self.owner = threading.get_ident()
        self.buffers = []  # Identity-based: test adapters need not be hashable.
        self.started = time.monotonic()

    @property
    def pending(self):
        return [statement for buffer in self.buffers for statement in buffer['pending']]

    @property
    def bytes(self):
        return sum(buffer['bytes'] for buffer in self.buffers)

    def buffer(self, store):
        found=next((buffer for buffer in self.buffers if buffer['store'] is store),None)
        if found is None:
            found={'store':store,'pending':[],'values':{},'bytes':0}
            self.buffers.append(found)
        return found

    def flush(self, store=None):
        for buffer in list(self.buffers):
            if store is not None and buffer['store'] is not store:continue
            if buffer['pending']:
                with compile_timing.phase('history_write'), buffer['store'].tx() as db:
                    for sql, group in groupby(buffer['pending'],key=lambda statement:statement[0]):
                        args=[arguments for _,arguments in group]
                        # Preserve statement ordering and the adapter's SQL semantics.
                        if len(args)>1 and hasattr(db,'executemany'):
                            db.executemany(sql,args)
                        else:
                            for arguments in args:db.execute(sql,arguments)
                compile_timing.add('history_transactions',1)
                compile_timing.add('history_statements',len(buffer['pending']))
            self.buffers.remove(buffer)  # Never replay a committed store if another flush fails.
        if not self.buffers:self.started = time.monotonic()


def current():
    value = _current.get()
    return value if value is not None and value.owner == threading.get_ident() else None


def flush(store=None):
    value = current()
    if value is not None:value.flush(store)


def lookup(store, key):
    value = current()
    if value is None:return None
    buffer=next((buffer for buffer in value.buffers if buffer['store'] is store),None)
    return buffer['values'].get(key) if buffer is not None else None


def submit(store, statements, *, immediate=False, memo=None):
    value = current()
    if value is None:
        with store.tx() as db:
            for sql, args in statements:db.execute(sql, args)
        return
    size = sum(len(sql.encode()) + sum(len(arg.encode()) if isinstance(arg, str) else
               len(arg) if isinstance(arg, bytes) else 8 for arg in args) for sql, args in statements)
    if value.buffers and value.bytes + size > value.max_bytes:value.flush()
    buffer=value.buffer(store)
    buffer['pending'].extend(statements)
    if memo is not None:buffer['values'][memo[0]] = memo[1]
    buffer['bytes'] += size
    if immediate:value.flush(store)
    if value.bytes >= value.max_bytes or sum(len(b['pending']) for b in value.buffers) >= value.max_statements or time.monotonic()-value.started >= value.seconds:value.flush()


@contextmanager
def scope(maximum=False):
    if current() is not None:
        yield;return
    value = Batch(maximum);token = _current.set(value)
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
        log = kwargs.get('log') or (args[5] if len(args)>5 else None)
        maximum = getattr(getattr(log, 'compile_pool', None), 'maximum', False)
        with scope(maximum):return function(*args, **kwargs)
    return run
