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
_turn = ContextVar('turn_bookkeeping', default=None)
_FLUSHES = [0]  # transactions this process has committed through a Batch: rows read before one are stale after it
_STORED = {}
_STORED_LOCK = threading.Lock()
STORED_SECONDS = 1.0


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
    def statement_count(self):
        return sum(len(buffer['pending']) + len(buffer['counters']) for buffer in self.buffers)

    @property
    def bytes(self):
        return sum(buffer['bytes'] for buffer in self.buffers)

    def buffer(self, store):
        found=next((buffer for buffer in self.buffers if buffer['store'] is store),None)
        if found is None:
            found={'store':store,'pending':[],'values':{},'bytes':0,'counters':{},'cleanup':{}}
            self.buffers.append(found)
        return found

    def flush(self, store=None, *, final=None):
        for buffer in list(self.buffers):
            if store is not None and buffer['store'] is not store:continue
            statements = [*buffer['pending'], *((sql, args) for (sql, _), args in buffer['counters'].items()),
                          *buffer['cleanup']]
            if statements or final is not None:
                with compile_timing.phase('history_write'), buffer['store'].tx() as db:
                    for sql, group in groupby(statements,key=lambda statement:statement[0]):
                        args=[arguments for _,arguments in group]
                        # Preserve statement ordering and the adapter's SQL semantics.
                        if len(args)>1 and hasattr(db,'executemany'):
                            db.executemany(sql,args)
                        else:
                            for arguments in args:db.execute(sql,arguments)
                    if final is not None:
                        sql, rows, _ = final
                        db.executemany(sql, rows)
                compile_timing.add('history_transactions',1)
                _FLUSHES[0] += 1
                compile_timing.add('history_statements', len(statements) + (final[2] if final else 0))
            self.buffers.remove(buffer)  # Never replay a committed store if another flush fails.
        if not self.buffers:self.started = time.monotonic()


class Turn:
    """Advisory evidence shared by a function's paths until the owner finishes."""

    def __init__(self):
        self.batch = Batch()
        self.lock = threading.Lock()
        self.reads = {}


def current_turn():
    return _turn.get()


@contextmanager
def for_turn(turn):
    token = _turn.set(turn)
    try:
        yield
    finally:
        _turn.reset(token)


def submit_final(store, statements):
    if not statements:
        return
    turn = current_turn()
    if turn is None:
        submit(store, statements, immediate=True)
        return
    with turn.lock:
        turn.batch.buffer(store)['pending'].extend(statements)


def cleanup_final(store, sql, args):
    turn = current_turn()
    with turn.lock:
        turn.batch.buffer(store)['cleanup'][(sql, args)] = None


def current():
    value = _current.get()
    return value if value is not None and value.owner == threading.get_ident() else None


def flush(store=None):
    value = current()
    if value is not None:value.flush(store)


def flush_with(store, sql, rows, count):
    """Stream a function's completed miss records with the store's advisory evidence."""
    turn = current_turn()
    batch = turn.batch if turn is not None else current() or Batch()
    batch.buffer(store)
    batch.flush(store, final=(sql, rows, count))

def lookup(store, key):
    value = current()
    if value is None:return None
    buffer=next((buffer for buffer in value.buffers if buffer['store'] is store),None)
    return buffer['values'].get(key) if buffer is not None else None


def stored(store, key, load):
    """`load()`'s rows for `key`, read at most once a STORED_SECONDS and again after any flush of this process: the
    policy tables are read before every batch of candidates, and each read was a query (a quarter of a core of a
    sampled permuter worker) for rows that change by a few counts a second. Another process's writes show up within
    a second; this process's own are added by pending_counters until they are flushed, and a flush drops the copy.
    Deep searches keep this advisory snapshot for the function and overlay all of its paths’ counters."""
    turn = current_turn()
    if turn is not None:
        slot = (id(store), key)
        with turn.lock:
            if slot in turn.reads:
                return turn.reads[slot]
        rows = load()
        with turn.lock:
            return turn.reads.setdefault(slot, rows)
    now = time.monotonic()
    slot = (id(store), key)
    with _STORED_LOCK:
        kept = _STORED.get(slot)
        if kept is not None and kept[0] == _FLUSHES[0] and now - kept[1] < STORED_SECONDS and kept[3] is store:
            return kept[2]
    rows = load()
    with _STORED_LOCK:
        if len(_STORED) >= 256:
            _STORED.clear()
        _STORED[slot] = (_FLUSHES[0], now, rows, store)
    return rows


def written():
    """A transaction outside a Batch changed rows `stored` may be keeping: drop every kept copy."""
    _FLUSHES[0] += 1


def pending_counters(store, sql):
    """The counter rows `count` has queued for `store` under this exact SQL and not yet written: {key arguments: the
    queued arguments}. A reader that needs the totals can add these to the stored rows instead of flushing the batch,
    which is a transaction through the project's single writer each time."""
    turn = current_turn()
    if turn is not None:
        with turn.lock:
            return _pending_counters(turn.batch, store, sql)
    return _pending_counters(current(), store, sql)


def _pending_counters(batch, store, sql):
    buffer = next((b for b in batch.buffers if b['store'] is store), None) if batch is not None else None
    if buffer is None:
        return {}
    return {slot[1]: args for slot, args in buffer['counters'].items() if slot[0] == sql}


def submit(store, statements, *, immediate=False, memo=None):
    value = current()
    if value is None:
        with store.tx() as db:
            for sql, args in statements:db.execute(sql, args)
        _FLUSHES[0] += 1
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


def count(store, sql, args, key, merge=None, *, immediate=False, memo=None):
    """A counter upsert (attempted+N and the like): within a batch, one with the same SQL and key (`key` leading
    arguments) is merged into the pending one by `merge(old, new)`, so a batch writes one row per key rather than
    one per trial. The deep search wrote three counter rows for every candidate through the single writer."""
    turn = current_turn()
    if turn is not None:
        with turn.lock:
            _count(turn.batch, store, sql, args, key, merge, memo)
        return
    value = current()
    if value is None:
        submit(store, [(sql, args)], immediate=immediate)
        return
    _count(value, store, sql, args, key, merge, memo)
    if immediate:value.flush(store)
    elif (value.bytes >= value.max_bytes or value.statement_count >= value.max_statements
          or time.monotonic()-value.started >= value.seconds):value.flush()


def _count(value, store, sql, args, key, merge, memo):
    buffer = value.buffer(store)
    slot = (sql, tuple(args[:key]))
    old = buffer['counters'].get(slot)
    updated = tuple(merge(old, args)) if old is not None and merge is not None else tuple(args)
    buffer['counters'][slot] = updated
    if memo is not None:
        buffer['values'][memo[0]] = memo[1]
        # Replacement records can carry large feedback. Include it in the same
        # byte limit as ordinary queued writes, even when a key is reused.
        buffer['bytes'] += sum(len(arg.encode()) for arg in updated if isinstance(arg, str))
        if old is not None:
            buffer['bytes'] -= sum(len(arg.encode()) for arg in old if isinstance(arg, str))
    if old is None:
        buffer['bytes'] += len(sql.encode()) + 64*len(args)


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
        turn = getattr(log, 'advisory_turn', None) or current_turn()
        with for_turn(turn), scope(maximum):
            return function(*args, **kwargs)
    return run
