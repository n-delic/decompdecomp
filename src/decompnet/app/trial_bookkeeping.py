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
        # Advisory counters only (verified game work is never queued here), and a crash loses at most this window of
        # them. Every flush is one transaction through the project's single writer, which a 12-lane search kept
        # busy half the time (10,642 transactions of 18.7 ms in 6 minutes): memory is cheaper than writer time.
        self.max_bytes = (64 if maximum else 8)*1024*1024
        self.max_statements = 8192 if maximum else 512
        self.seconds = 10 if maximum else 2
        self.owner = threading.get_ident()
        self.buffers = []  # Identity-based: test adapters need not be hashable.
        self.started = time.monotonic()

    @property
    def pending(self):
        return [statement for buffer in self.buffers
                for statement in (*buffer['pending'], *((sql, args) for (sql, _), args in buffer['counters'].items()))]

    @property
    def bytes(self):
        return sum(buffer['bytes'] for buffer in self.buffers)

    def buffer(self, store):
        found=next((buffer for buffer in self.buffers if buffer['store'] is store),None)
        if found is None:
            found={'store':store,'pending':[],'values':{},'bytes':0,'counters':{}}
            self.buffers.append(found)
        return found

    def flush(self, store=None):
        for buffer in list(self.buffers):
            if store is not None and buffer['store'] is not store:continue
            statements=[*buffer['pending'],*((sql,args) for (sql,_),args in buffer['counters'].items())]
            if statements:
                with compile_timing.phase('history_write'), buffer['store'].tx() as db:
                    for sql, group in groupby(statements,key=lambda statement:statement[0]):
                        args=[arguments for _,arguments in group]
                        # Preserve statement ordering and the adapter's SQL semantics.
                        if len(args)>1 and hasattr(db,'executemany'):
                            db.executemany(sql,args)
                        else:
                            for arguments in args:db.execute(sql,arguments)
                compile_timing.add('history_transactions',1)
                compile_timing.add('history_statements',len(statements))
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
def count(store, sql, args, key, merge, *, immediate=False):
    """A counter upsert (attempted+N and the like): within a batch, one with the same SQL and key (`key` leading
    arguments) is merged into the pending one by `merge(old, new)`, so a batch writes one row per key rather than
    one per trial. The deep search wrote three counter rows for every candidate through the single writer."""
    value = current()
    if value is None:
        submit(store, [(sql, args)], immediate=immediate)
        return
    buffer = value.buffer(store)
    slot = (sql, tuple(args[:key]))
    old = buffer['counters'].get(slot)
    buffer['counters'][slot] = tuple(args) if old is None else tuple(merge(old, args))
    if old is None:
        buffer['bytes'] += len(sql.encode()) + 64*len(args)
    if immediate:value.flush(store)
    elif (value.bytes >= value.max_bytes or len(value.pending) >= value.max_statements
          or time.monotonic()-value.started >= value.seconds):value.flush()


def sums(old, new, key, *, first=(), last=(), newest=None):
    """merge for count(): add every column after the key, except `first` (MIN), `last` (MAX) and the columns
    taken from whichever row is newer by column `newest`."""
    out = list(old)
    newer = newest is not None and new[newest] >= old[newest]
    for index in range(key, len(new)):
        if index in first:out[index] = min(old[index], new[index])
        elif index in last:out[index] = max(old[index], new[index])
        elif newest is not None and not isinstance(new[index], (int, float)):
            out[index] = new[index] if newer else old[index]
        elif index == newest:out[index] = max(old[index], new[index])
        else:out[index] = old[index] + new[index]
    return out


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
