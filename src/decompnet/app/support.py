"""What the local server and its routes share: background jobs, the settings file, and the small helpers
routes use to turn requests into work."""

from __future__ import annotations

import json
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from importlib import resources
from pathlib import Path

from .. import hostenv, toolchains
from ..agents import kind_id
from ..agents.api import API_PRESETS, endpoint_entry
from ..network import NetworkError
from ..rom import RomError
from .agents import PER_SESSION

MAX_DISC = 16 * 1024 * 1024 * 1024  # dual-layer DVD and then some


LOG_BYTES, LOG_BACKUPS = 5 << 20, 4
TOOL_SAID_CHARS = 400  # the end of what a failed tool said, in its job's error
DETAILS_CHARS, TRACE_CHARS = 6000, 4000  # what a failed job keeps of a tool's output and of the Python trace
JOB_ID_CHARS = 12
MIN_CONTEXT_TOKENS = 2048  # an endpoint's context is at least this: the work prompt alone needs about that
MAX_SESSIONS = 32  # helpers on one endpoint at once, at most


def file_log(path: Path) -> logging.Logger:
    """decompdecomp's log (logs/decomp.log in its data folder), always on: every job and what it said, decompdecomp's
    own debug messages, the web server's and the libraries' warnings, and every uncaught error in any thread with its
    traceback. The app once quit at start without a word (the tray's import path broke the web server), and finding
    out meant guessing: whatever goes wrong is in this file."""
    logger = logging.getLogger("decompdecomp")
    root = logging.getLogger()
    if any(getattr(h, "baseFilename", None) == str(path) for h in root.handlers):
        return logger
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError:
        return logger
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s %(threadName)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)  # the libraries' debug chatter (httpx, mcp) would bury the app's own
    logger.setLevel(logging.DEBUG)
    logger.propagate = True

    def uncaught(kind, value, tb):
        logger.critical("uncaught error", exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = uncaught
    threading.excepthook = lambda a: logger.critical("uncaught error in thread %s", a.thread.name if a.thread else "?",
                                                     exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    return logger


def _tool_of(cmd) -> str:
    """The program a command ran (past a systemd-run wrapper), by name."""
    argv = [str(a) for a in cmd] if isinstance(cmd, (list, tuple)) else str(cmd).split()
    if "--" in argv and Path(argv[0]).name.startswith("systemd-run"):
        argv = argv[argv.index("--") + 1:]
    return Path(argv[0]).name if argv else "a tool"


def job_error(e: BaseException, log_path: Path | None = None) -> str:
    """What to tell the user when a job fails: why, in words, for every kind of failure."""
    if isinstance(e, subprocess.TimeoutExpired):
        mins = max(1, round((e.timeout or 0) / 60))
        return (f"{_tool_of(e.cmd)} took longer than {mins} minute{'s' if mins != 1 else ''} and was stopped. "
                "A very big game can need more: please report it, with the log")
    if isinstance(e, subprocess.CalledProcessError):
        said = e.stderr or e.output or ""  # what the command said, so the error shows why
        said = (said.decode("utf-8", "replace") if isinstance(said, bytes) else said).strip()
        return f"{_tool_of(e.cmd)} failed ({e.returncode})" + (f": {said[-TOOL_SAID_CHARS:]}" if said else "")
    if isinstance(e, MemoryError):
        return "this computer ran out of memory: close other programs and try again"
    if isinstance(e, (NetworkError, RomError, toolchains.ToolchainError, ValueError, RuntimeError, OSError)):
        return str(e) or type(e).__name__
    where = f" (the log: {hostenv.shown_path(log_path)})" if log_path else ""
    return (f"something went wrong inside decompdecomp ({type(e).__name__}: {e}). That's a bug: please report it with "
            f"the log{where}")


class Jobs:
    """Background work the GUI can follow: imports and installs. A job always ends: done, or an error that says why
    (never `running` forever because of an error nobody caught). Everything goes to the log file too."""

    def __init__(self, log_path: Path | None = None):
        self.jobs: dict[str, dict] = {}
        self.log_path = log_path
        self.logger = file_log(log_path) if log_path else logging.getLogger("decompdecomp")

    def start(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:JOB_ID_CHARS]
        job = {"id": jid, "kind": kind, "state": "running", "log": [], "result": None, "error": None, "t": time.time()}
        self.jobs[jid] = job
        logger = self.logger
        logger.info("job %s %s started", kind, jid)

        def log(line: str) -> None:
            job["log"].append(str(line))
            logger.info("[%s %s] %s", kind, jid[:6], line)

        def run():
            try:
                job["result"] = fn(log)
                job["state"] = "done"
                logger.info("job %s %s done in %.0fs", kind, jid, time.time() - job["t"])
            except BaseException as e:  # noqa: BLE001  a job must end, whatever went wrong
                if getattr(e, "details", None):  # what the tool said, in full
                    job["trace"] = str(e.details)[-DETAILS_CHARS:]
                elif not isinstance(e, (NetworkError, RomError, ValueError)):
                    job["trace"] = traceback.format_exc()[-TRACE_CHARS:]
                job["error"] = job_error(e, self.log_path)
                job["state"] = "error"  # last: whoever sees the state sees the rest
                logger.error("job %s %s failed: %s\n%s", kind, jid, job["error"], traceback.format_exc())
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return jid


class Settings:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data.get("endpoints"), list):  # one saved "api" before 0.10.1: the first endpoint
            old = self.data.get("api") if isinstance(self.data.get("api"), dict) else {}
            self.data["endpoints"] = [endpoint_entry(old | {"id": "e1"})] if old.get("base_url") or old.get("provider") else []

    _lock = threading.Lock()

    def save(self) -> None:
        """Whole or not at all: helpers' threads and the page save at once, and a file two writes had garbled was read
        back as empty settings (endpoints, budget, the helpers to resume)."""
        with self._lock:
            text = json.dumps(self.data, indent=2)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)

    # -- API endpoints: OpenAI-compatible servers for the built-in agent, each with its own address, model and key

    def endpoints(self) -> list[dict]:
        return [e for e in self.data.get("endpoints") or [] if isinstance(e, dict) and e.get("id")]

    def endpoint(self, eid: str) -> dict | None:
        return next((e for e in self.endpoints() if e["id"] == eid), None)

    def save_endpoint(self, data: dict) -> dict:
        """Add an endpoint (no id) or change one. A key is only replaced by a new one typed in (the masked one the
        GUI shows comes back unchanged)."""
        numbers = {}
        for k in ("context_tokens", "max_sessions"):  # the model's context in tokens (empty = find out); a box's limit
            if k in data:
                try:
                    numbers[k] = int(str(data[k] or "").strip() or 0)
                except ValueError:
                    raise ValueError("the context size is a number of tokens, like 16384" if k == "context_tokens"
                                     else "the session limit is a number, like 1") from None
        current = self.endpoint(str(data.get("id") or ""))
        preset = API_PRESETS.get(str(data.get("provider") or (current or {}).get("provider") or "custom"), {})
        for k in ("base_url", "model"):  # "Add" on an empty form added an endpoint that failed only when started
            if k in data and not str(data[k] or "").strip() and not preset.get(k):
                raise ValueError("Give the endpoint's API address and model, like https://api.deepseek.com/v1 and "
                                 "deepseek-chat.")
        if current is None:  # ids are never reused: a session resumed later can't end up on another machine
            ids = {e["id"] for e in self.endpoints()}
            n = max(int(self.data.get("endpoint_seq") or 0), len(ids)) + 1
            while f"e{n}" in ids:
                n += 1
            self.data["endpoint_seq"] = n
            current = {"id": f"e{n}"}
            self.data.setdefault("endpoints", []).append(current)
        for k in ("name", "provider", "base_url", "model"):
            if k in data:
                current[k] = str(data[k] or "").strip()
        for k, n in numbers.items():
            if n:
                current[k] = max(MIN_CONTEXT_TOKENS, n) if k == "context_tokens" else max(1, min(n, MAX_SESSIONS))
            else:
                current.pop(k, None)
        if data.get("api_key") and "•" not in str(data["api_key"]):
            current["api_key"] = str(data["api_key"]).strip()
        if data.get("clear_key"):
            current.pop("api_key", None)
        current.update(endpoint_entry(current))
        return current

    def remove_endpoint(self, eid: str) -> None:
        self.data["endpoints"] = [e for e in self.endpoints() if e["id"] != eid]

    def public(self) -> dict:
        def masked(e: dict) -> dict:
            out = {k: v for k, v in e.items() if k != "api_key"}
            if e.get("api_key"):
                out["api_key"], out["has_key"] = "•" * 8 + e["api_key"][-4:], True
            return out

        eps = [masked(e) for e in self.endpoints()]
        return {"endpoints": eps, "api": eps[0] if eps else {},
                "per_session": self.data.get("per_session", PER_SESSION), "last_project": self.data.get("last_project"),
                "autopilot": self.data.get("autopilot", True), "budget_usd": self.data.get("budget_usd") or 0,
                "auto_update": bool(self.data.get("auto_update")), "check_updates": self.data.get("check_updates", True) is not False,
                "auto_compiler": bool(self.data.get("auto_compiler")),
                "bridges_removed_seen": bool(self.data.get("bridges_removed_seen")),
                "launcher": _launcher(self.data.get("launcher") or {}), "recent_models": self.data.get("recent_models") or {}}


