"""Read-only MCP tools in a process of their own, so they answer while the app is busy.

Every tool call used to run in the app's process, beside the window, saves, sweeps and every other helper's
compiles, all under one interpreter lock: a quick read (get_claims, list_functions) waited for whatever held it.
This process opens the same databases (store.ATTACH_ONLY: no schema work, no writer gate) and runs the ordinary
tool handlers for a few read-only tools. The MCP gateway (gateway/, docs/MCP_GATEWAY.md) sends those calls here
first and to the app when this process declines, isn't running or fails.

It answers only what it can answer exactly as the app would:
- tools and arguments in READER_TOOLS whose output comes from the databases and files alone (not from what the
  app keeps in memory: last diffs, search evidence, selected candidates);
- helpers the app says it can serve (`routes`, sent by the app every second over standard input: which project
  each helper works, and the helpers with app-only state: a pending connection handshake, an asset or boundary
  investigation, a used-up session budget), in projects the app isn't rechecking, importing or sharing in a room;
- never the saved progress folders: it reads what the app has synchronized into the database (ReaderProgress).
Anything else, and any unexpected error, is DECLINED and the gateway asks the app. What the app keeps for each
call (the transcript, the session budget, tool usage, project activity) it learns from a report on standard output.

It answers a few of the app window's read-only pages too (app/window_reads.py), which the gateway's window listener
sends here first (gateway/src/window.rs). It checks each request as the app's guard does, with the window's
per-launch secret (given in its environment, never on its command line), and declines whatever it can't: a request
without the secret (a browser signed in to `decomp serve` included), with an Origin, while the app prepares a
runtime update, or for a project the app is rechecking or sharing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from ..hostenv import NO_WINDOW
from . import frontend_reload

log = logging.getLogger("decompdecomp")

DECLINED = "decompdecomp-reader: declined"  # the gateway asks the app instead (gateway/src/main.rs)
READER_TOOLS = frozenset({
    "get_claims", "resolve_addresses", "get_context", "list_functions", "search_notes", "project_info", "get_function",
    "read_data", "get_class", "guess_struct", "get_boundary_evidence", "read_file", "search_code", "file_status",
    "standins_for",
})
# get_function's sections that read only the databases (not the agent's selected candidate, compile evidence or
# warnings the app keeps in memory, and no machine draft written as a side effect).
FUNCTION_SECTIONS = frozenset({"summary", "draft", "matched", "history", "quality"})
SNAPSHOT_SECONDS = 1.0
STALE_SECONDS = 5.0  # routes older than this: the app may have stalled with a change unsent, so decline
START_SECONDS = 60.0
# The revisions (store.read_version) the engine's own per-project caches (_syms, _xrefs, _roms) are built from.
CACHE_REVISIONS = ("scope_symbols", "naming", "fingerprints", "targets")
WINDOW_ENV = "DECOMP_READER_WINDOW"  # the window's secret and the app's port, for the child
WINDOW_ANSWERED = "x-decomp-reader"  # on a window read the reader answered (gateway/src/window.rs ANSWERED)
DRAIN_SECONDS = 5.0
SOURCE_CHECK_SECONDS = 0.5
WORKER_SOURCE = Path(__file__).with_name("tool_reader_worker.py")


def enabled() -> bool:
    return os.environ.get("DECOMP_TOOL_READER", "").strip().lower() not in ("0", "off", "false", "no")


def allowed(name: str, args: dict) -> bool:
    """A tool and arguments this process answers as the app would."""
    if name not in READER_TOOLS:
        return False
    if name == "project_info":
        return not args.get("detail", False)  # detail=true of a fork starts its preflight build
    if name == "get_function":
        return args.get("section", "summary") in FUNCTION_SECTIONS
    return True


# ---------------------------------------------------------------------------------------------------- the app's side
class Reader:
    """The running reader process, restarted on the same port if it ends while the app runs."""

    def __init__(self, net, internal: Path, window: dict | None = None):
        self.net, self.internal = net, Path(internal)
        # The window's secret and the app's port, to answer the window's reads (None: tools only).
        self.window = window
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self.stopping = threading.Event()
        self.lock = threading.Lock()  # held while the process starts (up to START_SECONDS)
        self.pipe = threading.Lock()  # held only to write routes: a helper starting never waits for a restart
        self.routes = None  # () -> the routing snapshot, once the app's agent manager exists (attach)
        self.report = None  # (message) -> None
        self.restarts = 0
        self.served = 0
        self.watch_source = frontend_reload.enabled()
        self.requested_revision = None
        self.loaded_revision = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def window_url(self) -> str | None:
        """Where the gateway sends the window's reads (window_reads.py), or None when this reader doesn't answer them."""
        return f"http://127.0.0.1:{self.port}" if self.window else None

    def _launch(self, port: int) -> None:
        log_path = self.internal / "logs" / "tool-reader.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.pop(WINDOW_ENV, None)
        if self.window:  # (the environment, not the command line, which other users' `ps` shows)
            env[WINDOW_ENV] = json.dumps(self.window)
        with log_path.open("ab") as errors:
            process = subprocess.Popen(
                [sys.executable, str(WORKER_SOURCE), str(self.net.store.path),
                 str(self.net.workspace), str(port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, bufsize=0, creationflags=NO_WINDOW,
                env=env)
        found: list[bytes] = []
        reader = threading.Thread(target=lambda: found.append(process.stdout.readline()), daemon=True,
                                  name="tool-reader-start")
        reader.start()
        reader.join(START_SECONDS)
        line = found[0].decode("ascii", "replace").strip() if found else ""
        if line != f"LISTENING {port}":
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"the tool reader didn't start (exit {process.poll()}, said {line!r})")
        self.process = process
        threading.Thread(target=self._read, args=(process,), daemon=True, name="tool-reader-reports").start()

    def start(self) -> str:
        """Its address at once; the process opens the databases in the background (until it listens, the gateway's
        connections are refused and the app answers)."""
        with socket.socket() as probe:  # a free port, kept for the gateway across restarts
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        threading.Thread(target=self._watch, daemon=True, name="tool-reader-watch").start()
        threading.Thread(target=self._push, daemon=True, name="tool-reader-routes").start()
        return self.url

    def attach(self, routes, report) -> None:
        self.routes, self.report = routes, report
        self._send_routes()

    def changed(self) -> None:
        """A helper started or stopped: tell the reader now rather than within the second."""
        self._send_routes()

    def _send_routes(self) -> None:
        if self.routes is None:
            return
        try:
            snapshot = self.routes()
        except Exception:  # never let a snapshot problem stop the app; the reader declines once routes go stale
            log.exception("Tool reader routes failed")
            return
        with self.pipe:
            process = self.process
            if process is None or process.poll() is not None or not process.stdin or process.stdin.closed:
                return
            try:
                process.stdin.write((json.dumps({"routes": snapshot}) + "\n").encode())
                process.stdin.flush()
            except OSError:
                pass

    def _push(self) -> None:
        while not self.stopping.wait(SNAPSHOT_SECONDS):
            self._send_routes()

    def _read(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            self.served += 1
            if self.report is not None:
                try:
                    self.report(message)
                except Exception:
                    log.exception("Tool reader report failed")

    @staticmethod
    def _source_revision() -> bytes:
        return hashlib.sha256(WORKER_SOURCE.read_bytes()).digest()

    def reload_code(self) -> bool:
        """Wait for the updated worker on the existing port, even with local watching disabled."""
        revision = self._source_revision()
        self.requested_revision = revision
        deadline = time.monotonic() + START_SECONDS + DRAIN_SECONDS + 10
        while time.monotonic() < deadline:
            process = self.process
            if process is not None and process.poll() is None and self.loaded_revision == revision:
                return True
            if self.stopping.wait(0.1):
                break
        return False

    def _code_changed(self, revision: bytes) -> bool:
        if self.watch_source:
            try:
                return self._source_revision() != revision
            except OSError:  # an editor may briefly remove the file while replacing it
                return False
        return self.requested_revision is not None and self.requested_revision != revision

    def _drain(self, process: subprocess.Popen) -> None:
        try:
            with self.pipe:
                process.stdin.write(b'{"drain":true}\n')
                process.stdin.flush()
            process.wait(timeout=DRAIN_SECONDS + 2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()

    def _watch(self) -> None:
        """Start the process, and again on the same port whenever it ends while the app runs."""
        pause, first = 0.5, True
        while not self.stopping.is_set():
            try:
                with self.lock:
                    if self.stopping.is_set():
                        return
                    revision = self._source_revision()
                    self.loaded_revision = None
                    self._launch(self.port)
                    self.loaded_revision = revision
                    if self.requested_revision == revision:
                        self.requested_revision = None
            except (OSError, RuntimeError, ValueError) as error:
                log.warning("Tool reader didn't start: %s", error)
                if self.stopping.wait(pause):
                    return
                pause = min(pause * 2, 60.0)
                continue
            if first:
                log.info("Tool reader listening on %s", self.url)
            else:
                self.restarts += 1
            first, began = False, time.monotonic()
            process = self.process
            self._send_routes()
            while not self.stopping.is_set():
                try:
                    code = process.wait(timeout=SOURCE_CHECK_SECONDS)
                except subprocess.TimeoutExpired:
                    if self.stopping.is_set() or not self._code_changed(revision):
                        continue
                    log.info("Tool reader source changed; restarting it on port %s", self.port)
                    self._drain(process)
                    break
                if self.stopping.is_set():
                    return
                # Back off when a worker repeatedly exits shortly after startup.
                pause = 0.5 if time.monotonic() - began > 60 else min(pause * 2, 60.0)
                log.warning("Tool reader ended (exit %s); starting it again on port %s", code, self.port)
                self.stopping.wait(pause)
                break

    def stop(self) -> None:
        self.stopping.set()
        with self.lock:
            process = self.process
        if process is None:
            return
        try:
            with self.pipe:
                if process.stdin:
                    process.stdin.close()  # the reader ends on end-of-file
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def start(net, internal: Path, window: dict | None = None) -> Reader | None:
    """The reader in front of nothing yet (the gateway learns its address), or None when it shouldn't run.
    `window` ({"secret", "port"}: the app window's) lets it answer the window's reads too."""
    if not enabled():
        return None
    reader = Reader(net, internal, window)
    try:
        reader.start()
    except (OSError, RuntimeError, ValueError) as error:
        log.warning("Tool reader unavailable, the app answers every tool: %s", error)
        return None
    return reader


def routes(net, manager, settings, jobs=None) -> dict:
    """What the reader needs from the app's memory to answer as the app would (see the module docstring)."""
    from . import manual_mcp
    from .session_budget import Budget
    progress = getattr(net, "progress", None)
    busy = set(getattr(progress, "rechecking", {}) or {}) | set(net.links) | set(net.rooms)
    budgets = getattr(net, "_session_budgets", {})
    spent = [agent for (agent, _slug), budget in list(budgets.items())
             if isinstance(budget, Budget) and (budget.calls >= budget.tool_limit or budget.chars >= budget.char_limit)]
    assignments = {str(agent): slug for agent, slug in list(manager.assignments.items())}
    manual = {str(item["id"]): item["slug"] for item in manual_mcp.connections(settings)}
    try:
        pairings = {str(r["agent_id"]): r["project"]
                    for r in net.store.all("SELECT agent_id,project FROM pairings WHERE state='approved'")}
    except Exception:  # (no pairing yet: the table comes with the first)
        pairings = {}
    return {
        "at": time.time(),
        "catalog": str(net.store.path),
        "assignments": assignments,
        "pairings": pairings,
        "manual": manual,
        "projects": _projects(net, {*assignments.values(), *pairings.values(), *manual.values()}),
        "app_only": sorted({agent for agent, _slug in getattr(net, "_pending_tool_handshakes", set())}
                           | set(getattr(net, "_asset_investigators", {}))
                           | set(getattr(net, "_boundary_investigators", {})) | set(spent)),
        "tasks": {str(agent): task for agent, task in getattr(net, "_agent_tool_tasks", {}).items()},
        "busy": sorted(busy),
        "hidden": _hidden(net, {**assignments, **pairings, **manual}),
        # (agent, function) with a kept compile diff: get_assembly's default view starts at its first mismatch, which
        # only the app knows.
        "diffs": sorted({(agent, function) for agent, function in list(getattr(net, "_last_diffs", {}))}),
        # The app's guard refuses every page while it prepares a runtime update (server.build guard).
        "preparing": bool(getattr(jobs, "source_update_preparing", False)),
        # Each helper's next claim, for the gateway to hand over at once (app/claim_ahead.py).
        "prepared": ahead.routes() if (ahead := getattr(net, "_claims_ahead", None)) is not None else {},
    }


# The tools the gateway answers itself (gateway/src/native.rs TOOLS).
GATEWAY_TOOLS = ("get_claims", "list_functions", "project_info", "get_assembly", "get_context", "guide", "claim_function")


def _hidden(net, helpers: dict) -> dict:
    """The gateway's tools each helper's tool settings disable (tool_policy.visible): it leaves those to the app,
    which says so."""
    from ..network import Agent
    from .tool_policy import visible
    out = {}
    for agent_id, slug in helpers.items():
        try:
            row = net.store.one("SELECT id,name,owner,trusted FROM agents WHERE id=?", int(agent_id))
            if row is None:
                continue
            agent = Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))
            shown = set(visible(net, slug, list(GATEWAY_TOOLS), agent))
        except Exception:
            shown = set()
        hidden = [name for name in GATEWAY_TOOLS if name not in shown]
        if hidden:
            out[str(agent_id)] = hidden
    return out


