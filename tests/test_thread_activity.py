import sys
import threading
import time

import pytest

from decompnet.app import thread_activity as activity


@pytest.mark.parametrize('name,role',[('MainThread','Server'),('AnyIO worker thread','Requests'),
    ('autosave','Saved progress'),('compiler-process-tracker','Compilers'),('background-worker_0','Background pool'),
    ('link-example','Room synchronization'),('room-heartbeat-example','Room heartbeat'),('unknown','Other')])
def test_purpose(name, role):
    actual, text=activity.purpose(name)
    assert actual==role and text


def test_interval_sampling_prunes_threads_and_does_not_guess_missing_cpu(monkeypatch):
    class Thread:
        native_id=10
        name='background-worker_0'
        def is_alive(self):
            return True
    thread=Thread()
    monkeypatch.setattr(activity.threading,'enumerate',lambda:[thread])
    clock=[10];cpu=[1]
    monkeypatch.setattr(activity.time,'monotonic',lambda:clock[0])
    sampler=activity.Sampler();sampler.read=lambda tid:cpu[0]
    assert sampler.snapshot()[0]['cpu_state']=='measuring'
    clock[0]=12;cpu[0]=2
    assert sampler.snapshot()[0]['cpu_percent']==50
    clock[0]=12.1
    assert sampler.snapshot()[0]['cpu_percent']==50
    cpu[0]=None
    assert sampler.snapshot()[0]['cpu_state']=='unavailable'
    monkeypatch.setattr(activity.threading,'enumerate',lambda:[])
    assert sampler.snapshot()==[] and sampler.previous=={}


def test_linux_stat_parser_tolerates_spaces_and_parentheses(monkeypatch):
    monkeypatch.setattr(activity.sys,'platform','linux')
    monkeypatch.setattr(activity.os,'sysconf',lambda name:100)
    monkeypatch.setattr(activity.Path,'read_text',lambda self:'10 (worker (pool)) '+ ' '.join(['S']+['0']*10+['150','50']))
    assert activity.cpu_reader()(10)==2


@pytest.mark.skipif(sys.platform!='darwin' and not sys.platform.startswith('linux'),reason='OS thread counters not available')
def test_os_counter_measures_current_thread_cpu():
    read=activity.cpu_reader()
    before=read(threading.get_native_id())
    assert before is not None
    until=time.thread_time()+.03
    while time.thread_time()<until:
        pass
    after=read(threading.get_native_id())
    assert after is not None and after>before
