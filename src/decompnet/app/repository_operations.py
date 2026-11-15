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
    root = Path(root).expanduser().resolve()
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


class _Ticket:
    """A queued request: who asked and for what, so Background work can show the queue."""
    def __init__(self, project, label):
        self.project, self.label, self.started = project, label, time.monotonic()


def snapshot():
    """Each busy repository's holder and queue, oldest first. Identical requests (the same project asking for
    the same operation again while the first still waits) are one entry with a count, not a repeated row."""
    now = time.monotonic()
    with _CONDITION:
        states = [(identity, state['holder'], list(state['waiters'])) for identity, state in _STATES.items()]
    rows = []
    for identity, holder, waiters in states:
        repository = str(Path(identity).parent) if Path(identity).name == '.git' else str(identity)
        queued = {}
        for ticket in waiters:
            entry = queued.setdefault((ticket.project, ticket.label),
                                      {'project':ticket.project, 'operation':ticket.label, 'requests':0,
                                       'position':len(queued)+1, 'wait_seconds':int(now-ticket.started)})
            entry['requests'] += 1
        rows.append({'repository':repository, 'queued':list(queued.values()),
                     'holder':{'project':holder.project, 'operation':holder.label, 'phase':holder.phase,
                               'elapsed_seconds':int(now-holder.started)} if holder else None})
    return rows


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
def push(root, project, log, check, *, label='Pushing commits'):
    identity = key(root)
    ticket = _Ticket(project, label)
    started = ticket.started
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
                    acquired = state['holder'] = Operation(project, label, log)
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
                if changed:log(phase+('. This push' if label == 'Pushing commits' else '. This request')+' has priority when the current operation finishes; Stop removes it from the queue.')
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
