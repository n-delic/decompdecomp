"""Local attempt workers. The caller owns claims, project locks and result recording."""
from __future__ import annotations

import logging
import os
import pickle
import queue
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .. import compile_timing, limits
from ..compile_worker import _read, _write
from ..hostenv import NO_WINDOW

WORKER_SOURCE = Path(__file__).with_name("attempt_worker_child.py")
REPLY_SECONDS = 30 * 60
_SLOT = ContextVar("attempt_worker", default=None)
log = logging.getLogger("decompdecomp")


class Unavailable(Exception):
    """The private worker channel closed before returning a result."""


class _Worker:
    def __init__(self, source, errors):
        self.source = source
        with tempfile.TemporaryDirectory(prefix="decomp-judge-") as folder:
            # Python reads the entire script at startup. Keep the snapshot until it signals readiness.
            script = Path(folder) / "judge.py"
            script.write_bytes(source)
            with errors.open("ab") as diagnostics:
                self.process = subprocess.Popen(
                    limits.capped([sys.executable, str(script)]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=diagnostics, creationflags=NO_WINDOW, start_new_session=os.name != "nt",
                )
            self.messages = queue.Queue()
            self.reader = threading.Thread(target=self._receive, daemon=True, name="attempt-worker-output")
            self.reader.start()
            try:
                if self.messages.get(timeout=30) != ("ready", None):
                    raise Unavailable("worker did not start")
            except (queue.Empty, Unavailable) as error:
                self.close()
                raise Unavailable("worker did not start") from error

    def _receive(self):
        try:
            while (message := _read(self.process.stdout)) is not None:
                self.messages.put(message)
        except (OSError, EOFError, ValueError, pickle.UnpicklingError):
            pass
        finally:
            self.messages.put(None)

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdout.close()

    def send(self, value):
        try:
            _write(self.process.stdin, value)
        except (OSError, TypeError, ValueError, pickle.PicklingError) as error:
            raise Unavailable("worker could not receive the request") from error

    def run(self, action, args, build, check):
        self.send((action, args))
        deadline = time.monotonic() + REPLY_SECONDS
        while True:
            try:
                check()
                if time.monotonic() >= deadline:
                    raise Unavailable("worker did not answer in time")
            except BaseException:
                try:
                    self.send(("cancel", None))
                except Unavailable:
                    pass
                raise
            try:
                message = self.messages.get(timeout=.1)
            except queue.Empty:
                continue
            if message is None:
                raise Unavailable("worker output closed")
            kind, value = message
            if kind == "build":
                # The app launches and reaps the actual project command while the project build lock is held.
                # A disconnected worker therefore cannot leave a compiler writing after the app rolls back C.
                cmd, timeout, wants_failed, platform = value
                failed = [] if wants_failed else None
                try:
                    result = build(cmd, lambda *_: None, timeout, failed, platform)
                except Exception as error:
                    self.send(("error", error))
                else:
                    self.send(("ok", (result, failed)))
                deadline = time.monotonic() + REPLY_SECONDS
                continue
            outcome, counts = value
            for name, amount in counts.items():
                compile_timing.add(name, amount)
            return kind, outcome


class Pool:
    def __init__(self, internal: Path, size=4):
        self.errors = Path(internal) / "logs" / "attempt-workers.log"
        self.idle = queue.LifoQueue()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        for _ in range(size):
            self.idle.put(None)

    def close(self):
        with self.lock:
            self.closed.set()
        while True:
            try:
                worker = self.idle.get_nowait()
            except queue.Empty:
                return
            if worker is not None:
                worker.close()

    def run(self, revision, action, args, *, build, check):
        while True:
            check()
            if self.closed.is_set():
                raise Unavailable("worker pool is closing")
            try:
                worker = self.idle.get(timeout=.1)
                break
            except queue.Empty:
                continue
        try:
            if worker is not None and (worker.source != revision or worker.process.poll() is not None):
                worker.close()
                worker = None
            if worker is None:
                try:
                    self.errors.parent.mkdir(parents=True, exist_ok=True)
                    worker = _Worker(revision, self.errors)
                except OSError as error:
                    raise Unavailable(str(error)) from error
            return worker.run(action, args, build, check)
        except BaseException:
            # A reply can still be in flight after cancellation or a damaged channel.
            if worker is not None:
                worker.close()
                worker = None
            raise
        finally:
            with self.lock:
                closing = self.closed.is_set()
                if not closing:
                    self.idle.put(worker)
            if closing and worker is not None:
                worker.close()


@contextmanager
def attempt(net):
    """Keep an attempt on one source revision even when it makes several worker calls."""
    pool = getattr(net, "attempt_workers", None)
    if pool is None or _SLOT.get() is not None:
        yield
        return
    try:
        source = WORKER_SOURCE.read_bytes()
    except OSError:
        yield
        return
    token = _SLOT.set((pool, source))
    try:
        yield
    finally:
        _SLOT.reset(token)


def active():
    return _SLOT.get() is not None


def run(action, args, local, *, build=None, compile_slot=False):
    scope = _SLOT.get()
    if scope is not None:
        from .. import compiler

        pool, source = scope
        check = compiler._CANCEL_CHECK.get() or (lambda: None)
        try:
            if compile_slot:
                with compiler._slot():
                    kind, result = pool.run(source, action, args, build=build, check=check)
            else:
                kind, result = pool.run(source, action, args, build=build, check=check)
        except Unavailable as error:
            log.warning("Attempt worker unavailable for %s: %s", action, error)
        else:
            if kind == "error":
                raise result
            return result
    return local()


def start(internal):
    return None if os.environ.get("DECOMP_ATTEMPT_WORKERS") == "0" else Pool(internal)
