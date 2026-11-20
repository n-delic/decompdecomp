"""Progress-folder renders in processes of their own.

Publishing a project's work to its progress folder (progress.py `_publish_changes`) renders the files from the
database: every function's C and the manifest (saves._render, saves._render_partial). On v-rally that is about half a
second of pure Python per publish, most of it encoding the manifest, and it ran on a thread of the app under its one
interpreter lock, so tool calls, the window and saves elsewhere waited for it. Every publish renders, partial ones
too (the manifest is always rebuilt).

These processes do the rendering: each opens the same databases as the tool reader does (store.ATTACH_ONLY, no
saved-progress sync, caches dropped when the database's revisions move) and answers one render at a time. The app
keeps everything else: it checks that the render is still current (folder signature, baseline, dirty generation,
settings) and writes the files, exactly as before. When no worker can answer (none started yet, one died, a render
took too long, an error the app doesn't expect from a render) it renders on its own thread as it always did.

DECOMP_RENDER_WORKERS=0 turns them off; any other number is how many run (two by default: renders of different
projects go side by side; one project renders one at a time anyway).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..hostenv import NO_WINDOW
from . import frontend_reload

log = logging.getLogger("decompdecomp")
WORKERS = 2
LIMIT_SECONDS = 120.0  # a render that takes longer is done here instead (and the worker replaced)
START_SECONDS = 60.0
WORKER_SOURCE = Path(__file__).with_name("render_worker_child.py")


class Unavailable(Exception):
    """No worker answered: render in this process instead."""


def count() -> int:
    raw = os.environ.get("DECOMP_RENDER_WORKERS", "").strip()
    if not raw:
        return WORKERS
    try:
        return max(0, int(raw))
    except ValueError:
        return WORKERS


class _Worker:
    """One render process; one request at a time."""

    def __init__(self, net, internal: Path, number: int):
        self.net, self.internal, self.number = net, Path(internal), number
        self.process: subprocess.Popen | None = None
        self.revision = None
        self.lock = threading.Lock()  # held for a whole request: one at a time

    def _start(self) -> subprocess.Popen:
        log_path = self.internal / "logs" / "render-worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as errors:
            process = subprocess.Popen(
                [sys.executable, str(WORKER_SOURCE), str(self.net.store.path), str(self.net.workspace)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, bufsize=1 << 20,
                creationflags=NO_WINDOW)
        ready: list[bytes] = []
        reader = threading.Thread(target=lambda: ready.append(process.stdout.readline()), daemon=True,
                                  name=f"render-worker-start-{self.number}")
        reader.start()
        reader.join(START_SECONDS)
        if not ready or ready[0].strip() != b"READY":
            process.kill()
            process.wait()
            raise Unavailable(f"the render worker didn't start ({ready[0][:200] if ready else 'no answer'!r})")
        return process

    def ask(self, request: dict, source_revision) -> dict:
        with self.lock:
            # Read the revision after waiting: a queued save can outlive a source update.
            revision = source_revision()
            if self.process is None or self.process.poll() is not None or self.revision != revision:
                self._stop()
                try:
                    self.process = self._start()
                except OSError as error:
                    raise Unavailable(f"the render worker couldn't start: {error}") from None
                self.revision = revision
            process = self.process
            answer: list[bytes] = []
            try:
                process.stdin.write((json.dumps(request) + "\n").encode())
                process.stdin.flush()
            except OSError as error:
                self.process = None
                raise Unavailable(f"the render worker ended: {error}") from None
            reader = threading.Thread(target=lambda: answer.append(process.stdout.readline()), daemon=True,
                                      name=f"render-worker-reply-{self.number}")
            reader.start()
            reader.join(LIMIT_SECONDS)
            if not answer or not answer[0]:
                process.kill()  # (hung or ended: the next request starts another)
                self.process = None
                raise Unavailable("the render worker didn't answer in time" if not answer else "the render worker ended")
            return json.loads(answer[0])

    def _stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait()

    def stop(self) -> None:
        with self.lock:
            self._stop()


class Renderers:
    """The app's side: render(...) in a worker, raising Unavailable when the app should render itself."""

    def __init__(self, net, internal: Path, workers: int):
        self.workers = [_Worker(net, internal, n) for n in range(workers)]
        self.ids = itertools.count(1)
        self.served = self.fallbacks = 0
        self.busy_seconds = 0.0
        self.watch_source = frontend_reload.enabled()
        self.update_generation = 0

    def reload_code(self) -> None:
        """New renders replace their worker once its current request has finished."""
        self.update_generation += 1

    def _source_revision(self) -> tuple[int, bytes | None]:
        digest = None
        if self.watch_source:
            try:
                digest = hashlib.sha256(WORKER_SOURCE.read_bytes()).digest()
            except OSError:  # an editor may briefly remove the file while replacing it
                raise Unavailable("the render worker source isn't available") from None
        return self.update_generation, digest

    def _pick(self) -> _Worker:
        # The first idle one, else the first (its lock queues the request).
        for worker in self.workers:
            if not worker.lock.locked():
                return worker
        return self.workers[0]

    def render(self, slug: str, folder: Path, *, full: bool, names=(), headers: bool = False, readme_key=None):
        """(files, counts) as saves._render / _render_partial return them (write_readme=False)."""
        from ..network import NetworkError
        request = {"id": next(self.ids), "slug": slug, "folder": str(folder), "full": full, "names": list(names),
                   "headers": headers, "readme_key": list(readme_key) if readme_key is not None else None}
        began = time.monotonic()
        try:
            reply = self._pick().ask(request, self._source_revision)
        except Unavailable:
            self.fallbacks += 1
            raise
        if not reply.get("ok"):
            kind, message = reply.get("type", ""), reply.get("message", "")
            if kind == "NetworkError":
                raise NetworkError(message)
            if kind == "OSError":
                raise OSError(message)
            if kind == "UnicodeError":
                raise UnicodeError(message)
            if kind == "JSONDecodeError":
                raise json.JSONDecodeError(message, "", 0)
            self.fallbacks += 1
            raise Unavailable(f"{kind}: {message}")  # (anything else: the app's own render reports it)
        self.served += 1
        self.busy_seconds += time.monotonic() - began
        return reply["files"], reply["counts"]

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()


def start(net, internal: Path) -> Renderers | None:
    workers = count()
    return Renderers(net, internal, workers) if workers else None
