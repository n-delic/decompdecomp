"""Automatic commit failures keep pending work without rebuilding an executor every second."""
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from decompnet.app import project_activity, purpose_work, saves
from decompnet.app.progress import _StillFailing
from decompnet.network import NetworkError


def harness(monkeypatch, *, until=65):
    clock = [0]
    hook = [lambda: None]
    pools = []

    class Stop:
        stopped = False

        def is_set(self):return self.stopped
        def set(self):self.stopped = True
        def wait(self, seconds):
            assert seconds <= 1  # Stop/resume must not disappear into a long sleep.
            clock[0] += seconds
            hook[0]()
            if clock[0] >= until:self.set()
            return self.stopped

    class Executor:
        def __init__(self, *args, **kwargs):pools.append(clock[0])
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def check_cancel(self):pass
        def submit(self, fn):
            result = Future()
            try:result.set_result(fn())
            except BaseException as error:result.set_exception(error)
            return result

    monkeypatch.setattr(saves.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(project_activity, 'active', lambda *args: True)
    monkeypatch.setattr(purpose_work, 'Executor', Executor)
    auto = saves.Autosave(SimpleNamespace())
    auto.stop = Stop()
    worker = dict(row={'config':'{"auto_commit_matches":true}'}, cancel=threading.Event(), running=False)
    auto.workers['game'] = worker
    return auto, worker, clock, hook, pools


@pytest.mark.parametrize('error', [NetworkError, _StillFailing, OSError])
def test_failed_commits_back_off_actual_work_and_still_log_every_attempt(monkeypatch, caplog, error):
    auto, worker, clock, _, pools = harness(monkeypatch)
    attempts = []
    def commit(*args):
        attempts.append(clock[0])
        raise error('invalid saved progress')
    monkeypatch.setattr(auto, '_commit_once', commit)
    auto._commit_loop('game', worker)
    assert attempts == [0,30,60]
    assert pools == attempts  # Waiting creates no executor or new commit work.
    failures = [r for r in caplog.records if 'Could not process automatic commits' in r.getMessage()]
    assert len(failures) == 3 and all(r.exc_info for r in failures)
    assert all('30 seconds' in r.getMessage() for r in failures)


def test_success_recovers_normal_polling_after_commit_error(monkeypatch):
    auto, worker, clock, _, _ = harness(monkeypatch, until=34)
    attempts = []
    def commit(*args):
        attempts.append(clock[0])
        if len(attempts) == 1:raise NetworkError('temporary failure')
    monkeypatch.setattr(auto, '_commit_once', commit)
    auto._commit_loop('game', worker)
    assert attempts == [0,30,31,32,33]


def test_explicit_resume_retries_without_waiting_for_old_backoff(monkeypatch):
    auto, worker, clock, hook, _ = harness(monkeypatch, until=7)
    attempts = []
    def commit(*args):
        attempts.append(clock[0])
        if len(attempts) == 1:raise NetworkError('corrected after pausing')
    def resume():
        if clock[0] == 2:auto.pause('game')
        if clock[0] == 4:auto.pause('game', False)
    hook[0] = resume
    monkeypatch.setattr(auto, '_commit_once', commit)
    auto._commit_loop('game', worker)
    assert attempts == [0,4,5,6]


def test_shutdown_interrupts_backoff_and_other_project_is_independent(monkeypatch):
    auto, worker, clock, _, _ = harness(monkeypatch, until=1)
    calls = []
    def commit(*args):
        calls.append(clock[0])
        raise NetworkError('only game failed')
    monkeypatch.setattr(auto, '_commit_once', commit)
    auto._commit_loop('game', worker)
    assert calls == [0] and clock[0] == 1
    other = dict(row=worker['row'], cancel=threading.Event(), running=False)
    auto.workers['other'] = other
    auto.stop.stopped = False
    clock[0] = 0
    monkeypatch.setattr(auto, '_commit_once', lambda *args: calls.append(clock[0]))
    auto._commit_loop('other', other)
    assert calls == [0,0]
