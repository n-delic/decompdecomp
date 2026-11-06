"""SQLite lock acquisition must not pin the local writer mutex behind another process."""
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.store import Store


def test_external_writer_does_not_pin_local_mutex_for_whole_busy_timeout(tmp_path):
    store = Store(tmp_path / 'app.db')
    blocker = sqlite3.connect(store.path, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    store.conn.execute('PRAGMA busy_timeout=2000')
    entered = threading.Event()
    store.conn.set_trace_callback(lambda sql: entered.set() if sql == 'BEGIN IMMEDIATE' else None)
    bodies, callbacks = [], []

    def write():
        with store.tx() as db:
            bodies.append(1)
            db.execute("INSERT INTO settings VALUES('test','saved')")
            store.after_commit(lambda: callbacks.append(1))

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(write)
        try:
            assert entered.wait(3)
            acquired = store.lock.acquire(timeout=.6)
            if acquired:
                store.lock.release()
            assert acquired, 'External SQLite contention pinned the process-local writer mutex'
        finally:
            blocker.execute('ROLLBACK')
            blocker.close()
        future.result(timeout=4)
    assert bodies == callbacks == [1]
    assert store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 2000
    assert store.one("SELECT value FROM settings WHERE key='test'")['value'] == 'saved'


def test_acquisition_timeout_does_not_enter_body_or_leave_a_transaction(tmp_path):
    store = Store(tmp_path / 'app.db')
    blocker = sqlite3.connect(store.path, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    store.conn.execute('PRAGMA busy_timeout=150')
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            with store.tx():
                pytest.fail('Busy acquisition entered the transaction body')
        assert time.monotonic() - started < 1.5, 'Configured acquisition timeout was reset on retries'
        assert not store.conn.in_transaction
        assert store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 150
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    with store.tx() as db:
        db.execute("INSERT INTO settings VALUES('after','ok')")


def test_transaction_body_failure_is_rolled_back_without_replay(tmp_path):
    store = Store(tmp_path / 'app.db')
    bodies, callbacks = [], []
    with pytest.raises(sqlite3.OperationalError, match='body failure'):
        with store.tx() as db:
            bodies.append(1)
            db.execute("INSERT INTO settings VALUES('body','not committed')")
            store.after_commit(lambda: callbacks.append(1))
            failure = sqlite3.OperationalError('body failure')
            failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise failure
    assert bodies == [1] and callbacks == []
    assert store.one("SELECT value FROM settings WHERE key='body'") is None
    assert not store.conn.in_transaction


def test_zero_busy_timeout_remains_immediate_without_body_retries(tmp_path):
    store = Store(tmp_path / 'app.db')
    blocker = sqlite3.connect(store.path, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    store.conn.execute('PRAGMA busy_timeout=0')
    attempts = []
    store.conn.set_trace_callback(lambda sql: attempts.append(sql) if sql == 'BEGIN IMMEDIATE' else None)
    try:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            with store.tx():
                pytest.fail('Busy writer entered body')
        assert attempts == ['BEGIN IMMEDIATE']
        assert store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 0
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()


def test_nested_transactions_keep_read_your_writes_and_one_commit_callback(tmp_path):
    store = Store(tmp_path / 'app.db')
    callbacks = []
    with store.tx() as outer:
        outer.execute("INSERT INTO settings VALUES('nested','pending')")
        with store.tx() as inner:
            assert inner is outer
            assert store.one("SELECT value FROM settings WHERE key='nested'")['value'] == 'pending'
            store.after_commit(lambda: callbacks.append(1))
        assert callbacks == []
    assert callbacks == [1]
