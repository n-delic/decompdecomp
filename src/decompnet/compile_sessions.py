"""Private, bounded compiler preparation for one worker's search, with no persistent results."""
import copy
import functools
import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path

_current = ContextVar('compile_session', default=None)
RECHECK_SECONDS = 0.5  # how long a session trusts the headers it just read and staged (see Session.prepare)


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
        self.links = None
        self.instructions = OrderedDict()
        self.alignments = OrderedDict()
        self.metrics = {}
        self.candidate_shapes = OrderedDict()
        self.candidate_calls = OrderedDict()
        self.candidate_header = None
        self.comparisons = OrderedDict()
        self.comparison_sizes = {}
        self.comparison_bytes = 0
        self.header_check = None  # (include dirs, when read, their files, (the set staged, when))
        self.staged_work = None

    def prepare(self, cfg, header):
        from . import compile_headers
        from .compiler import _workspace
        # Exact bytes (a same-size edit with the modification time restored included) are re-read at most every
        # RECHECK_SECONDS: a search compiles hundreds of candidates a second, and walking and stat-ing every header
        # of a game for each was a tenth of a lane's CPU. A header edited meanwhile reaches the next window of
        # candidates a moment later; every result is keyed by the exact bytes it was built with, so none is stale.
        now=time.monotonic()
        seen=self.header_check
        if seen is not None and seen[0]==cfg.include_dirs and now-seen[1]<RECHECK_SECONDS:base=seen[2]
        else:
            base=compile_headers.read(cfg.include_dirs)
            if base is None:return None
            seen=self.header_check=(cfg.include_dirs,now,base,None)
        files=dict(base)
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
        staged=self.header_check[3]
        if staged is not None and staged[0]==files and now-staged[1]<RECHECK_SECONDS and self.staged_work is work:
            pass  # (this very set was staged moments ago; the compiler writes only beside the include folder)
        else:
            self.staged_files=compile_headers.stage(work/'include',files,getattr(self,'staged_files',None))
            self.staged_work=work
            self.header_check=self.header_check[:3]+((files,now),)
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