def guides(platforms) -> dict:
    """What `guide` answers, for the gateway (native.rs): every topic's text for each platform, the listing, and the
    tools' extended help. They change only with the app, so the gateway is sent them once per platform."""
    from . import tools
    topics = sorted({*tools.GUIDES, *tools._console_guides()})
    return {"tool_help": dict(tools.TOOL_HELP),
            "missing_tool_help": tools.guide_text("tool:\x00"),
            "platforms": {platform: {"listing": tools.guide_text("", platform),
                                     "topics": {topic: tools.guide_text(topic, platform) for topic in topics}}
                          for platform in sorted(platforms)}}


class WatchedSet(set):
    """A set that tells the reader when it changes (the app's pending connection handshakes)."""

    def __init__(self, items, changed):
        super().__init__(items)
        self.changed = changed

    def add(self, item):
        super().add(item)
        self.changed()

    def discard(self, item):
        super().discard(item)
        self.changed()

    def remove(self, item):
        super().remove(item)
        self.changed()


class WatchedDict(dict):
    """A dict that tells the reader when it changes (the app's investigators)."""

    def __init__(self, items, changed):
        super().__init__(items)
        self.changed = changed

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.changed()

    def __delitem__(self, key):
        super().__delitem__(key)
        self.changed()

    def pop(self, *args):
        value = super().pop(*args)
        self.changed()
        return value

    def setdefault(self, key, default=None):
        value = super().setdefault(key, default)
        self.changed()
        return value


