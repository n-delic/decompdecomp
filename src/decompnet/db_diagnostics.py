"""Why the database is slow, in the log: one warning per cause, so what to optimise is never a guess.

A slow read or a waiting write has four usual causes, and each has its own warning (store.py calls these):
- a long write transaction: other writers, and reads made inside a transaction, queue behind it
  ("Long database write", naming where it began);
- a writer that waited for its turn ("Database write waited", naming the transaction it waited behind, when it was
  this process's);
- a write-ahead log the checkpoint can't empty, because a read keeps older pages alive: every read then searches a
  longer log ("Database log not checkpointed");
- a slow read, with its query plan's shape (full table scans, temporary sorts, automatic indexes) and how long it
  waited for the writer's connection ("Slow database read", store.Store._read).

Never the SQL text, its bindings or table names: either can hold private source or credentials (the same rule as the
slow read warning). Each warning is repeated at most once a minute per place, so a busy minute stays readable.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger('decompdecomp')

LONG_WRITE_SECONDS = 0.5  # a transaction held this long makes every other writer and transactional read wait
WRITE_WAIT_SECONDS = 0.5  # a writer that queued this long for its turn
WAL_CHECK_SECONDS = 30  # how often a commit looks at the write-ahead log
WAL_WARN_BYTES = 32 * 1024 * 1024  # log content not yet copied into the database
REPEAT_SECONDS = 60
_INTERNAL = {'store.py', 'contextlib.py', 'writer_gate.py', 'db_diagnostics.py', 'threading.py'}
_last = {}
_last_lock = threading.Lock()


def caller(depth=2) -> str:
    """The first frame outside the database layer: where a transaction or read was asked for."""
    frame = sys._getframe(depth)
    while frame is not None and Path(frame.f_code.co_filename).name in _INTERNAL:
        frame = frame.f_back
    if frame is None:
        return 'unknown'
    return f'{Path(frame.f_code.co_filename).name}:{frame.f_lineno} ({frame.f_code.co_name})'


def due(kind, key, now=None) -> bool:
    """Whether this warning may be written now: once a minute per kind and place."""
    now = time.monotonic() if now is None else now
    with _last_lock:
        if now - _last.get((kind, key), float('-inf')) < REPEAT_SECONDS:
            return False
        if len(_last) >= 512:
            _last.clear()
        _last[(kind, key)] = now
        return True


def long_write(held, where):
    if held >= LONG_WRITE_SECONDS and due('long_write', where):
        log.warning('Long database write %.3fs at %s: other writers, and reads inside transactions, wait for it',
                    held, where)


def write_waited(waited, where, holder):
    if waited >= WRITE_WAIT_SECONDS and due('write_waited', where):
        log.warning('Database write waited %.3fs at %s, last behind %s', waited, where,
                    holder or 'another process (the permuter, a tool reader or a second app)')


def plan_shape(conn, sql, args) -> str:
    """How SQLite runs a query, as counts only: full scans and temporary sorts are what an index would fix."""
    try:
        rows = conn.execute('EXPLAIN QUERY PLAN ' + sql, args).fetchall()
    except Exception:  # a statement that can't be explained (a PRAGMA) still gets its timing logged
        return 'plan=unavailable'
    details = [str(row[-1]) for row in rows]
    full = sum(1 for d in details if d.startswith('SCAN ') and ' INDEX ' not in d and 'CONSTANT' not in d)
    index = sum(1 for d in details if d.startswith('SCAN ') and ' INDEX ' in d)
    search = sum(1 for d in details if d.startswith('SEARCH '))
    temp = sum(1 for d in details if 'TEMP B-TREE' in d)
    automatic = sum(1 for d in details if 'AUTOMATIC' in d)
    return (f'full_scans={full} index_scans={index} index_searches={search} '
            f'temp_sorts={temp} automatic_indexes={automatic}')


class WalWatch:
    """Looks at the write-ahead log after commits, at most every WAL_CHECK_SECONDS.

    A passive checkpoint never waits for anyone; what it reports says whether the log is being emptied. SQLite tries
    one after commits once the log passes 1,000 pages, so this adds no real work; its value is the numbers: frames
    in the log that it couldn't copy are pages a reader still uses."""

    def __init__(self, path):
        self.path = path
        self.next = 0.

    def check(self, conn, lock):
        now = time.monotonic()
        if now < self.next or not lock.acquire(blocking=False):
            return
        try:
            self.next = now + WAL_CHECK_SECONDS
            if conn.in_transaction:
                return
            busy, frames, copied = conn.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
            page = conn.execute('PRAGMA page_size').fetchone()[0]
        except Exception as error:
            log.info('Database log not inspected: %s', error)
            return
        finally:
            lock.release()
        report_wal(self.path, busy, frames, copied, page)


def report_wal(path, busy, frames, copied, page):
    behind = max(0, frames - copied) * page
    if frames < 0 or frames * page < WAL_WARN_BYTES or behind == 0:
        return
    if due('wal', path):
        log.warning('Database log not checkpointed: %.1f MB in the write-ahead log, %.1f MB still needed by a read '
                    '(checkpoint %s). Every read searches the log; look for slow or long-running reads.',
                    frames * page / 1048576, behind / 1048576, 'busy' if busy else 'blocked by readers')
