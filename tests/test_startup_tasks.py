"""Optional startup tasks are attributable, bounded and stoppable without killing threads."""
import logging
import threading
import time

from decompnet.app import maintenance
from decompnet.app.startup import StartupQueue


def test_task_cancellation_is_cooperative_and_queued_task_can_be_skipped():
    queue=StartupQueue(logging.getLogger('test'),delay=0,spacing=0)
    began=threading.Event();later=threading.Event();finished=threading.Event()
    def running(log):
        log.progress({'phase':'Checking functions','done':1,'total':10})
        began.set()
        try:
            while True:
                maintenance.checkpoint()
                time.sleep(.001)
        finally:finished.set()
    first=queue.add('SDK naming',running,project='game',with_log=True)
    second=queue.add('Console upgrades',later.set,project='other')
    thread=threading.Thread(target=queue.run);thread.start()
    try:
        assert began.wait(2)
        rows=queue.snapshot()
        row=next(r for r in rows if r['key']==first)
        assert row['thread_id'] is not None and row['project']=='game'
        assert '1 / 10' in row['detail']
        assert queue.cancel(second)
        assert queue.cancel(first)
        assert finished.wait(2)
        thread.join(2)
        assert queue.records[first].state=='stopped'
        assert not later.is_set() and not queue.snapshot()
    finally:
        queue.close();thread.join(2)


def test_each_task_owns_a_thread_but_tasks_do_not_overlap():
    queue=StartupQueue(logging.getLogger('test'),delay=0,spacing=0)
    seen=[]
    for label in ('archive','names'):
        queue.add(label,lambda:seen.append(threading.current_thread().name))
    queue.run()
    assert seen==['startup-task-1','startup-task-2']
    assert all(t.state=='done' for t in queue.records.values())
