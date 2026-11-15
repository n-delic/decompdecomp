import threading
from concurrent.futures import CancelledError

import pytest

from decompnet import compiler
from decompnet.app import purpose_work
from decompnet.network import NetworkError


def test_named_group_reports_real_threads_and_cancels_only_its_work():
    entered,release=threading.Event(),threading.Event()
    parent=threading.Event()
    def work():
        entered.set()
        assert release.wait(5)
        pool.check_cancel()
    with purpose_work.Executor(1,purpose='Test match search',project='game',stop=parent.set) as pool:
        first=pool.submit(work)
        assert entered.wait(5)
        queued=pool.submit(lambda:pytest.fail('cancelled work must not run'))
        row=next(r for r in purpose_work.snapshot() if r['key']==pool.key)
        assert row['project']=='game' and len(row['thread_ids'])==1 and '1 queued' in row['detail']
        purpose_work.stop(pool.key)
        assert parent.is_set()
        assert next(r for r in purpose_work.snapshot() if r['key']==pool.key)['state']=='stopping'
        with purpose_work.Executor(1,purpose='Unrelated') as other:assert other.submit(lambda:42).result()==42
        release.set()
        with pytest.raises(NetworkError,match='stopped'):first.result()
        with pytest.raises(CancelledError):queued.result()
    assert all(r['key']!=pool.key for r in purpose_work.snapshot())


def test_nested_groups_inherit_project_and_stop_owner():
    stopped=threading.Event()
    with purpose_work.owner('game',stopped.set):
        with purpose_work.Executor(1,purpose='Parent') as parent:
            def work():
                with purpose_work.Executor(1,purpose='Child') as child:
                    assert child.project=='game'
                    child.stop()
            with pytest.raises(NetworkError,match='stopped'):parent.submit(work).result()
    assert stopped.is_set() and parent.cancelled.is_set()


def test_failed_submission_and_failed_work_leave_no_activity():
    pool=purpose_work.Executor(1,purpose='Closed')
    pool.shutdown()
    with pytest.raises(RuntimeError):pool.submit(lambda:None)
    with purpose_work.Executor(1,purpose='Failure') as pool:
        with pytest.raises(ValueError):pool.submit(lambda:(_ for _ in ()).throw(ValueError())).result()
    assert all(row['key']!=pool.key for row in purpose_work.snapshot())


def test_nested_compiler_cancellation_retains_outer_owner():
    checks=[]
    with compiler.cancellable(lambda:checks.append('outer')):
        with compiler.cancellable(lambda:checks.append('inner')):compiler._CANCEL_CHECK.get()()
    assert checks==['outer','inner']


def test_shutdown_cancels_groups_without_changing_saved_preferences():
    started,release=threading.Event(),threading.Event()
    preferences=[]
    with purpose_work.Executor(1,purpose='Automatic push',stop=lambda:preferences.append('disabled')) as pool:
        def work():
            started.set()
            assert release.wait(5)
            pool.check_cancel()
        future=pool.submit(work)
        assert started.wait(5)
        purpose_work.shutdown()
        release.set()
        with pytest.raises(NetworkError,match='stopped'):future.result()
    assert not preferences


def test_cancelled_verification_cannot_return_a_successful_verdict():
    entered,release=threading.Event(),threading.Event()
    with purpose_work.Executor(1,purpose='Exact verification') as pool:
        def verify():
            entered.set()
            assert release.wait(5)
            return True
        future=pool.submit(verify)
        assert entered.wait(5)
        pool.stop()
        release.set()
        with pytest.raises(NetworkError,match='stopped'):future.result()


def test_core_match_recheck_has_a_purpose_and_cannot_publish_after_stop():
    from types import SimpleNamespace

    from decompnet.network import Network
    entered,release=threading.Event(),threading.Event()
    result={}
    groups=purpose_work.Groups(8)
    def compile_source(*args):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(ok=True,obj=object())
    net=SimpleNamespace(_pool=groups,project_store=lambda pid:None,_compile=compile_source,
                        _evaluate=lambda *args:(object(),True,None))
    def verify():
        try:result['verified']=Network.regression_test(net,{'id':1,'slug':'game'},'header',only=[{'id':1,'name':'function','matched_source':'C'}])
        except Exception as error:result['error']=error
    thread=threading.Thread(target=verify)
    thread.start()
    try:
        assert entered.wait(5)
        row=next(r for r in purpose_work.snapshot() if r['title']=='Matched function verification')
        assert row['project']=='game'
        purpose_work.stop(row['key'])
    finally:
        release.set();thread.join(5);groups.shutdown()
    assert not thread.is_alive() and 'verified' not in result
    assert isinstance(result['error'],NetworkError)


