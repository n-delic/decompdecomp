import threading
from types import SimpleNamespace as NS

import pytest

from decompnet.app.support import Jobs
from decompnet.app.work_monitor import Monitor
from decompnet.network import NetworkError


def monitor():
    stop=threading.Event()
    component=NS(jobs={'game':NS(state='running',stop=stop)},stop=lambda slug:stop.set())
    net=NS(drafter=component,permuter=NS(jobs={}),twins=NS(jobs={}),autopilots={},rooms={},links={},
           progress=NS(activity={},activity_lock=threading.Lock()))
    jobs=Jobs()
    return Monitor(net,jobs,NS(sessions={})),stop


def test_inventory_and_safe_stop():
    m,stop=monitor()
    result=m.snapshot()
    assert result['cpu_percent'] is None
    assert result['items'][0]['title']=='Machine drafts'
    assert result['items'][0]['cpu_percent'] is None
    assert not stop.is_set()
    m.stop('drafter','game')
    assert stop.is_set()
    assert m.snapshot()['items'][0]['state']=='stopping'
    with pytest.raises(NetworkError):
        m.stop('service','unknown')


def test_cpu_is_interval_process_usage(monkeypatch):
    from decompnet.app import work_monitor
    m,_=monitor()
    monkeypatch.setattr(work_monitor.time,'monotonic',lambda:10)
    monkeypatch.setattr(work_monitor.os,'times',lambda:(1,1,0,0,0))
    assert m.snapshot()['cpu_percent'] is None
    monkeypatch.setattr(work_monitor.time,'monotonic',lambda:12)
    monkeypatch.setattr(work_monitor.os,'times',lambda:(2,1,0,0,0))
    assert m.snapshot()['cpu_percent']==50


def test_jobs_stop_requested_not_claimed_finished():
    m,_=monitor()
    event=threading.Event()
    m.jobs.jobs['j']={'state':'running','kind':'Test search'}
    m.jobs.cancel_events['j']=event
    m.stop('job','j')
    assert event.is_set()
    assert m.jobs.jobs['j']['state']=='running'
    assert next(x for x in m.snapshot()['items'] if x['kind']=='job')['state']=='stopping'


def test_thread_association_keeps_shared_pool_and_ambiguous_names_separate():
    m,_=monitor()
    m.jobs.jobs['j']={'state':'running','kind':'Search','thread_id':20}
    m.thread_sampler.snapshot=lambda:[
        {'name':'drafts-game','native_id':10}, {'name':'job-Search','native_id':20},
        {'name':'background-worker_0','native_id':30}]
    rows=m.snapshot()['items']
    assert next(r for r in rows if r['kind']=='job')['thread_ids']==[20]
    assert next(r for r in rows if r['kind']=='drafter')['thread_ids']==[10]
    assert all(30 not in r['thread_ids'] for r in rows)
    m.thread_sampler.snapshot=lambda:[{'name':'drafts-game','native_id':10},{'name':'drafts-game','native_id':11}]
    assert next(r for r in m.snapshot()['items'] if r['kind']=='drafter')['thread_ids']==[]


def test_gui_job_records_its_actual_native_thread():
    m,_=monitor()
    started,release=threading.Event(),threading.Event()
    seen=[]
    def work(log):
        seen.append(threading.get_native_id())
        started.set()
        release.wait(3)
    jid=m.jobs.start('thread registration',work)
    try:
        assert started.wait(2)
        assert m.jobs.jobs[jid]['thread_id']==seen[0]
    finally:
        release.set()


def test_startup_tasks_have_project_context_and_stop_control():
    import logging

    from decompnet.app.startup import StartupQueue
    m,_=monitor()
    m.net.startup=StartupQueue(logging.getLogger('test'))
    key=m.net.startup.add('SDK naming',lambda:None,project='game')
    item=next(r for r in m.snapshot()['items'] if r['kind']=='startup')
    assert item['project']=='game' and item['state']=='queued' and item['stoppable']
    m.stop('startup',key)
    assert m.net.startup.records[key].stop.is_set()
    assert not any(r['kind']=='startup' for r in m.snapshot()['items'])


def test_autosave_has_project_controls_and_thread_attribution():
    m, _ = monitor()
    calls = []
    m.net.autosave = NS(snapshot=lambda:[dict(key='game',state='paused',thread_id=123,detail='Checking files')],
                         pause=lambda *args:calls.append(args))
    m.thread_sampler.snapshot=lambda:[dict(name='autosave-game',native_id=123)]
    row = next(r for r in m.snapshot()['items'] if r['kind']=='autosave')
    assert row['project']=='game' and row['state']=='paused' and row['stoppable']
    assert row['thread_ids']==[123]
    m.stop('autosave','game')
    m.resume('autosave','game')
    assert calls == [('game',), ('game',False)]
    with pytest.raises(NetworkError):m.resume('job','game')
