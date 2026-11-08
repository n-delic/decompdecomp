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
            parent.submit(work).result()
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
