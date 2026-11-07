"""Background scheduling changes stay local to maintenance, never foreground judging."""
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import maintenance, saves
from decompnet.app.agents import connection_retry_delay


@pytest.fixture(autouse=True)
def isolated_clocks(monkeypatch):
    # These tests fake sleeps/CPU clocks while earlier integration tests may still
    # have cleanup threads. Never replace functions on the shared stdlib module.
    from decompnet.app import twins
    # Fake monotonic clocks must not inherit an earlier test's real-time yield stamp.
    monkeypatch.setattr(maintenance._local,'yielded',float('-inf'),raising=False)
    for module in (maintenance, saves, twins):
        monkeypatch.setattr(module, 'time', SimpleNamespace(
            monotonic=time.monotonic, thread_time=time.thread_time,
            sleep=time.sleep, time=time.time))


def test_deep_autosave_checks_are_periodic_but_dirty_checks_continue(monkeypatch):
    clock=[10.0]
    monkeypatch.setattr(saves.time,'monotonic',lambda:clock[0])
    calls=[]
    net=SimpleNamespace(store=SimpleNamespace(all=lambda *_:[{'slug':'a','root':'','config':'{}'}]),
                        progress=SimpleNamespace(sync=lambda slug,**kw:calls.append(kw['force']) or True))
    net.project_store=lambda _:net.store
    auto=saves.Autosave(net)
    assert auto.run_once(force=False)==['a']
    clock[0]+=1
    auto.run_once(force=False)
    clock[0]+=14
    auto.run_once(force=False)
    auto.run_once()  # Explicit saves retain their immediate deep check.
    assert calls==[True,False,True,True]


def test_maintenance_slot_is_global_reentrant_and_queued_work_can_stop():
    entered=threading.Event();finished=threading.Event();stop=threading.Event()
    def waiting():
        entered.set()
        with maintenance.slot(stop) as ready:
            assert not ready
        finished.set()
    with maintenance.slot() as ready:
        assert ready
        with maintenance.slot() as nested:assert nested
        worker=threading.Thread(target=waiting)
        worker.start();assert entered.wait(1)
        stop.set();assert finished.wait(1)
    worker.join()


def test_maintenance_yield_is_bounded_and_foreground_is_not_delayed(monkeypatch):
    clock=[100.0];sleeps=[]
    monkeypatch.setattr(maintenance.time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(maintenance.time,'sleep',sleep)
    maintenance.touch();maintenance.checkpoint()
    assert not sleeps
    with maintenance.slot():maintenance.checkpoint()
    assert sleeps and sum(sleeps)<=.22


def test_connection_backoff_caps_outage_retries_and_spreads_workers():
    assert [connection_retry_delay(n,0) for n in range(1,9)]==[5,10,20,40,80,160,300,300]
    assert connection_retry_delay(1000,30)==330
    assert connection_retry_delay(1,1)!=connection_retry_delay(1,2)


def test_reuse_full_passes_share_one_global_lane():
    from decompnet.app.twins import Twins
    first=threading.Event();release=threading.Event();second=threading.Event()
    twin=Twins.__new__(Twins)
    def full(slug,job):
        if slug=='first':
            first.set();assert release.wait(2)
        else:second.set()
        return slug
    twin._lock=threading.RLock();twin.jobs={}
    twin._execute_full_pass=full
    a=threading.Thread(target=lambda:twin.full_pass('first'))
    b=threading.Thread(target=lambda:twin.full_pass('second'))
    a.start();assert first.wait(1)
    b.start();assert not second.wait(.05)
    release.set();a.join(2);b.join(2)
    assert second.is_set()


def test_duplicate_reuse_waits_without_occupying_the_lane(monkeypatch):
    from contextlib import contextmanager

    from decompnet.app.twins import Job, Twins
    twin=Twins.__new__(Twins)
    twin._lock=threading.RLock();job=Job();twin.jobs={'game':job}
    attempted=threading.Event();waiting=threading.Event();release=threading.Event()
    def wait_for_owner(_):
        waiting.set()
        assert release.wait(3)
    # Wait for the duplicate to observe its owner, rather than assuming that the
    # OS schedules the new thread within a fixed 50ms window.
    monkeypatch.setattr('decompnet.app.twins.time.sleep',wait_for_owner)
    @contextmanager
    def forbidden(*args):
        attempted.set()
        yield True
    monkeypatch.setattr(maintenance,'slot',forbidden)
    result=[]
    worker=threading.Thread(target=lambda:result.append(twin.full_pass('game')))
    worker.start()
    try:
        assert waiting.wait(2)
        assert not attempted.is_set()
        job.state='done'
    finally:
        release.set();worker.join(2)
    assert result==[job] and not attempted.is_set()


def test_cpu_budget_measures_cpu_not_io_and_is_shared_by_nested_slots(monkeypatch):
    wall, cpu, sleeps = [100.0], [0.0], []
    monkeypatch.setattr(maintenance, '_cpu_limit', 50)
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: wall[0])
    monkeypatch.setattr(maintenance.time, 'thread_time', lambda: cpu[0])
    monkeypatch.setattr(maintenance, '_last_ui', float('-inf'))
    def sleep(seconds):
        sleeps.append(seconds)
        wall[0] += seconds
    monkeypatch.setattr(maintenance.time, 'sleep', sleep)
    with maintenance.slot():
        # 100ms CPU in 100ms wall needs another 100ms wall at a 50% limit.
        cpu[0] += .1; wall[0] += .1
        with maintenance.slot():
            maintenance.checkpoint()
        assert .099 <= sum(sleeps) <= .112
        sleeps.clear()
        # Disk waits never create debt, and do not bank credit for the next burst.
        wall[0] += 10
        maintenance.checkpoint()
        assert sum(sleeps) < .01
        sleeps.clear()
        cpu[0] += .1; wall[0] += .1
        maintenance.checkpoint()
        assert sum(sleeps) >= .099
    sleeps.clear()
    cpu[0] += 1
    maintenance.checkpoint()
    assert not sleeps  # foreground work is never paced


