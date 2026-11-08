"""Request-scoped observation of ordinary judging; never bypass its sync or save gates."""
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('attempt_status', default=None)
MESSAGES = {
    'waiting': 'Waiting for saved-file verification…',
    'compiling': 'Compiling candidate…',
    'checking': 'Checking linked bytes…',
    'saving': 'Saving verified result…',
}


@contextmanager
def watch(name, callback):
    token = _current.set({'name':name, 'callback':callback, 'stage':None})
    try:
        update('waiting',name)
        yield
    finally:
        _current.reset(token)


def update(stage, name):
    state = _current.get()
    if not state or state['name'] != name or state['stage']==stage:return
    # A saved-file import can record other results before this candidate starts.
    if stage=='saving' and state['stage']=='waiting':return
    state['stage']=stage
    state['callback']({'stage':stage,'message':MESSAGES[stage],'function':name})
