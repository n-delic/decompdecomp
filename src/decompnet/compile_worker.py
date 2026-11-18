"""Compiles run in a child process of the deep search, so the app's one interpreter only hands them out.

A deep search's compile threads spent their Python (staging headers, the cache key, starting the compiler, reading
its object back) under the app's one interpreter lock: with 32 compile slots a sweep still finished about a hundred
candidates a second and left most of an 18-core machine idle. Each compile thread now owns a child that runs
`compiler.compile_source` in its own compile session. The app keeps what must stay in one place: the compile
slots every compile shares (held while the child works), cancellation, and the timing counters (the child's are
added to the thread's). The child is the app's own Python, spoken to over pipes with pickles; it never sees the
database, claims or verdicts, only a compiler configuration, a header and a source.
"""
from __future__ import annotations

import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_RUNNER: ContextVar = ContextVar('compile_worker_runner', default=None)
_HEADER = struct.Struct('<Q')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _write(stream, value):
    data = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)) + data)
    stream.flush()


def _read(stream):
    head = stream.read(_HEADER.size)
    if len(head) < _HEADER.size:
        return None
    size, = _HEADER.unpack(head)
    data = stream.read(size)
    if len(data) < size:
        return None
    return pickle.loads(data)


class Unavailable(Exception):
    """The child could not start or stopped: the caller compiles in this process instead."""


class Worker:
    def __init__(self, background=False):
        from . import limits
        argv = [sys.executable, '-m', 'decompnet.compile_worker'] + (['--background'] if background else [])
        self.process = subprocess.Popen(limits.capped(argv, memory=2 << 30), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                        start_new_session=os.name != 'nt')
        self.replies = queue.Queue()
        self.sent = 0
        self.closed = threading.Event()
        threading.Thread(target=self._receive, daemon=True, name='compile-worker-output').start()

    def _receive(self):
        try:
            while (reply := _read(self.process.stdout)) is not None:
                self.replies.put(reply)
        except (OSError, EOFError, pickle.UnpicklingError):
            pass
        finally:
            self.closed.set()
            self.replies.put(None)

    def compile(self, cfg, header, source, trusted, check):
        """compile_source(cfg, header, source, trusted) in the child. A cancelled call raises from `check` and
        leaves its reply to be dropped by the next call: the compile ends within its own timeout."""
        if self.closed.is_set():
            raise Unavailable()
        self.sent += 1
        ident = self.sent
        try:
            _write(self.process.stdin, (ident, cfg, header, source, trusted))
        except (OSError, ValueError) as error:
            raise Unavailable() from error
        while True:
            check()
            try:
                reply = self.replies.get(timeout=.05)
            except queue.Empty:
                continue
            if reply is None:
                raise Unavailable()
            number, outcome, value, counts = reply
            if number != ident:
                continue  # a cancelled call's late reply
            from . import compile_timing
            for name, amount in counts.items():
                compile_timing.add(name, amount)
            if outcome == 'error':
                raise value
            return value

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


@contextmanager
def remote(*, background=False):
    """Within this block, this thread's compile_source calls run in one child of its own (started on first use).
    If the child can't start or stops, compiles carry on in this process."""
    state = {'worker': None, 'failed': False}

    def run(cfg, header, source, trusted, local):
        from . import compiler
        if state['failed']:
            return local()
        if state['worker'] is None or state['worker'].closed.is_set():
            try:
                state['worker'] = Worker(background)
            except OSError:
                state['failed'] = True
                return local()
        check = compiler._CANCEL_CHECK.get() or (lambda: None)
        try:
            with compiler._slot():
                return state['worker'].compile(cfg, header, source, trusted, check)
        except Unavailable:
            state['worker'] = None
            state['failed'] = True
            return local()

    token = _RUNNER.set(run)
    try:
        yield
    finally:
        _RUNNER.reset(token)
        if state['worker'] is not None:
            state['worker'].close()


def runner():
    return _RUNNER.get()


def _serve(background):
    from . import compile_sessions, compile_timing, compiler
    if background:
        from .app.workers import started_priority
        started_priority()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing printed may corrupt the replies
    with compile_sessions.session():
        while (request := _read(stdin)) is not None:
            ident, cfg, header, source, trusted = request
            counts = compile_timing.Counts()
            try:
                with compile_timing.collect(counts), _RUNNER_DISABLED():
                    result = compiler.compile_source(cfg, header, source, trusted)
                reply = (ident, 'ok', result, counts.values)
            except Exception as error:  # returned to the caller, raised there
                try:
                    pickle.dumps(error)
                except Exception:
                    error = RuntimeError(f'{type(error).__name__}: {error}')
                reply = (ident, 'error', error, counts.values)
            _write(stdout, reply)


@contextmanager
def _RUNNER_DISABLED():
    token = _RUNNER.set(None)
    try:
        yield
    finally:
        _RUNNER.reset(token)


if __name__ == '__main__':
    _serve('--background' in sys.argv)
