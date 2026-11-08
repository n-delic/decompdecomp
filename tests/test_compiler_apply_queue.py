"""The comparison job owns verification workers through cancellation and errors."""
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from decompnet.app.compiler_apply_queue import Queue


def test_stop_cancels_queued_apply_and_joins_running_worker():
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    ran=[]
    queue=Queue(SimpleNamespace(progress=SimpleNamespace(view=nullcontext)),lambda line:None)
    def running():
        started.set()
        assert release.wait(3)
        ran.append('saved')
    queue.submit('first',running)
    assert started.wait(3)
    queue.submit('second',lambda:ran.append('pending'))
    thread=threading.Thread(target=lambda:(queue.close(),closed.set()))
    thread.start()
    try:
        assert not closed.wait(.05), 'Job must own the active verification until it stops'
    finally:
        release.set();thread.join(3)
    assert closed.is_set() and ran==['saved']


def test_apply_failure_is_not_reported_as_success():
    queue=Queue(SimpleNamespace(progress=SimpleNamespace(view=nullcontext)),lambda line:None)
    def fail():raise ValueError('failed verification')
    try:
        queue.submit('first',fail)
        with pytest.raises(ValueError,match='failed verification'):queue.finish()
    finally:queue.close()
