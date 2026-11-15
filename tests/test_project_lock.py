"""Project lock diagnostics retain native recursion, timeout and Condition semantics."""
import logging
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import project_lock
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


def test_sync_reports_the_owner_while_the_wait_is_still_blocked(tmp_path, monkeypatch, caplog):
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    tiny_game(net, 'private-project')
    progress = net.progress
    lock = progress._project_lock('private-project')
    entered, release, logged = threading.Event(), threading.Event(), threading.Event()
    owner_ids, messages = [], []
    original = net._project
    def reading(slug):
        with progress._project_lock(slug):
            if not owner_ids:owner_ids.extend((threading.get_ident(), threading.get_native_id()))
            if threading.get_ident() == owner_ids[0]:
                private_source = 'proprietary C body'
                credential = 'Bearer private-credential'
                assert private_source and credential
                entered.set()
                assert release.wait(5)
        return original(slug)
    class Notice(logging.Handler):
        def emit(self, record):
            if record.getMessage().startswith('Project lock wait '):
                messages.append(record.getMessage())
                logged.set()
    logger = logging.getLogger('decompdecomp')
    notice = Notice()
    logger.addHandler(notice)
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    monkeypatch.setattr(net, '_project', reading)
    monkeypatch.setattr(project_lock, 'WAIT_SECONDS', .02)
    try:
        with ThreadPoolExecutor(3, thread_name_prefix='private-project-Bearer-private-credential') as pool:
            owner = pool.submit(progress.sync, 'private-project', force=True)
            waiters = []
            try:
                assert entered.wait(3)
                waiters = [pool.submit(progress.sync, 'private-project', force=True) for _ in range(2)]
                assert logged.wait(3), 'the diagnostic must arrive before the holder releases the lock'
                assert not owner.done() and all(not waiter.done() for waiter in waiters)
                assert len(messages) == 1
                message = messages[0]
                assert f'owner={owner_ids[0]}' in message and f'owner_native={owner_ids[1]}' in message
                assert 'depth=2 phase=Reading saved files' in message
                assert 'progress.py:sync:' in message and 'waiter_native=' in message and 'held=' in message
                assert len(message) < 3000
                for secret in ('private-project', 'proprietary C body', 'Bearer', 'private-credential', str(tmp_path)):
                    assert secret not in message
            finally:release.set()
            assert owner.result(timeout=3) is False
            for waiter in waiters:assert waiter.result(timeout=3) is False
    finally:logger.removeHandler(notice)
    assert lock.owner() is None


def test_recursive_condition_wait_releases_and_restores_every_level():
    lock = project_lock.ProjectLock()
    condition = threading.Condition(lock)
    seen = []
    lock.acquire();lock.acquire();lock.acquire()
    lock.set_phase('Reading saved files')
    before = lock.owner()
    def notify():
        with condition:
            seen.append(lock.owner())
            condition.notify()
    thread = threading.Thread(target=notify)
    thread.start()
    try:
        assert condition.wait(3)
        owner = lock.owner()
        assert owner.thread == before.thread and owner.depth == 3
        assert owner.phase == before.phase and owner.since >= before.since
        assert seen[0].thread != before.thread and seen[0].depth == 1
        assert not condition.wait(.001)  # Timeout restores the same recursive ownership too.
        assert lock.owner().depth == 3
    finally:
        lock.release();lock.release();lock.release()
        thread.join(3)
    assert not thread.is_alive() and lock.owner() is None
    with pytest.raises(RuntimeError):lock.release()


def test_timeouts_invalid_arguments_and_nonowner_release_keep_native_behavior():
    lock = project_lock.ProjectLock()
    with lock:
        owner = lock.owner()
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(lock.acquire, True, .005).result(timeout=1) is False
            with pytest.raises(RuntimeError):pool.submit(lock.release).result(timeout=1)
        assert lock.owner() == owner
        for blocking, timeout, error in ((False, .1, ValueError), (True, -2, ValueError), (True, None, TypeError)):
            with pytest.raises(error):lock.acquire(blocking, timeout)
            assert lock.owner() == owner
        assert lock.acquire(False)
        assert lock.owner().depth == 2 and lock.owner().since == owner.since
        lock.release()
    assert lock.owner() is None


