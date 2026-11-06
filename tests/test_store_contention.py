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


def _process_writer(path,ready,release,crash=False):
    import os
    store=Store(path)
    with store.tx() as db:
        db.execute("INSERT INTO settings VALUES('remote','saved')")
        ready.set()
        release.wait(10)
        if crash:os._exit(0)


def test_process_writers_wait_outside_sqlite_timeout_and_readers_remain_free(tmp_path):
    import multiprocessing
    ctx=multiprocessing.get_context('spawn')
    store=Store(tmp_path/'app.db')
    ready=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=_process_writer,args=(store.path,ready,release))
    child.start()
    bodies=[];callbacks=[]
    try:
        assert ready.wait(8)
        store.conn.execute('PRAGMA busy_timeout=20')
        def write():
            with store.tx() as db:
                bodies.append(1)
                db.execute("INSERT INTO settings VALUES('parent','saved')")
                store.after_commit(lambda:callbacks.append(1))
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(write)
            # Longer than SQLite's configured timeout: admission must wait instead of fail.
            time.sleep(.15)
            assert not future.done() and not bodies
            assert store.lock.acquire(timeout=.1)
            store.lock.release()
            assert store.one("SELECT value FROM settings WHERE key='remote'") is None
            release.set();future.result(timeout=4)
        assert bodies==callbacks==[1]
        assert store.one("SELECT value FROM settings WHERE key='remote'")['value']=='saved'
        assert store.conn.execute('PRAGMA busy_timeout').fetchone()[0]==20
    finally:
        release.set();child.join(5)
        if child.is_alive():child.terminate();child.join(3)


def test_writer_admission_is_cancellable_and_optional_work_never_waits(tmp_path):
    a=Store(tmp_path/'app.db');b=Store(a.path)
    entered=threading.Event();release=threading.Event()
    def hold():
        with a.tx():entered.set();release.wait(3)
    worker=threading.Thread(target=hold);worker.start()
    try:
        assert entered.wait(1)
        started=time.monotonic()
        with b.try_tx() as db:assert db is None
        assert time.monotonic()-started<.1
        def cancel():raise RuntimeError('cancelled waiting writer')
        with pytest.raises(RuntimeError,match='cancelled waiting'):
            with b.tx(wait=cancel):pytest.fail('cancelled admission entered body')
        assert not b.conn.in_transaction
    finally:release.set();worker.join(3)
    with b.tx() as db:db.execute("INSERT INTO settings VALUES('after','saved')")


def test_process_exit_releases_writer_gate_and_rolls_back_uncommitted_work(tmp_path):
    import multiprocessing
    ctx=multiprocessing.get_context('spawn')
    store=Store(tmp_path/'app.db');ready=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=_process_writer,args=(store.path,ready,release,True))
    child.start()
    try:
        assert ready.wait(8)
        release.set();child.join(5)
        assert child.exitcode==0
        with store.tx() as db:db.execute("INSERT INTO settings VALUES('survivor','saved')")
        assert store.one("SELECT value FROM settings WHERE key='remote'") is None
    finally:
        if child.is_alive():child.terminate();child.join(3)


def test_commit_hooks_run_after_writer_admission_is_released(tmp_path):
    first=Store(tmp_path/'app.db');second=Store(first.path);callbacks=[]
    def after():
        with second.try_tx() as db:
            assert db is not None
            db.execute("INSERT INTO settings VALUES('callback','saved')")
        callbacks.append(1)
    with first.tx() as db:
        db.execute("INSERT INTO settings VALUES('outer','saved')")
        first.after_commit(after)
        with first.tx():assert callbacks==[]
    assert callbacks==[1]


def test_commit_failure_rolls_back_and_releases_admission(tmp_path):
    store=Store(tmp_path/'app.db');calls=[]
    # An authorizer can fail COMMIT without altering the executed transaction body.
    def authorize(action,arg,*unused):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_TRANSACTION and arg=='COMMIT' else sqlite3.SQLITE_OK
    store.conn.set_authorizer(authorize)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            with store.tx() as db:
                db.execute("INSERT INTO settings VALUES('failed','discarded')")
                store.after_commit(lambda:calls.append(1))
    finally:store.conn.set_authorizer(None)
    assert not store.conn.in_transaction and not calls
    assert store.one("SELECT value FROM settings WHERE key='failed'") is None
    with store.try_tx() as db:assert db is not None


def test_second_connection_in_same_thread_fails_without_self_deadlock(tmp_path):
    first=Store(tmp_path/'app.db');second=Store(first.path)
    with first.tx():
        with second.try_tx() as db:assert db is None
        with pytest.raises(RuntimeError,match='second connection'):
            with second.tx():pytest.fail('entered recursive independent connection')
    with second.tx() as db:db.execute("INSERT INTO settings VALUES('later','saved')")


def test_windows_writer_region_lock_retries_and_unlocks(tmp_path,monkeypatch):
    import errno
    import os
    import sys
    from types import SimpleNamespace

    from decompnet import writer_gate
    calls=[];waits=[]
    def locking(fd,mode,length):
        assert os.lseek(fd,0,os.SEEK_CUR)==0 and length==1
        calls.append(mode)
        if len(calls)==1:raise OSError(errno.EACCES,'busy')
    fake_os=SimpleNamespace(**{name:getattr(os,name) for name in
        ('open','close','write','fstat','lseek','O_CREAT','O_RDWR','SEEK_SET')},name='nt')
    monkeypatch.setattr(writer_gate,'os',fake_os)
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(locking=locking,LK_NBLCK=1,LK_UNLCK=2))
    gate=writer_gate.WriterGate(tmp_path/'app.db')
    with gate.hold(wait=lambda:waits.append(1)) as acquired:
        assert acquired and gate.path.stat().st_size==0
    assert waits==[1] and calls==[1,1,2]