def test_cpu_budget_changes_and_cancellation_take_effect_while_waiting(monkeypatch):
    import pytest
    wall, cpu = [100.0], [0.0]
    monkeypatch.setattr(maintenance, '_cpu_limit', 50)
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: wall[0])
    monkeypatch.setattr(maintenance.time, 'thread_time', lambda: cpu[0])
    stop = threading.Event()
    def sleep(seconds):
        wall[0] += seconds
        stop.set()
    monkeypatch.setattr(maintenance.time, 'sleep', sleep)
    with maintenance.slot(stop):
        cpu[0] += 1; wall[0] += 1
        with pytest.raises(maintenance.Cancelled):maintenance.checkpoint()
    maintenance.configure_cpu(0)
    for value in (-1, 101, 50.5, True, '50', None):
        with pytest.raises(ValueError):maintenance.configure_cpu(value)
    maintenance.configure_cpu(100)
    assert maintenance._cpu_limit == 100


def test_cpu_limit_can_be_removed_without_waiting_out_old_debt(monkeypatch):
    wall, cpu, sleeps = [100.0], [0.0], []
    monkeypatch.setattr(maintenance, '_cpu_limit', 1)
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: wall[0])
    monkeypatch.setattr(maintenance.time, 'thread_time', lambda: cpu[0])
    monkeypatch.setattr(maintenance, '_last_ui', float('-inf'))
    def sleep(seconds):
        sleeps.append(seconds);wall[0] += seconds
        maintenance.configure_cpu(0)
    monkeypatch.setattr(maintenance.time, 'sleep', sleep)
    with maintenance.slot():
        cpu[0] += 1;wall[0] += 1
        maintenance.checkpoint()
    assert sum(sleeps) < .1


def test_cpu_budget_accumulates_small_units(monkeypatch):
    wall, cpu, sleeps = [100.0], [0.0], []
    monkeypatch.setattr(maintenance, '_cpu_limit', 50)
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: wall[0])
    monkeypatch.setattr(maintenance.time, 'thread_time', lambda: cpu[0])
    def sleep(seconds):
        sleeps.append(seconds);wall[0] += seconds
    monkeypatch.setattr(maintenance.time, 'sleep', sleep)
    with maintenance.slot():
        for _ in range(100):
            wall[0] += .0001;cpu[0] += .0001
            maintenance._pace_cpu()
    assert .009 <= sum(sleeps) <= .011


def test_explicit_job_bypasses_busy_maintenance_lane_and_pacing(monkeypatch):
    from decompnet.app.support import Jobs
    entered=threading.Event()
    monkeypatch.setattr('decompnet.compiler.background_thread',lambda:(_ for _ in ()).throw(AssertionError('interactive job lowered priority')))
    monkeypatch.setattr(maintenance,'_pace_cpu',lambda:(_ for _ in ()).throw(AssertionError('interactive job throttled')))
    def work(log):
        with maintenance.slot(log.cancel_event) as ready:
            assert ready
            maintenance.checkpoint()
            entered.set()
    jobs=Jobs()
    with maintenance.slot():
        jid=jobs.start('clicked action',work)
        assert entered.wait(2),'Deliberate action waited for background maintenance'
    assert jobs.jobs[jid]['foreground']


def test_manual_cleanup_promotes_queued_retention_without_duplicate(monkeypatch):
    from decompnet.app import database_cleanup
    from decompnet.app.support import Jobs
    entered=threading.Event();finished=threading.Event()
    monkeypatch.setattr('decompnet.compiler.background_thread',lambda:None)
    def clean(net,value,log):
        entered.set()
        assert finished.wait(2)
        return {'messages':0}
    monkeypatch.setattr(database_cleanup,'run',clean)
    jobs=Jobs();value={'removed':True,'before':'2000-01-01','automatic':True}
    try:
        with maintenance.slot():
            automatic=database_cleanup.start(None,jobs,value,foreground=False)
            assert not entered.wait(.15)
            manual=database_cleanup.start(None,jobs,value)
            assert manual==automatic and len(jobs.jobs)==1
            assert entered.wait(2),'Manual cleanup stayed queued behind the lane'
            assert jobs.jobs[manual]['foreground']
    finally:finished.set()


def test_manual_header_cleanup_starts_during_background_maintenance(monkeypatch):
    from decompnet.app import header_cleanup
    from decompnet.app.support import Jobs
    entered=threading.Event()
    monkeypatch.setattr(header_cleanup,'prune',lambda *args:entered.set())
    jobs=Jobs()
    with maintenance.slot():
        jid=header_cleanup.start(None,jobs,'headers')
        assert entered.wait(2)
    assert 'Waiting for the background maintenance lane' not in jobs.jobs[jid]['log']
