"""Background scheduling changes stay local to maintenance, never foreground judging."""
import threading
from types import SimpleNamespace

from decompnet.app import maintenance, saves
from decompnet.app.agents import connection_retry_delay


def test_deep_autosave_checks_are_periodic_but_dirty_checks_continue(monkeypatch):
    clock=[10.0]
    monkeypatch.setattr(saves.time,'monotonic',lambda:clock[0])
    calls=[]
    net=SimpleNamespace(store=SimpleNamespace(all=lambda *_:[{'slug':'a','root':'','config':'{}'}]),
                        progress=SimpleNamespace(sync=lambda slug,**kw:calls.append(kw['force']) or True))
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
    attempted=threading.Event()
    @contextmanager
    def forbidden(*args):
        attempted.set()
        yield True
    monkeypatch.setattr(maintenance,'slot',forbidden)
    result=[]
    worker=threading.Thread(target=lambda:result.append(twin.full_pass('game')))
    worker.start()
    assert not attempted.wait(.05)
    job.state='done';worker.join(2)
    assert result==[job] and not attempted.is_set()
