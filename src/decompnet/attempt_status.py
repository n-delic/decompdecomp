"""Request-scoped observation of candidate judging."""
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('attempt_status', default=None)
MESSAGES = {
    'starting': 'Preparing candidate…',
    'waiting': 'Waiting for saved-file verification…',
    'compiling': 'Compiling candidate…',
    'checking': 'Checking linked bytes…',
    'saving': 'Recording result…',
}


@contextmanager
def watch(name, callback):
    token = _current.set({'name':name, 'callback':callback, 'stage':None})
    try:
        update('starting',name)
        yield
    finally:
        _current.reset(token)


def update(stage, name=None):
    state = _current.get()
    if not state or (name is not None and state['name'] != name) or state['stage']==stage:return
    # A saved-file import can record other results before this candidate starts.
    if stage=='saving' and state['stage'] in ('starting', 'waiting'):return
    state['stage']=stage
    state['callback']({'stage':stage,'message':MESSAGES[stage],'function':state['name']})
