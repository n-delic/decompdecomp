"""Cancellable FIFO push requests sharing the existing repository operation lock."""
import threading
from collections import deque
from contextlib import contextmanager

from . import repository_sync

_CONDITION = threading.Condition()
_WAITERS = deque()


@contextmanager
def turn(log, check):
    ticket = object()
    acquired = False
    previous = None
    with _CONDITION:
        _WAITERS.append(ticket)
    try:
        while True:
            check()
            with _CONDITION:
                position = _WAITERS.index(ticket)+1
                if position == 1 and repository_sync._LOCK.acquire(blocking=False):
                    acquired = True
                    _WAITERS.popleft()
                    _CONDITION.notify_all()
                    break
                if position != previous:
                    phase = ('Queued — waiting for the current repository operation' if position == 1 else
                             f'Queued — {position-1} '+('push request' if position == 2 else 'push requests')+' ahead')
                    log(phase+'. This push will start automatically; Stop removes it from the queue.')
                    progress = getattr(log, 'progress', lambda value: None)
                    progress({'phase':phase, 'queued':True, 'queue_position':position})
                    previous = position
                _CONDITION.wait(.2)
        check()
        yield
    finally:
        with _CONDITION:
            if acquired:
                repository_sync._LOCK.release()
            elif ticket in _WAITERS:
                _WAITERS.remove(ticket)
            _CONDITION.notify_all()