def watch(net, changed) -> None:
    """The reader hears at once when a helper gains state only the app has: a connection handshake is set just
    before its CLI starts, sooner than the next routes."""
    net.__dict__["_pending_tool_handshakes"] = WatchedSet(getattr(net, "_pending_tool_handshakes", ()), changed)
    for name in ("_asset_investigators", "_boundary_investigators"):
        net.__dict__[name] = WatchedDict(getattr(net, name, {}), changed)


def _projects(net, slugs) -> dict:
    """For the gateway's own answers (gateway/src/native.rs): each active project's database and the saved parts of
    its compact project_info. Read without the engine's project lookup, which would recheck saved progress."""
    from ..network import CLAIM_ORDERS, Network
    from .tools import COMPACT_NEXT, compact_info_head, compact_info_steering
    out = {}
    for slug in slugs:
        try:
            proj = Network._project(net, slug)
            cfg = net.cfg(proj)
            # (net.instructions and net.claim_order read these same settings)
            order = cfg.get("claim_order") or ""
            tail = COMPACT_NEXT + compact_info_steering(cfg.get("instructions") or "",
                                                        order if order in CLAIM_ORDERS else "")
            out[slug] = {"id": proj["id"], "db": str(net.project_store(proj["id"]).path),
                         "fork": cfg.get("kind") == "fork", "info_head": compact_info_head(net, proj),
                         "info_tail": tail}
            try:  # (guide's platform: the overview's, its console's id)
                out[slug]["platform"] = net.console(proj).id
            except Exception:
                pass
        except Exception:  # a project removed meanwhile: the app answers its helpers
            continue
    return out