def session_options(entry: dict) -> dict:
    """What a built-in agent session needs from its endpoint."""
    out = {"endpoint": entry.get("id") or "", "provider": entry["provider"], "base_url": entry["base_url"],
           "model": entry["model"], "provider_name": entry["name"]}
    return out | {k: entry[k] for k in ("api_key", "context_tokens") if entry.get(k)}


def spread_sessions(settings: Settings, manager, picked: list[str], count: int, given: dict) -> list[tuple[dict, int]]:
    """(endpoint, sessions) for a start of `count` built-in agent sessions: spread over the endpoints picked, one
    at a time in turn, each up to its own limit of sessions at once (a small box serves one request at a time).
    A start without endpoints (an older GUI, or an address typed in) uses the first saved one, with what it
    changes; a saved key only ever goes to its own endpoint's address."""
    if not picked:
        first = settings.endpoints()[0] if settings.endpoints() else {}
        typed = {k: v for k, v in given.items() if k in ("provider", "base_url", "model", "context_tokens") and v}
        entry = endpoint_entry(first | typed)
        if first and entry["base_url"].rstrip("/") != first["base_url"].rstrip("/"):
            entry.pop("api_key", None)
        if not entry.get("base_url") or not entry.get("model"):
            raise ValueError("add an API endpoint first (Agents & models): its address and model")
        return [(entry, count)]
    eps = [settings.endpoint(e) for e in picked]
    if any(e is None for e in eps):
        raise ValueError("an endpoint you picked isn't saved any more: pick again")
    busy = {e["id"]: sum(1 for x in manager.sessions.values() if x.options.get("endpoint") == e["id"]
                         and x.state in ("starting", "working", "waiting") and not x.stop.is_set()) for e in eps}
    plan = {e["id"]: 0 for e in eps}
    for _ in range(count):
        room = [e for e in eps if not e.get("max_sessions") or busy[e["id"]] + plan[e["id"]] < int(e["max_sessions"])]
        if not room:
            break
        pick = min(room, key=lambda e: (busy[e["id"]] + plan[e["id"]], eps.index(e)))
        plan[pick["id"]] += 1
    if not any(plan.values()):
        raise ValueError("every endpoint you picked already runs as many sessions as you allowed it")
    return [(e, plan[e["id"]]) for e in eps if plan[e["id"]]]


def _launcher(saved: dict) -> dict:
    """The last launcher choice; one saved for Gemini CLI (retired) comes back as Antigravity with its default model."""
    if saved.get("kind") and kind_id(saved["kind"]) != saved["kind"]:
        return {**saved, "kind": kind_id(saved["kind"]), "model": ""}
    return saved


def static_file(name: str) -> bytes | None:
    if ".." in name or name.startswith(("/", "\\")):
        return None
    try:
        return resources.files("decompnet.app").joinpath("static", name).read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None