def test_condition_restore_wait_reports_the_current_holder(monkeypatch, caplog):
    lock = project_lock.ProjectLock()
    condition = threading.Condition(lock)
    logged = threading.Event()
    messages, notifier_ids = [], []
    class Notice(logging.Handler):
        def emit(self, record):
            if record.getMessage().startswith('Project lock wait '):
                messages.append(record.getMessage())
                logged.set()
    notice = Notice()
    logger = logging.getLogger('decompdecomp')
    logger.addHandler(notice)
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    monkeypatch.setattr(project_lock, 'WAIT_SECONDS', .02)
    lock.acquire();lock.acquire()
    lock.set_phase('Reading saved files')
    original = lock.owner()
    def notify():
        with condition:
            notifier_ids.extend((threading.get_ident(), threading.get_native_id()))
            lock.set_phase('Comparing saved files')
            condition.notify()
            logged.wait(3)  # Force the original holder to wait in native _acquire_restore.
    thread = threading.Thread(target=notify)
    thread.start()
    try:
        assert condition.wait(4)
        assert logged.is_set() and len(messages) == 1
        assert f'owner={notifier_ids[0]}' in messages[0]
        assert f'owner_native={notifier_ids[1]}' in messages[0]
        assert f'waiter={original.thread}' in messages[0]
        assert 'depth=1 phase=Comparing saved files' in messages[0]
        assert lock.owner().thread == original.thread and lock.owner().depth == 2
        assert lock.owner().phase == 'Reading saved files'
    finally:
        lock.release();lock.release()
        thread.join(3)
        logger.removeHandler(notice)
    assert not thread.is_alive() and lock.owner() is None


def test_phase_is_constant_and_only_the_holder_can_update_it():
    lock = project_lock.ProjectLock()
    with lock:
        lock.set_phase('Comparing saved files')
        with ThreadPoolExecutor(1) as pool:pool.submit(lock.set_phase, 'Saving verified progress').result(timeout=1)
        lock.set_phase('private source and credentials')
        assert lock.owner().phase == 'Comparing saved files'


def test_monitor_is_bounded_weak_and_rate_limited(monkeypatch, caplog):
    monitor = project_lock._Monitor()
    monitor.worker = object()  # Drive the observer synchronously for this bookkeeping test.
    monkeypatch.setattr(project_lock, 'MAX_WAITERS', 2)
    monkeypatch.setattr(project_lock, '_owner_stack', lambda _: 'safe.py:work:1')
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    lock = project_lock.ProjectLock()
    with lock:
        keys = [monitor.add(lock) for _ in range(3)]
        assert len(monitor.waiters) == 2 and keys[2] is None
        now = time.monotonic()+project_lock.WAIT_SECONDS
        with monitor.condition:
            due = monitor.due(now)
            assert len(due) == 2 and monitor.due(now) == []
            for _, waiter in due:waiter.thread = -1
        for key, waiter in due:monitor.report(key, waiter, now)
        assert len(caplog.records) == 1
        monitor.report(*due[0], now+project_lock.LOG_SECONDS)
        assert len(caplog.records) == 2
        for key in keys:monitor.remove(key)
        monitor.report(*due[0], now+2*project_lock.LOG_SECONDS)
        assert len(caplog.records) == 2  # A completed wait never logs again.
    reference = weakref.ref(lock)
    key = monitor.add(lock)
    del lock
    assert reference() is None  # The registry cannot keep a removed project alive.
    monitor.remove(key)
    assert not monitor.waiters


def test_observer_failure_does_not_change_lock_results(monkeypatch, caplog):
    monitor = project_lock._Monitor()
    monkeypatch.setattr(project_lock, '_monitor', monitor)
    lock = project_lock.ProjectLock()
    entered, release = threading.Event(), threading.Event()
    def holding():
        with lock:
            entered.set()
            release.wait(3)
    holder = threading.Thread(target=holding)
    holder.start()
    assert entered.wait(1)
    attempts = []
    def unavailable(*args):
        attempts.append(1)
        raise RuntimeError('private host detail')
    monkeypatch.setattr(threading.Thread, 'start', unavailable)
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    try:
        assert not lock.acquire(timeout=.001)
        assert not lock.acquire(timeout=.001)
    finally:
        release.set()
        holder.join(1)
    assert lock.acquire(timeout=.001)  # Optional observer cannot replace a successful native result either.
    lock.release()
    assert not holder.is_alive()
    assert not monitor.waiters and lock.owner() is None
    assert attempts == [1]
    assert len(caplog.records) == 1 and 'monitor unavailable: RuntimeError' in caplog.text
    assert 'private host detail' not in caplog.text


def test_nonblocking_and_recursive_entries_do_not_start_an_observer(monkeypatch):
    monitor = project_lock._Monitor()
    monkeypatch.setattr(project_lock, '_monitor', monitor)
    lock = project_lock.ProjectLock()
    with lock:
        assert lock.acquire(False)
        lock.release()
        assert lock.acquire(timeout=.01)
        lock.release()
    assert monitor.worker is None and not monitor.waiters and lock.owner() is None


def test_exception_in_holder_clears_ownership():
    lock = project_lock.ProjectLock()
    with pytest.raises(ValueError, match='synthetic'):
        with lock:raise ValueError('synthetic')
    assert lock.owner() is None
    with lock:assert lock.owner().depth == 1