def test_parent_stop_reaches_running_child_verification():
    entered,release,cancelled=threading.Event(),threading.Event(),threading.Event()
    def check():
        if cancelled.is_set():raise RuntimeError('Parent cancelled')
    with purpose_work.owner('game',cancelled.set,check):
        with purpose_work.Executor(1,purpose='Child verification') as pool:
            def work():
                entered.set()
                assert release.wait(5)
                return True
            future=pool.submit(work)
            assert entered.wait(5)
            cancelled.set();release.set()
            with pytest.raises(RuntimeError,match='Parent cancelled'):future.result()


def test_operations_reuse_the_shared_threads_instead_of_starting_their_own():
    """#perf: a pool per operation started threads all day, and each new thread opened its own database readers."""
    seen=set()
    for _ in range(20):
        with purpose_work.Executor(2,purpose='Reused work') as pool:
            for future in [pool.submit(lambda:seen.add(threading.get_ident())) for _ in range(4)]:future.result()
    assert len(seen)<=4


def test_a_group_keeps_its_width_and_names_on_shared_threads():
    running,peak,lock=[0],[0],threading.Lock()
    release=threading.Event()
    names=set()
    def work():
        with lock:
            running[0]+=1;peak[0]=max(peak[0],running[0])
            names.add(threading.current_thread().name)
        release.wait(.05)
        with lock:running[0]-=1
    with purpose_work.Executor(3,purpose='Width check',thread_name_prefix='width-check') as pool:
        for future in [pool.submit(work) for _ in range(12)]:future.result()
    assert peak[0]==3 and names<={'width-check_0','width-check_1','width-check_2'}


def test_nested_groups_on_shared_threads_cannot_deadlock():
    """A task that waits for its own nested group's tasks never waits for a thread: the lanes grow on demand."""
    def outer(depth):
        if not depth:return 1
        with purpose_work.Executor(2,purpose='Nested') as inner:
            return sum(f.result(10) for f in [inner.submit(outer,depth-1) for _ in range(2)])
    with purpose_work.Executor(2,purpose='Outer') as pool:
        assert sum(f.result(20) for f in [pool.submit(outer,3) for _ in range(2)])==16


def test_work_starts_with_a_fresh_context_and_scheduling_on_a_reused_thread():
    from contextvars import ContextVar
    marker=ContextVar('marker',default='fresh')
    def leak():
        marker.set('leaked')
        compiler.background_thread()
    with purpose_work.Executor(1,purpose='Leak') as pool:pool.submit(leak).result()
    with purpose_work.Executor(1,purpose='Read') as pool:
        value,background=pool.submit(lambda:(marker.get(),getattr(compiler._SCHEDULING,'background',False))).result()
    assert value=='fresh' and not background


def test_a_thread_whose_work_lowered_its_priority_is_not_reused_for_normal_work(monkeypatch):
    from decompnet.app import thread_lanes, workers
    monkeypatch.setattr(workers.sys,'platform','test')
    monkeypatch.setattr(workers.os,'nice',lambda value:None)
    with purpose_work.Executor(1,purpose='Lowers itself') as pool:
        lowered=pool.submit(lambda:(workers.lower_priority(),threading.get_ident())[1]).result()
    with purpose_work.Executor(4,purpose='Normal') as pool:
        idents={f.result() for f in [pool.submit(lambda:(threading.Event().wait(.02),threading.get_ident(),
                                                          thread_lanes.priority_lowered())) for _ in range(4)]}
    assert all(not low for _,ident,low in idents) and lowered not in {ident for _,ident,_ in idents}


def test_lower_priority_lowers_a_thread_only_once(monkeypatch):
    from decompnet.app import workers
    calls=[]
    monkeypatch.setattr(workers.sys,'platform','test')
    monkeypatch.setattr(workers.os,'name','posix')
    monkeypatch.setattr(workers.os,'nice',calls.append)
    def twice():
        workers.lower_priority();workers.lower_priority()
    thread=threading.Thread(target=twice);thread.start();thread.join()
    assert calls==[workers.NICE]


def test_shutdown_without_waiting_cancels_queued_work_only():
    entered,release=threading.Event(),threading.Event()
    pool=purpose_work.Executor(1,purpose='Cancel queued')
    def block():
        entered.set();assert release.wait(5);return 'ran'
    running=pool.submit(block);assert entered.wait(5)
    queued=pool.submit(lambda:'never')
    pool.shutdown(wait=False,cancel_futures=True)
    release.set()
    assert running.result(5)=='ran' and queued.cancelled()
    with pytest.raises(RuntimeError):pool.submit(lambda:None)
