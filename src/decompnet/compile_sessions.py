"""Private, bounded compiler preparation for one worker's search, never a match cache."""
import copy
import functools
import json
import logging
import os
import shutil
import threading
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path

_current = ContextVar('compile_session', default=None)


def current():
    session = _current.get()
    return session if session and session.owner == threading.get_ident() else None


class Session:
    def __init__(self):
        self.owner = threading.get_ident()
        self.stack = ExitStack()
        self.work = None
        self.headers = None
        self.configurations = OrderedDict()
        self.results = OrderedDict()
        self.bytes = 0
        self.targets = OrderedDict()
        self.instructions = OrderedDict()
        self.metrics = {}
        self.comparisons = OrderedDict()
        self.comparison_sizes = {}
        self.comparison_bytes = 0

    def prepare(self, cfg, header):
        from . import compile_headers
        from .compiler import _workspace
        # Read exact bytes each time, including same-size edits with restored mtimes.
        files=compile_headers.read(cfg.include_dirs)
        if files is None:return None
        files['context.h']=header.encode()
        if len({name.casefold() for name in files})!=len(files):return None
        if self.work is None:
            self.work = Path(self.stack.enter_context(_workspace('decompnet-session-')))
        work = self.work
        # Each invocation starts with just its headers. Never inherit compiler outputs.
        for path in work.iterdir():
            if path.name != 'include':
                if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
                else:path.unlink()
        compile_headers.stage(work/'include',files)
        if files!=self.headers:
            self.staged=[(name.encode(),data) for name,data in sorted(files.items())]
            self.headers=files
        return work

    def get(self, key):
        result = self.results.get(key)
        if result is not None:
            self.results.move_to_end(key)
            return copy.deepcopy(result)

    def put(self, key, result):
        # Resource failures/timeouts are retryable. Cache successful objects only.
        size = len(result.obj or b'') + len(result.log.encode())
        if not result.ok or size > 4 * 1024 * 1024:return
        old = self.results.pop(key, None)
        if old:self.bytes -= len(old.obj or b'') + len(old.log.encode())
        self.results[key] = copy.deepcopy(result)
        self.bytes += size
        while len(self.results) > 128 or self.bytes > 32 * 1024 * 1024:
            _, old = self.results.popitem(last=False)
            self.bytes -= len(old.obj or b'') + len(old.log.encode())


@contextmanager
def session():
    if current() is not None:
        yield current();return
    value = Session()
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)
        value.stack.close()
        if os.environ.get('DECOMP_PERF') == '1':
            logging.getLogger('decompdecomp').info('Compile session timings %s', json.dumps(value.metrics, sort_keys=True))


def batched(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        with session():return function(*args, **kwargs)
    return run
