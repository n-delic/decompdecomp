"""Profile checks overlap without sharing compiler workspaces or changing tie order."""
import threading
from types import SimpleNamespace

import pytest

from decompnet import compile_sessions
from decompnet.app.compiler_profiles import check_profiles
from decompnet.network import NetworkError


def test_profiles_overlap_with_private_sessions_and_stable_order():
    barrier = threading.Barrier(4)
    sessions = []
    lock = threading.Lock()
    def judge(proj, fn, tc, flags, source):
        with lock:
            sessions.append(compile_sessions.current())
        barrier.wait(timeout=5)
        return True, flags[0], ''
    net = SimpleNamespace(_judge_with=judge)
    profiles = [('compiler', (str(i),)) for i in range(4)]
    results = list(check_profiles(net, {}, {}, profiles, 'C', lambda:None))
    assert [profile for profile, _ in results] == profiles
    assert [result[1] for _, result in results] == ['0','1','2','3']
    assert len({id(s) for s in sessions}) == 4 and all(sessions)


def test_abandoned_profiles_cancel_running_work_and_do_not_queue_rest():
    started = threading.Event()
    calls = []
    def judge(proj, fn, tc, flags, source):
        calls.append(flags[0])
        if flags[0] == '0':
            assert started.wait(5)
            return False, None, ''
        started.set()
        # Exercise the same cancellation context used by compiler subprocesses.
        from decompnet.compiler import _CANCEL_CHECK
        while True:
            _CANCEL_CHECK.get()()
            threading.Event().wait(.01)
    checks = check_profiles(SimpleNamespace(_judge_with=judge), {}, {},
                            [('compiler',(str(i),)) for i in range(50)], 'C', lambda:None)
    next(checks)
    checks.close()
    assert len(calls) <= 4


def test_cancel_before_start_launches_no_compilers():
    def cancel():raise NetworkError('cancelled')
    net = SimpleNamespace(_judge_with=lambda *a:pytest.fail('cancelled'))
    with pytest.raises(NetworkError, match='cancelled'):
        list(check_profiles(net, {}, {}, [('compiler',())], 'C', cancel))


def test_worker_failure_is_not_saved_as_a_completed_check():
    def judge(*args):raise TimeoutError('judge timed out')
    with pytest.raises(TimeoutError, match='judge timed out'):
        list(check_profiles(SimpleNamespace(_judge_with=judge), {}, {},
                            [('compiler',())], 'C', lambda:None))
