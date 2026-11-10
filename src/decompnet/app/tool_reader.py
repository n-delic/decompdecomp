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
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from ..hostenv import NO_WINDOW

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

    def __init__(self, net, internal: Path):
        self.net, self.internal = net, Path(internal)
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self.stopping = threading.Event()
        self.lock = threading.Lock()  # held while the process starts (up to START_SECONDS)
        self.pipe = threading.Lock()  # held only to write routes: a helper starting never waits for a restart
        self.routes = None  # () -> the routing snapshot, once the app's agent manager exists (attach)
        self.report = None  # (message) -> None
        self.restarts = 0
        self.served = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _launch(self, port: int) -> None:
        log_path = self.internal / "logs" / "tool-reader.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as errors:
            process = subprocess.Popen(
                [sys.executable, "-m", "decompnet.app.tool_reader", "--child", str(self.net.store.path),
                 str(self.net.workspace), str(port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, bufsize=0, creationflags=NO_WINDOW)
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
            if process is None or process.poll() is not None or process.stdin is None:
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

    def _watch(self) -> None:
        """Start the process, and again on the same port whenever it ends while the app runs."""
        pause, first = 0.5, True
        while not self.stopping.is_set():
            try:
                with self.lock:
                    if self.stopping.is_set():
                        return
                    self._launch(self.port)
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
            code = self.process.wait()
            if self.stopping.is_set():
                return
            # One that keeps ending soon after it starts is started less and less often.
            pause = 0.5 if time.monotonic() - began > 60 else min(pause * 2, 60.0)
            log.warning("Tool reader ended (exit %s); starting it again on port %s", code, self.port)
            if self.stopping.wait(pause):
                return

    def stop(self) -> None:
        self.stopping.set()
        with self.lock:
            process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()  # the reader ends on end-of-file
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def start(net, internal: Path) -> Reader | None:
    """The reader in front of nothing yet (the gateway learns its address), or None when it shouldn't run."""
    if not enabled():
        return None
    reader = Reader(net, internal)
    try:
        reader.start()
    except (OSError, RuntimeError, ValueError) as error:
        log.warning("Tool reader unavailable, the app answers every tool: %s", error)
        return None
    return reader


def routes(net, manager, settings) -> dict:
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
    }


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
        except Exception:  # a project removed meanwhile: the app answers its helpers
            continue
    return out


def apply_report(net, manager, message: dict) -> None:
    """Keep what the app keeps for a call the reader served: transcript, session budget, tool usage, activity."""
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


# -------------------------------------------------------------------------------------------------- the child's side
class Declined(Exception):
    pass


class Service:
    """The child's engine and what the app last said about its helpers."""

    def __init__(self, net, emit):
        self.net, self.emit = net, emit
        self.routes: dict = {}
        self.revisions: dict[int, tuple] = {}
        self.revisions_lock = threading.Lock()

    def project_for(self, agent_id: int) -> str:
        routes = self.routes
        if not routes or time.time() - float(routes.get("at", 0)) > STALE_SECONDS:
            raise Declined("no current routes from the app")
        if agent_id in set(routes.get("app_only", ())):
            raise Declined("the app keeps state for this helper")
        key = str(agent_id)
        slug = (routes.get("assignments", {}).get(key) or self.net.pairing_project(agent_id)
                or routes.get("manual", {}).get(key))
        if not slug:
            raise Declined("no project: the app explains")
        if slug in set(routes.get("busy", ())):
            raise Declined("the app is rechecking, importing or sharing this project")
        return slug

    def fresh(self, slug: str) -> None:
        """Drop the engine's per-project caches the app's own edits would have dropped (renames, splits, imports)."""
        project = self.net.project_row(slug)
        store = self.net.project_store(project["id"])
        now = (project["config"],) + tuple(store.read_version(kind) for kind in CACHE_REVISIONS)
        with self.revisions_lock:
            if self.revisions.get(project["id"]) == now:
                return
            self.revisions[project["id"]] = now
            for cache in ("_syms", "_xrefs", "_roms", "_sym_override", "_asm_names", "_generation",
                          "_rename_generation"):
                getattr(self.net, cache, {}).pop(project["id"], None)

    def call(self, headers: dict, name: str, args: dict) -> str:
        from ..network import NetworkError
        from ..read_cache import ReadPending
        from . import tools
        from .tool_policy import visible
        if not allowed(name, args):
            raise Declined(f"{name} is answered by the app")
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        try:
            agent = self.net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        except NetworkError:
            raise Declined("not a helper this process knows") from None
        slug = self.project_for(agent.id)
        started = time.monotonic()
        call_id = uuid.uuid4().hex
        report = {"agent_id": agent.id, "agent_name": agent.name, "slug": slug, "name": name, "arguments": args,
                  "call_id": call_id}
        try:
            self.fresh(slug)
            if name not in visible(self.net, slug, list(tools.HANDLERS), agent):
                raise Declined("disabled for this helper: the app explains")
            named = tools._named(self.net, agent, slug, args, name)
            result = tools.HANDLERS[name](self.net, agent, slug, **named)
            if name == "get_function" and args.get("function") != named.get("function"):
                result = (f"Resolved read {str(args.get('function'))[:200]!r} to {named['function']}. This does not "
                          "rename or create a function.\n" + result)
        except (NetworkError, ReadPending) as error:
            report.update(error=str(error), timings={"dispatch_ms": round((time.monotonic() - started) * 1000, 2)})
            self.emit({"call": report})
            raise
        report.update(content=result, timings={"dispatch_ms": round((time.monotonic() - started) * 1000, 2)})
        self.emit({"call": report})
        return result


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


def child(db_path: str, workspace: str, port: int) -> None:
    import uvicorn

    from .. import store as storage
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s [tool-reader pid=%(process)d %(threadName)s] %(message)s")
    for chatty in ("mcp", "httpx", "uvicorn"):  # a line per request otherwise
        logging.getLogger(chatty).setLevel(logging.WARNING)
    storage.ATTACH_ONLY = True
    os.environ.setdefault("DECOMP_REPAIR_ANALYSIS", "off")
    from ..network import NetworkError
    from ..read_cache import ReadPending
    from . import tools
    from .engine import RomNetwork
    from .workers import lower_priority
    lower_priority()  # the app's own work comes first
    net = RomNetwork(db_path, Path(workspace))
    reader_progress(net)
    output = threading.Lock()

    def emit(message):
        with output:
            sys.stdout.write(json.dumps(message, default=str) + "\n")
            sys.stdout.flush()

    service = Service(net, emit)

    def listen():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message.get("routes"), dict):
                routes = message["routes"]
                routes["app_only"] = {int(a) for a in routes.get("app_only", ())}
                net._agent_tool_tasks = {int(a): t for a, t in routes.get("tasks", {}).items()}
                service.routes = routes
        os._exit(0)  # the app closed our standard input: it ended

    from mcp.server.mcpserver.exceptions import ToolError

    def run(ctx, name, args):
        headers = dict(ctx.headers or {})
        try:
            return service.call(headers, name, args)
        except Declined as why:
            log.info("declined %s: %s", name, why)
            raise ToolError(DECLINED) from None
        except (NetworkError, ReadPending) as error:
            raise ToolError(str(error)) from None
        except Exception:
            log.exception("declined %s after an error", name)
            raise ToolError(DECLINED) from None

    def no_agent(ctx):
        raise ToolError(DECLINED)  # (unused: `reader` answers every call)

    mcp = tools.build_mcp(net, no_agent, reader=run)
    from mcp.server.transport_security import TransportSecuritySettings
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
                                         allowed_origins=[])
    app = mcp.streamable_http_app(transport_security=security, stateless_http=True, json_response=True,
                                  host="127.0.0.1")
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    threading.Thread(target=listen, daemon=True, name="tool-reader-routes").start()
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on"))
    with output:
        sys.stdout.write(f"LISTENING {port}\n")
        sys.stdout.flush()
    server.run(sockets=[sock])


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--child":
    child(sys.argv[2], sys.argv[3], int(sys.argv[4]))