def apply_report(net, manager, message: dict) -> None:
    """Keep what the app keeps for a call the reader served: transcript, session budget, tool usage, activity."""
    if isinstance(message.get("viewed"), str):
        # A project page the reader answered for the window: the project was viewed, as the app's with_project
        # notes (project_activity.py).
        try:
            net.project_activity.touch(message["viewed"])
        except Exception:
            pass
        return
    call = message.get("call")
    if not isinstance(call, dict):
        return
    agent = SimpleNamespace(id=int(call["agent_id"]), name=str(call["agent_name"]))
    slug, name = str(call["slug"]), str(call["name"])
    from . import session_budget
    from .tool_policy import record
    try:
        record(net, agent, slug, name)
    except Exception:  # (a tool disabled meanwhile: the call already ran)
        pass
    budget = session_budget.current(net, agent, slug)
    if budget:
        with budget.lock:  # begin() would refuse a call that has already run
            budget.calls += 1
            budget.chars += len(call.get("content") or "")
    try:
        net.project_activity.touch(slug)
    except Exception:
        pass
    timings = dict(call.get("timings") or {})
    if not timings.get("gateway"):
        timings["reader"] = True
    manager.record_agent(agent, slug, "tool_request", {"name": name, "arguments": call.get("arguments") or {},
                                                       "call_id": call["call_id"]})
    payload = {"name": name, "call_id": call["call_id"], "timings": timings}
    if "error" in call:
        payload["error"] = call["error"]
    else:
        payload["content"] = call.get("content")
    manager.record_agent(agent, slug, "tool_response", payload)


def drop_stale_caches(net, slug: str, seen: dict, lock: threading.Lock) -> None:
    """In a process beside the app (this reader, render_worker.py): drop the engine's per-project caches the app's
    own edits would have dropped (renames, splits, imports), as the database's revisions show them."""
    project = net.project_row(slug)
    store = net.project_store(project["id"])
    now = (project["config"],) + tuple(store.read_version(kind) for kind in CACHE_REVISIONS)
    with lock:
        if seen.get(project["id"]) == now:
            return
        seen[project["id"]] = now
        for cache in ("_syms", "_xrefs", "_roms", "_sym_override", "_asm_names", "_generation",
                      "_rename_generation"):
            getattr(net, cache, {}).pop(project["id"], None)


def reader_progress(net):
    """The child never synchronizes saved progress: it reads what the app has put in the database."""
    from .progress import Progress

    class ReaderProgress(Progress):
        @property
        def viewing(self) -> bool:
            return True

        def sync(self, *args, **kwargs) -> bool:
            return False

        def deferring(self):
            return nullcontext()

        def foreground(self):
            return nullcontext()

    net.progress.__class__ = ReaderProgress
