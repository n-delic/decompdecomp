"""Private, bounded compiler preparation for one worker's search, never a match cache."""
import copy
import functools
import hashlib
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
        self.results = OrderedDict()
        self.bytes = 0
        self.targets = OrderedDict()
        self.instructions = OrderedDict()
        self.metrics = {}

    def prepare(self, cfg, header):
        from .compiler import _workspace
        # Read exact bytes each time: same-size edits with restored mtimes must invalidate.
        files = {}
        for directory in cfg.include_dirs:
            root = Path(directory)
            if not root.exists():raise FileNotFoundError(root)
            if not root.is_dir():raise NotADirectoryError(root)
            def failed(error):raise error
            for folder, dirs, names in os.walk(root, onerror=failed):
                if any((Path(folder) / name).is_symlink() for name in dirs):
                    return None  # The ordinary copier owns directory-symlink semantics.
                for name in sorted(names):
                    path = Path(folder) / name
                    if not path.is_file():raise shutil.SpecialFileError(str(path))
                    data = path.read_bytes()
                    if path.suffix.lower() in ('.h', '.hpp', '.hxx', '.inc', '.inl'):
                        data = data.replace(b'\r\n', b'\n')
                    files[str(path.relative_to(root))] = data
        files['context.h'] = header.encode()
        if len({name.casefold() for name in files}) != len(files):
            return None  # Preserve the ordinary copier's behavior on case-colliding trees.
        fingerprint = hashlib.sha256()
        for name, data in sorted(files.items()):
            for part in (name.encode(), data):
                fingerprint.update(len(part).to_bytes(8, 'little')); fingerprint.update(part)
        key = fingerprint.digest()
        if self.work is None:
            self.work = Path(self.stack.enter_context(_workspace('decompnet-session-')))
        work = self.work
        # Each invocation starts with just its headers. Never inherit compiler outputs.
        for path in work.iterdir():
            if path.name != 'include':
                if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
                else:path.unlink()
        inc = work / 'include'
        if self.headers != key or not inc.is_dir() or inc.is_symlink():
            if inc.is_symlink():inc.unlink()
            elif inc.exists():shutil.rmtree(inc)
            inc.mkdir()
        else:
            # Restore any compiler-written header and remove unexpected files.
            for path in sorted(inc.rglob('*'), reverse=True):
                if path.is_symlink() or path.is_file() and str(path.relative_to(inc)) not in files:
                    path.unlink()
        for name, data in files.items():
            path = inc / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file() or path.read_bytes() != data:path.write_bytes(data)
        self.headers = key
        self.staged = [(name.encode(), data) for name, data in sorted(files.items())]
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
