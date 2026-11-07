"""Serialize each Git repository, giving queued pushes the next available turn."""
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from . import repository_git

_CONDITION = threading.Condition()
_STATES = {}


def key(root):
    root = Path(root).resolve()
    # Linked worktrees share refs and objects, so they must share the same queue.
    if (root/'.git').exists():
        return (root/repository_git.git(root, 'rev-parse', '--git-common-dir')).resolve()
    return root/'.git'  # Also stable while setup initializes a new repository.


class Operation:
    def __init__(self, project, label, log):
        self.project, self.label, self.output = project, label, log
        self.started = time.monotonic()
        self.phase = label

    def __call__(self, message):
        with _CONDITION:
            self.phase = str(message).splitlines()[0][:240] if str(message).strip() else self.phase
        self.output(message)

    def progress(self, value):
        with _CONDITION:
            self.phase = value.get('phase') or self.phase
        getattr(self.output, 'progress', lambda value: None)(value)

    def __getattr__(self, name):
        return getattr(self.output, name)


def _state(identity):
    return _STATES.setdefault(identity, {'holder':None, 'waiters':deque()})


def _clean(identity, state):
    if state['holder'] is None and not state['waiters']:
        _STATES.pop(identity, None)
    _CONDITION.notify_all()


@contextmanager
def operation(root, project, label, log):
    """Try a nonqueued operation; background callers leave durable work pending when busy."""
    identity = key(root)
    acquired = None
    with _CONDITION:
        state = _state(identity)
        if state['holder'] is None and not state['waiters']:
            acquired = state['holder'] = Operation(project, label, log)
    try:
        yield acquired
    finally:
        if acquired is not None:
            with _CONDITION:
                state['holder'] = None
                _clean(identity, state)


def _duration(seconds):
    seconds = int(seconds)
    return f'{seconds//60}m {seconds%60:02d}s' if seconds >= 60 else f'{seconds}s'


@contextmanager
def push(root, project, log, check):
    identity = key(root)
    ticket = object()
    started = time.monotonic()
    acquired = None
    previous = None
    refreshed = float('-inf')
    with _CONDITION:
        state = _state(identity)
        state['waiters'].append(ticket)
    try:
        while True:
            check()
            with _CONDITION:
                position = state['waiters'].index(ticket)+1
                if position == 1 and state['holder'] is None:
                    acquired = state['holder'] = Operation(project, 'Pushing commits', log)
                    state['waiters'].popleft()
                    break
                now = time.monotonic()
                holder = state['holder']
                blocker = {'project':holder.project, 'operation':holder.label, 'phase':holder.phase,
                           'elapsed_seconds':int(now-holder.started)} if holder else None
                description = (f'{holder.label} ({holder.project})'+
                               (f': {holder.phase}' if holder.phase != holder.label else '') if holder else
                               'the preceding push to start')
                phase = (f'Queued — {position-1} push request(s) ahead. ' if position > 1 else 'Queued — ')
                phase += f'waiting for {description}'
                changed = (position, description) != previous
                if changed or now-refreshed >= 1:
                    update = {'phase':phase+f' · waited {_duration(now-started)}', 'queued':True,
                              'queue_position':position, 'wait_seconds':int(now-started), 'blocked_by':blocker}
                    # Do not call user callbacks while holding the coordinator's condition.
                    refreshed = now
                    previous = (position, description)
                else:
                    update = None
            if update:
                if changed:log(phase+'. This push has priority when the current operation finishes; Stop removes it from the queue.')
                getattr(log, 'progress', lambda value: None)(update)
            with _CONDITION:
                _CONDITION.wait(.2)
        check()
        yield acquired
    finally:
        with _CONDITION:
            if acquired is not None:
                state['holder'] = None
            elif ticket in state['waiters']:
                state['waiters'].remove(ticket)
            _clean(identity, state)
