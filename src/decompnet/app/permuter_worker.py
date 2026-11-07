"""Run background permutation in a replaceable process, away from the GUI and agents.

The child opens the same project database and uses the ordinary Permuter and judge.
Only control and progress cross the private stdio pipe. A code edit restarts this
worker; the app stays up, and claims left by an interrupted turn are released.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from .. import permuter_claims
from ..hostenv import NO_WINDOW
from ..network import NetworkError
from . import edition, local_claims, workers

log = logging.getLogger("decompdecomp")
WATCHED = ("permuter_claims.py", "app/permute.py", "app/local_claims.py", "app/permuter_initialization.py", "app/permuter_local.py",
           "app/permuter_macro_effects.py", "permuter_gen.py", "permuter_capture.py", "permuter_formatting.py")
POLL_SECONDS = 1
STOP_SECONDS = 5


def empty_status(state):
    return {"state": state, "tried": 0, "variants": 0, "improved": 0, "matched": 0,
            "current": "", "started_at": time.time(), "repairs": 0, "threads": max(1, workers.WORKERS - 1)}


def revision() -> tuple:
    root = Path(__file__).resolve().parents[1]
    result = []
    for path in sorted(root / name for name in WATCHED):
        name = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
            result.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((name, None, None))
    return tuple(result)


class PermuterWorker:
    """The app-side handle. Only the child imports changed permuter code after startup."""

    def __init__(self, net):
        self.net = net
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._update_mutex = threading.Lock()
        self._paused = False
        self._statuses: dict[str, dict] = {}
        self.jobs: dict[str, SimpleNamespace] = {}
        self._running: set[str] = set()
        self._replies: dict[int, queue.Queue] = {}
        self._next_id = 0
        self._settings: dict = {}
        self.max_insns = 400
        self.seconds = 60
        self.max_turns = 4
        self._revision = revision()
        self._closed = threading.Event()
        self._watch_started = False

    def configure(self, settings: dict) -> None:
        self._settings = dict(settings)
        self.max_insns = max(1, min(100000, int(settings.get("permuter_max_insns", 400))))
        self.seconds = max(1, min(3600, int(settings.get("permuter_seconds", 60))))
        self.max_turns = max(1, min(1000, int(settings.get("permuter_max_turns", 4))))
        with self._lock:
            if self._process is not None:
                self._send("configure", settings=self._settings)

    def _hold(self, agent, fn, *, count_turn=True, seconds=None) -> bool:
        """Permutation is nonexclusive; other parent-side checks retain ordinary leases."""
        return permuter_claims.hold(self.net, agent, fn, self.seconds if seconds is None else seconds,
                                 count_turn=count_turn)

    def _let_go(self, agent, fn) -> None:
        local_claims.release(self.net, agent, fn)

    def parent_threads(self, configured) -> int:
        """The app keeps one worker while the child owns the rest of the CPU budget."""
        total = workers.clamp(configured)
        return 1 if self._process is not None else total

    def status(self, slug: str) -> dict | None:
        with self._lock:
            if slug in self._statuses:
                return dict(self._statuses[slug])
            if slug in self.jobs:
                state = self.jobs[slug]
                return {name: getattr(state, name) for name in (
                    "state", "tried", "variants", "improved", "matched", "current", "started_at", "repairs"
                )} | {"threads": workers.WORKERS}
            return None

    def start(self, slug: str):
        if slug in self.net.links:
            return None  # A room member receives its host's matches.
        with self._lock:
            self._running.add(slug)
            if self._paused:
                self._statuses[slug] = empty_status("update_wait")
                self.jobs[slug] = SimpleNamespace(state="update_wait")
                return self.jobs[slug]
            self._ensure()
            self._statuses.setdefault(slug, empty_status("running"))
            self.jobs[slug] = SimpleNamespace(state="running")
            self._send("start", slug=slug)
            return self.jobs[slug]

    def stop(self, slug: str) -> None:
        with self._lock:
            self._running.discard(slug)
            if self._process is not None and self._process.poll() is None:
                self._send("stop", slug=slug)

    def repair_pass(self, slug: str, stop: threading.Event):
        """The cross-game pass waits for its result, while cancellation remains responsive."""
        if slug in self.net.links or stop.is_set():
            return SimpleNamespace(matched=0)
        while self._paused:
            if stop.wait(.1):
                return SimpleNamespace(matched=0)
            if self._closed.is_set():
                raise NetworkError("The permuter worker stopped. Run cross-game checks again.")
        with self._lock:
            self._ensure()
            self._next_id += 1
            ident = self._next_id
            reply = self._replies[ident] = queue.Queue(maxsize=1)
            self._send("repair_pass", slug=slug, ident=ident)
        cancelled = False
        try:
            while not self._closed.is_set():
                try:
                    result = reply.get(timeout=.2)
                    if "error" in result:
                        raise NetworkError("The permuter worker failed. Check its log and run cross-game checks again.")
                    return SimpleNamespace(matched=result.get("matched", 0))
                except queue.Empty:
                    if stop.is_set() and not cancelled:
                        self._send("cancel", ident=ident)
                        cancelled = True
                    if self._process is None or self._process.poll() is not None:
                        raise NetworkError("The permuter worker stopped. Run cross-game checks again.")
            raise NetworkError("The permuter worker stopped. Run cross-game checks again.")
        finally:
            with self._lock:
                self._replies.pop(ident, None)

    def _ensure(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._release_claims()
        workers.configure(1)
        db = self.net.store.path
        home = str(self.net.workspace)
        log_path = Path(home) / ".decomp" / "logs" / "permuter-worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as errors:
            process = subprocess.Popen([sys.executable, "-m", "decompnet.app.permuter_worker", "--child", db, home],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                       bufsize=0, creationflags=NO_WINDOW)
        self._process = process
        if edition.DEV and not self._watch_started:
            self._watch_started = True
            threading.Thread(target=self._watch, daemon=True, name="permuter-code-watch").start()
        threading.Thread(target=self._read, args=(process,), daemon=True, name="permuter-worker-output").start()
        self._send("configure", settings=self._settings)
        self._send("links", slugs=list(self.net.links))

    def _send(self, action: str, **payload) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise NetworkError("The permuter worker stopped. Start it again from the project page.")
            try:
                process.stdin.write((json.dumps({"action": action, **payload}) + "\n").encode())
                process.stdin.flush()
            except OSError as error:
                raise NetworkError("The permuter worker stopped. Start it again from the project page.") from error

    def _read(self, process: subprocess.Popen) -> None:
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self._lock:
                    if process is not self._process:
                        return
                    if message.get("kind") == "status":
                        self._statuses = message["jobs"]
                        self.jobs = {slug: SimpleNamespace(state=value["state"])
                                     for slug, value in self._statuses.items()}
                        self._running = {slug for slug, state in self._statuses.items()
                                         if state["state"] in ("running", "update_wait")}
                    elif message.get("kind") == "reply":
                        reply = self._replies.get(message.get("ident"))
                        if reply is not None:
                            reply.put_nowait(message)
        finally:
            with self._lock:
                if process is self._process:
                    self._process = None
                    self._release_claims()
                    for slug in self._running:
                        self._statuses.setdefault(slug, {})["state"] = "stopped"
                        self.jobs.setdefault(slug, SimpleNamespace()).state = "stopped"
                    workers.configure(self._settings.get("worker_threads"))

    def _release_claims(self) -> None:
        # An interrupted child cannot run Permuter._let_go. Only its local
        # permuter identity is cleared; agent and other background claims remain.
        agent = self.net.store.one("SELECT id FROM agents WHERE name='permuter' AND origin='local'")
        if agent:
            with self.net.store.tx() as db:
                db.execute("DELETE FROM claims WHERE agent_id=?", (agent["id"],))

    def _restart(self) -> None:
        with self._update_mutex:
            with self._lock:
                if self._paused or self._closed.is_set() or revision() == self._revision:
                    return
                running = set(self._running)
            self._stop_process()
            with self._lock:
                self._statuses.clear()
                self.jobs.clear()
                if running:
                    self._ensure()
                    for slug in running:
                        if slug not in self.net.links:
                            self._send("start", slug=slug)

    @contextmanager
    def code_update(self):
        """Stop the child before its Python files change; restart it after the pull."""
        with self._update_mutex:
            with self._lock:
                if self._replies:
                    raise NetworkError("A cross-game repair pass is using the permuter. Try the update again shortly.")
                self._paused = True
                for slug in self._running:
                    self._statuses.setdefault(slug, {})["state"] = "update_wait"
                    self.jobs.setdefault(slug, SimpleNamespace()).state = "update_wait"
            try:
                self._stop_process()
                yield
            finally:
                with self._lock:
                    self._revision = revision()
                    self._paused = False
                    if self._running and not self._closed.is_set():
                        self._ensure()
                        for slug in self._running:
                            if slug not in self.net.links:
                                self._send("start", slug=slug)

    def _stop_process(self) -> None:
        with self._lock:
            process = self._process
        if process is None:
            return
        try:
            self._send("shutdown")
            process.wait(timeout=STOP_SECONDS)
        except (OSError, subprocess.TimeoutExpired, NetworkError):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            with self._lock:
                self._process = None
                self._release_claims()
            workers.configure(self._settings.get("worker_threads"))

    def _watch(self) -> None:
        last_links = None
        while not self._closed.wait(POLL_SECONDS):
            current = revision()
            if current != self._revision:
                log.info("permuter code changed; replacing only its worker")
                self._restart()
                self._revision = revision()
            links = tuple(sorted(self.net.links))
            if links != last_links:
                last_links = links
                with self._lock:
                    if self._process is not None:
                        try:
                            self._send("links", slugs=links)
                        except NetworkError:
                            pass

    def close(self) -> None:
        self._closed.set()
        self._stop_process()


def child(db_path: str, workspace: str) -> None:
    """Own one engine and its permuter; stdout carries only framed JSON replies."""
    from .engine import RomNetwork
    from .permute import Permuter

    net = RomNetwork(db_path, Path(workspace))
    permuter = net.permuter = Permuter(net)
    output_lock = threading.Lock()
    repair_stops: dict[int, threading.Event] = {}
    stopped = threading.Event()

    def emit(message):
        with output_lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def snapshot():
        emit({"kind": "status", "jobs": {slug: permuter.status(slug) for slug in list(permuter.jobs)}})

    def heartbeat():
        while not stopped.wait(POLL_SECONDS):
            snapshot()

    threading.Thread(target=heartbeat, daemon=True, name="permuter-status").start()

    def repair(ident, slug, stop):
        try:
            result = permuter.repair_pass(slug, stop)
            emit({"kind": "reply", "ident": ident, "matched": result.matched})
        except Exception:
            log.exception("permuter worker repair pass failed")
            emit({"kind": "reply", "ident": ident, "error": "repair failed"})
        finally:
            repair_stops.pop(ident, None)

    try:
        for line in sys.stdin:
            message = json.loads(line)
            action = message["action"]
            if action == "shutdown":
                break
            if action == "configure":
                permuter.configure(message["settings"])
                workers.configure(max(1, workers.clamp(message["settings"].get("worker_threads")) - 1))
            elif action == "links":
                linked = set(message["slugs"])
                for slug in linked:
                    permuter.stop(slug)
                net.links = {slug: object() for slug in linked}
            elif action == "start":
                permuter.start(message["slug"])
            elif action == "stop":
                permuter.stop(message["slug"])
            elif action == "repair_pass":
                ident = message["ident"]
                stop = repair_stops[ident] = threading.Event()
                threading.Thread(target=repair, args=(ident, message["slug"], stop), daemon=True).start()
            elif action == "cancel":
                if stop := repair_stops.get(message["ident"]):
                    stop.set()
            snapshot()
    finally:
        stopped.set()
        for state in list(permuter.jobs.values()):
            state.stop.set()
        for stop in list(repair_stops.values()):
            stop.set()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--child":
    child(sys.argv[2], sys.argv[3])
