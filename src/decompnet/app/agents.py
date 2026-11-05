"""Helper agents: the AI agents decompdecomp starts on a project. Only these work on projects; nobody connects from
outside.

This file is what's the same for every agent. What one agent does its own way (its command line, reading what it
prints, its models, the API helper's loop) is in its module in agents/, reached through the connector,
`agents.get(kind)`: a check of an agent's name here is a bug. Programs (Claude Code, Codex, Antigravity CLI, opencode)
run headless in a private working folder, each connected to this app's MCP endpoint on 127.0.0.1 with a token of its
own and allowed to use only its tools (_run_cli). An agent built into the app runs its own loop (Agent.run).

Every helper is a record in the database (`helpers`): what it is (kind, model, its name), what it does now (working,
waiting for something and until when, stopped and by whom, paused on an error it can't get past), what it said last,
how often it started again and what it spent. Each session it runs is a row of `helper_sessions` (spend, tokens,
turns, tool calls, compiles and matches, per model), so models can be compared on cost. The records outlive the app:
after a restart a dead helper's claim showed as a "connected agent" with no stop button, and its name was taken, so
numbers kept climbing (opencode-20). Now a helper from before shows as it was, its claim is released at start, and it
carries on if the app stopped it by quitting.

A helper starts again by itself after a session ends or fails, with a growing pause between failures; on a usage limit
it waits until the limit resets (when the agent says when) and carries on. Five fast failures in a row pause it, with
the error to read. Quitting the app ends every helper's whole process group (Manager.shutdown).

The prompt goes in on stdin, never on the command line: no length limit, and nothing in it is parsed.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decompnet.hostenv import NO_WINDOW

from .. import agents, consoles, network
from ..network import NetworkError
from . import tools
from .engine import RomNetwork

start_progress = ContextVar('agent-start-progress', default=None)


def report_start(message):
    if (report := start_progress.get()) is not None:
        report(message)


log = logging.getLogger("decompdecomp")
_windows_jobs: dict[int, object] = {}
_jobs_lock = threading.Lock()


def _close_windows_job(pid: int) -> bool:
    with _jobs_lock:
        job = _windows_jobs.pop(pid, None)
    if job is not None:
        job.close()
        return True
    return False


class Fatal(RuntimeError):
    """A helper can't work until the user changes something: it pauses with this, instead of trying again."""


class ToolConnectionUnavailable(RuntimeError):
    """A recoverable tool connection failure; a CLI handshake retry may require a model request."""


def connection_failure(error, token):
    """Exception groups often hide the transport error; retain bounded, redacted leaf diagnostics."""
    children = getattr(error, 'exceptions', None)
    if children:
        return '; '.join(connection_failure(child, token) for child in children[:4])[:600]
    return (type(error).__name__+': '+(str(error) or 'no additional message')).replace(token, '[redacted]')[:600]


def resumable(s):
    return (s.state == 'stopped' and s.stopped_by == BY_QUIT or
            s.state == 'paused' and s.why.startswith("The app's tool connection is unavailable."))


# An agent's error that's about signing in or keys: then the pause says how to sign in
_SIGNIN = re.compile(r"auth|log ?in|sign(ed)? ?in|credential|api.?key|unauthori[sz]ed|\b401\b|\b403\b", re.I)

# Claims per helper session. Each session starts with a small, clean context (a function's assembly, drafts and
# diffs would otherwise ride along into the next one), but pays for its system prompt, tools and guides again:
# with 2 per session that was most of what small functions cost.
PER_SESSION = 8
STARTUP_FACTS_CHARS = 1800
STARTUP_BRIEF_CHARS = 6000
MAX_AT_ONCE = 8  # helpers started with one click
MAX_PER_START = 24  # helpers one start may begin in total, across the models it picks: without it, many models at once
                    # would launch hundreds (a start used to check only each endpoint's own max_sessions)
FEED_LINES = 400  # what each helper said and did, kept for its details
FEED_TEXT = 4000  # characters of one feed line
LAST_WORDS = 600  # characters of the agent's last message kept on its record

# The states a helper is in. `stopped_by` says who stopped one: the user, the app quitting (it carries on when the
# app starts again), or the helper itself (nothing left, the budget spent).
LIVE = ("starting", "working", "waiting")
BY_YOU, BY_QUIT, BY_ITSELF = "you", "quit", "itself"

# Starting again. After a good session the next one starts at once; after a failure the pause doubles from
# BACKOFF_FIRST up to BACKOFF_LONGEST. FAST_FAILURES_TO_PAUSE failures in a row, each within FAST_FAILURE of its start,
# pause the helper: it can't work, and trying on only spends.
SESSION_GAP = 3
BACKOFF_FIRST = 10
BACKOFF_LONGEST = 10 * 60
FAST_FAILURE = 2 * 60
FAST_FAILURES_TO_PAUSE = 5
# the provider's limit of requests at once (HTTP 429): not the helper's failure, so waited out, this many times
THROTTLE_WAIT = 60
THROTTLES_IN_A_ROW = 10
# A plan's usage limit resets by itself: wait until the time the agent says, a little after it; when it says none,
# from LIMIT_WAIT_FIRST doubling up to LIMIT_WAIT_LONGEST. A reset further away than LIMIT_RESET_FURTHEST is misread.
LIMIT_WAIT_FIRST = 15 * 60
LIMIT_WAIT_LONGEST = 60 * 60
LIMIT_RESET_MARGIN = 60
LIMIT_RESET_FURTHEST = 8 * 24 * 3600
# in a room whose host is away: look again this often; the host counts as away after ROOM_STALE without an answer
ROOM_RETRY = 10
ROOM_STALE = 30
PREP_WAIT = 15 * 60  # longest wait for start_files before the helpers start anyway
ORDER_WAIT = 2 * 60  # longest wait for the twins' first ordering
STOP_GRACE = 5  # seconds between asking a helper's processes to end (SIGTERM) and ending them (SIGKILL)

# Every process a helper starts carries this in its environment, set to the app's home: quitting ends them, even one
# that left the helper's process group (opencode 2 starts a background `opencode serve --service`, which outlived
# its parent as a child of PID 1), and a start after a crash ends what the crashed app left. The home tells a
# development copy's helpers from the release's.
HELPER_ENV = "DECOMP_HELPER_OF"
# /proc/<pid>/stat after the command's closing parenthesis: field 22 (starttime) is the 20th, counted from 0 at 19
STAT_STARTTIME_INDEX = 19
PROCFS = Path("/proc")

# finding which agents are installed: DECOMP_DETECT_AGENTS=off never runs one (the tests set it: `agy models` starts
# the MCP servers in Antigravity's settings)
DETECT_ENV = "DECOMP_DETECT_AGENTS"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS helpers(
      id INTEGER PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
      name TEXT NOT NULL, label TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
      why TEXT NOT NULL DEFAULT '', todo TEXT NOT NULL DEFAULT '', until REAL, stopped_by TEXT NOT NULL DEFAULT '',
      last_message TEXT NOT NULL DEFAULT '', started_at REAL, restarts INTEGER NOT NULL DEFAULT 0,
      sessions INTEGER NOT NULL DEFAULT 0, usd REAL NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
      pid INTEGER, pid_start INTEGER, UNIQUE(project, name))""",
    """CREATE TABLE IF NOT EXISTS helper_sessions(
      id INTEGER PRIMARY KEY, helper_id INTEGER NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL,
      model TEXT NOT NULL, agent TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
      outcome TEXT NOT NULL DEFAULT '', usd REAL NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
      output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
      cache_write_tokens INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
      turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0, compiles INTEGER NOT NULL DEFAULT 0,
      matches INTEGER NOT NULL DEFAULT 0, task TEXT NOT NULL DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS idx_helper_sessions_handoff ON helper_sessions(helper_id,project,id)",
    "CREATE INDEX IF NOT EXISTS idx_helper_sessions_model ON helper_sessions(project, model)",
    "CREATE INDEX IF NOT EXISTS idx_helper_sessions_worker ON helper_sessions(helper_id,agent,ended_at)",
)
TOKEN_KINDS = ("input", "output", "cache_read", "cache_write")  # what agents report, as `usage` items say it
# A model spinning on a project: in the last SPIN_WINDOW its helpers compiled at least SPIN_COMPILES times, and either
# matched once per SPIN_ALONE compiles or worse, or needed SPIN_RATIO times the compiles a match of another model at
# work there (one with SPIN_MATCHES matches or more). On an Xbox game in one night: one model's 462 sessions
# compiled 5,741 times for 10 matches ($138), another's 22 compiled 1,184 times for 26 ($199).
SPIN_WINDOW = 24 * 3600
SPIN_COMPILES = 200
SPIN_ALONE = 200
SPIN_RATIO = 4
SPIN_MATCHES = 3


# what an agent CLI says when nobody signed in to it (Claude Code: "Not logged in · Please run /login")
_SIGNED_OUT = re.compile(r"not logged in|run /login|invalid api key|not authenticated|please (?:log|sign) ?in|"
                         r"auth(?:entication)? method", re.I)
# and when its sign-in stopped working (Claude Code, exit code 1 at once, nothing done: "Failed to authenticate: OAuth
# session expired and could not be refreshed", its message marked "error": "authentication_failed"). Read as a plain
# failure, it went by unnoticed in a long session: two helpers retried after 10, 19, 39 and 76 s with no state saying
# why, until the user happened to sign in again. It may be passing (a refresh that failed once), so it's tried a few
# more times, then the helper asks for a new sign-in.
AUTH_FAILED = re.compile(r"failed to authenticate|oauth (?:session|token)[^.\n]{0,40}(?:expired|revoked|invalid)|"
                         r"authentication_(?:failed|error)|could not be refreshed|\b401\b[^\n]{0,80}unauthori[sz]ed|"
                         r"token (?:has )?expired|authorization grant[^.\n]{0,40}(?:invalid|revoked|expired)|\binvalid_grant\b", re.I)
AUTH_RETRIES = 3  # failed sign-ins in a row before the helper pauses and asks for a new one
AUTH_WAIT = 60  # seconds before the first try again, doubling
# what a model's provider says when the account has no credit left (DeepSeek: "Insufficient Balance", OpenAI:
# "insufficient_quota", Anthropic: "credit balance is too low", OpenCode: "quota exceeded"): trying again can't help
# until it's topped up
_NO_CREDIT = re.compile(r"insufficient[ _](?:balance|credits?|funds|quota)|exceeded your current quota|"
                        r"credit balance is too low|payment required|out of credits?|requires usage credits", re.I)
# and when it's asked too much at once (HTTP 429): it works again in a while
THROTTLED = re.compile(r"too many requests|rate[ _-]?limit|concurrency[ _-]?limit|\b429\b|overloaded", re.I)
# and when a subscription's allowance is used up for now (Claude Code: "usage limit reached", "You've hit your
# session limit", Codex: "You've hit your usage limit", Antigravity: RESOURCE_EXHAUSTED, OpenCode Go: "quota
# exceeded"): it resets by itself in hours, so a helper waits for it instead of stopping (a night's run lost most of
# its helpers that way; Claude Code's "session limit" wording once paused them as five fast failures)
LIMIT = re.compile(r"(?:usage|session|weekly) limit|(?<!rate )(?<!rate-)(?<!rate_)(?<!concurrency )"
                    r"(?<!concurrency-)(?<!concurrency_)limit (?:reached|exceeded)|"
                    r"hit your (?:(?!rate\b)[\w-]+ )?limit|quota[ _](?:exceeded|exhausted)|resource[ _]exhausted|"
                    r"out of (?:usage|messages)", re.I)
# When a limit resets, as agents say it. Claude Code: "usage limit reached|1792140000" (seconds since 1970), or
# "5-hour limit reached ∙ resets 3pm (Europe/Berlin)"; Codex: "try again in 2 hours 13 minutes" or "try again at
# 8:05 PM"; Gemini: "Please retry in 46.6s"; others an ISO time after "reset" or "until".
_RESET_EPOCH = re.compile(r"limit reached\|(\d{9,11})\b", re.I)
_RESET_IN = re.compile(r"(?:try again|retry|resets?|available again)\s+in\s+((?:\d+(?:\.\d+)?\s*[a-z]+[\s,]*(?:and\s+)?)+)",
                       re.I)
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])", re.I)
_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_RESET_AT = re.compile(r"(?:resets?|try again|available again)(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\b\.?"
                       r"(?:\s*\(([A-Za-z_]+(?:/[A-Za-z_+-]+)+|UTC)\))?", re.I)
_RESET_ISO = re.compile(r"(?:reset|again|until|after)\D{0,40}?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
                        r"(?:Z|[+-]\d{2}:?\d{2})?)", re.I)
HOURS_HALF_DAY = 12


def no_credit(said: str) -> str | None:
    """The helper's error when its model's provider says the account is out of credit, else None."""
    if not (m := _NO_CREDIT.search(said)):
        return None
    return (f"the model's provider says the account is out of credit (“{m.group(0)}”): top it up or select another available model, then "
            "start the agents again")


def limit_reset(said: str, now: float | None = None) -> float | None:
    """When a usage limit resets (seconds since 1970), read from what the agent said; None when it doesn't say, or
    says something implausible."""
    now = time.time() if now is None else now
    at = None
    if (m := _RESET_EPOCH.search(said)):
        at = float(m.group(1))
    elif (m := _RESET_IN.search(said)):
        at = now + sum(float(n) * _SECONDS[unit.lower()] for n, unit in _DURATION_PART.findall(m.group(1)))
    elif (m := _RESET_ISO.search(said)):
        try:
            when = datetime.fromisoformat(m.group(1).replace(" ", "T"))
            at = (when if when.tzinfo else when.astimezone()).timestamp()
        except ValueError:
            at = None
    elif (m := _RESET_AT.search(said)) and (m.group(2) or m.group(3)):  # "resets 3" alone could be anything
        hour, minute, half, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        if half:
            hour = hour % HOURS_HALF_DAY + (HOURS_HALF_DAY if half == "p" else 0)
        tz = None
        if zone and zone.upper() == "UTC":
            tz = timezone.utc
        elif zone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                tz = ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                tz = None
        try:
            here = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
            when = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if when.timestamp() <= now:
            when += timedelta(days=1)
        at = when.timestamp()
    if at is None or not now - LIMIT_RESET_MARGIN < at < now + LIMIT_RESET_FURTHEST:
        return None
    return at


def helper_name(kind: str, model: str | None, taken: set[str]) -> str:
    """The agent identity of a helper: its kind and its model, numbered (opencode-deepseek-v4.1-flash-1). Matches,
    compiles and statistics hang off this name, so a model never inherits another's: helpers used to be opencode-1,
    opencode-2... whatever model they ran, and a re-added helper with another model took over the old one's record.
    The same model again gets the same name back and carries on its record."""
    m = re.sub(r"[^a-z0-9._-]+", "-", (model or "").lower().rsplit("/", 1)[-1]).strip("-._")
    m = m.removeprefix(f"{kind}-")[:24]  # claude-opus-4-6-1, not claude-claude-opus-4-6-1
    base = f"{kind}-{m}" if m else kind
    k = 1
    while f"{base}-{k}" in taken:
        k += 1
    return f"{base}-{k}"


def _which(cmd: str) -> str | None:
    found = shutil.which(cmd)
    if found:
        return found
    home = Path.home()
    directories = [home / ".local" / "bin", home / ".claude" / "local", home / ".opencode" / "bin",
                   home / ".npm-global" / "bin"]
    if os.name != "nt":
        # Desktop launchers do not inherit nvm's shell initialization. nvm-windows uses a different layout.
        nvm = Path(os.environ.get("NVM_DIR", home / ".nvm")) / "versions" / "node"
        def version_key(folder):
            return tuple(int(part) for part in re.findall(r"\d+", folder.name))
        directories.extend(folder / "bin" for folder in sorted(nvm.glob("v*"), key=version_key, reverse=True))
    if os.name == "nt":
        directories.append(Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "npm")
        directories.append(Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "agy" / "bin")
    for d in directories:
        if os.name == "nt":
            found = shutil.which(cmd, path=str(d))
            if found:
                return found
        elif (d / cmd).is_file() and os.access(d / cmd, os.X_OK):
            return str(d / cmd)
    return None


def detect(quick: bool = False) -> list[dict]:
    """Which agents are installed, their versions and models. `quick` (or DECOMP_DETECT_AGENTS=off) runs none of
    them, only looks where they are: running them takes seconds (`agy models` alone about three, and it starts the MCP
    servers in Antigravity's settings), so the app does that in the background (Manager.detected)."""
    quick = quick or os.environ.get(DETECT_ENV) == "off"
    out = []
    for k in agents.AGENTS:
        row = k.describe()
        if not k.command:
            row.update(installed=True, version=None, path=None)
        else:
            path = _which(k.command)
            row.update(installed=path is not None, path=path, version=None)
            if path:
                row.update(version=k.version(path, run=not quick), models=k.models(path, run=not quick),
                           default_model=k.config_model())
        if row["install"].startswith("npm ") and not row["installed"] and not shutil.which("npm"):
            row["needs"] = "Node.js (https://nodejs.org) for npm"
        out.append(row)
    return out


# ---------------------------------------------------------------------------------- sessions

def _stdin_payload(prompt: str | None) -> str | None:
    """What to write to a helper's stdin. None means its prompt went in a file (zcode's --attach), so write no
    prompt text and just close the pipe. On Windows, winhelper.py still needs its \x01 gate before it starts the
    CLI; that gate is app plumbing, not prompt text, and must arrive even for an empty (or file) prompt."""
    gate = "\x01" if os.name == "nt" else ""
    if prompt is None:
        return gate or None
    return gate + prompt


def _feed_stdin(proc: subprocess.Popen, prompt: str | None) -> None:
    """Write `_stdin_payload(prompt)` and close the pipe, so the agent sees end of input."""
    payload = _stdin_payload(prompt)
    if payload is not None:
        proc.stdin.write(payload)
    proc.stdin.close()


def _fresh_usage() -> dict:
    return {"usd": 0.0, "tokens": 0, "turns": 0, "tool_calls": 0, "coverage": "unknown"} | {k: 0 for k in TOKEN_KINDS}


@dataclass
class Session:
    """A helper: its record (the `helpers` row, id as text) and, while it runs, its thread, process and feed."""
    id: str
    kind: str
    label: str
    slug: str
    agent_name: str
    options: dict
    state: str = "starting"  # starting | working | waiting | stopped | paused
    why: str = ""  # the state in words: waiting for what, stopped why, paused on what error
    todo: str = ""  # paused: what the user can do about it
    until: float | None = None  # waiting: until when
    stopped_by: str = ""  # BY_YOU | BY_QUIT | BY_ITSELF
    last_message: str = ""  # the agent's own last words
    started_at: float = field(default_factory=time.time)
    restarts: int = 0  # started again by itself, after a failure or a usage limit
    runs: int = 0  # sessions
    configuration_error: bool = False
    error: str = ""  # the last error
    feed: collections.deque = field(default_factory=lambda: collections.deque(maxlen=FEED_LINES))
    seq: int = 0
    record: Callable[[str, object], None] | None = field(default=None, repr=False)  # current session transcript callback
    session_id: int = 0
    prompt_config: dict | None = field(default=None, repr=False)
    startup_context: str | None = field(default=None, repr=False)
    tool_handshake_failures: int = 0
    tool_handshake_required: bool = False
    context_budget: object | None = field(default=None, repr=False)
    rotated: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    proc: subprocess.Popen | None = None
    pid_start: int | None = None
    usd: float = 0.0  # what the agent's provider charged, as the agent reports it
    tokens: int = 0
    usage: dict = field(default_factory=_fresh_usage)  # this session's counts (helper_sessions)
    signed_out: bool = False  # the agent CLI isn't signed in: trying again won't help
    auth_failed: bool = False  # its sign-in failed (an expired session): tried a few times, then a new sign-in
    throttled: bool = False  # the last run ended on the provider's limit of requests at once: not a failure
    limited: bool = False  # the last run ended on the plan's usage limit: wait for it to reset
    cooldown_observed: float = 0
    continuation_attempts: int = 0
    continuation_note: str = ""
    limit_until: float | None = None  # when the agent said that limit resets

    @property
    def model(self) -> str:
        return str(self.options.get("model") or "")

    def push(self, kind: str, text: str) -> None:
        if self.record and kind in ("text", "think", "error", "info", "termination"):
            self.record(kind, text)
        self.seq += 1
        self.feed.append({"seq": self.seq, "t": time.time(), "type": kind, "text": text[:FEED_TEXT]})
        if kind == "text":
            self.last_message = text[:LAST_WORDS]
        if kind in ("info", "error"):  # starts, waits, stops and errors outlive the session: why a night went as it did
            log.info("helper %s (%s): %s", self.agent_name, self.slug, text[:500])

    def snapshot(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "kind_name": agents.get(self.kind).name, "can_login": agents.get(self.kind).supports_login, "label": self.label,
                "selection": self.options.get("selection") or {},
                "focus_function":self.options.get("focus_function"), "focus_until":self.options.get("focus_until"),
                "per_session": self.options.get("per_session", PER_SESSION),
                "task": self.options.get("task") or "",  # "readable": it polishes matched code
                "slug": self.slug, "agent": self.agent_name, "model": self.model, "state": self.state, "why": self.why,
                "configuration_error": self.configuration_error, "todo": self.todo, "until": self.until, "stopped_by": self.stopped_by,
                "last_message": self.last_message, "runs": self.runs, "restarts": self.restarts, "error": self.error,
                "started_at": self.started_at, "usd": round(self.usd, 4), "tokens": self.tokens,
                "current_usage_coverage": self.usage.get("coverage", "unknown"),
                "feed": [f for f in self.feed if f["seq"] > since]}


def spin_advice(recent: list[dict]) -> dict[tuple[str, str], str]:
    """A sentence for each model spinning on a project (SPIN_*), by (kind, model): what its helpers got for their
    compiles lately and what a stronger model might do, for the user to weigh, not an order. `recent`: costs' rows
    (kind, model, usd, compiles, matches) for the last SPIN_WINDOW."""
    def name(r):
        return r["model"] or r["kind"]

    def per_match(r):
        return (r["compiles"] or 0) / r["matches"] if r["matches"] else None

    def money(r):
        return f", ${r['usd'] / r['matches']:,.2f} a match" if r["usd"] and r["matches"] else ""

    proven = [r for r in recent if (r["matches"] or 0) >= SPIN_MATCHES]
    best = min(proven, key=per_match, default=None)
    out = {}
    for r in recent:
        compiles, matches = r["compiles"] or 0, r["matches"] or 0
        if compiles < SPIN_COMPILES or r is best:
            continue
        mine = compiles / matches if matches else float("inf")
        if best is not None and mine >= SPIN_RATIO * per_match(best):
            cheaper = bool(r["usd"] and best["usd"] and (not matches or r["usd"] / matches > best["usd"] / best["matches"]))
            out[(r["kind"], r["model"])] = (
                f"In the last day {name(r)}'s helpers here made {compiles:,} compiles for "
                f"{matches or 'no'} match{'es' * (matches != 1)}{money(r)}; {name(best)}'s needed about "
                f"{per_match(best):,.0f} compiles a match{money(best)}. What's left of this game may be past what "
                f"{name(r)} does well: {name(best)} could get further{', for less' if cheaper else ''}. You might "
                f"give {name(r)} fewer helpers here.")
        elif mine >= SPIN_ALONE:
            out[(r["kind"], r["model"])] = (
                f"In the last day {name(r)}'s helpers here made {compiles:,} compiles for "
                f"{matches or 'no'} match{'es' * (matches != 1)}{money(r)}. What's left of this game may be past what "
                f"{name(r)} does well: a stronger model could get further.")
    return out


def _proc_start(pid: int) -> int | None:
    """When a process started (clock ticks since boot): with its pid, it tells that process from a later one that
    got the same pid."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                from datetime import datetime

                return int(datetime.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y").timestamp())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return None
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[STAT_STARTTIME_INDEX])
    except (OSError, ValueError, IndexError):
        return None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _end_groups(pgids: set[int], grace: float = STOP_GRACE) -> None:
    """End these process groups: asked first (SIGTERM), ended (SIGKILL) whatever is left after `grace` seconds."""
    if os.name == "nt":
        from ..winprocess import terminate_tree

        for pid in pgids:
            if pid > 1 and not _close_windows_job(pid):  # fallback for processes started without a job
                try:
                    terminate_tree(pid)
                except OSError:
                    log.warning("couldn't stop helper process tree %s", pid, exc_info=True)
        return
    pgids = {g for g in pgids if g > 1 and g != os.getpgid(0)}  # never the app's own group
    for g in pgids:
        try:
            os.killpg(g, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.time() + grace
    while pgids and time.time() < deadline:
        pgids = {g for g in pgids if _group_alive(g)}
        time.sleep(0.1)
    for g in pgids:
        try:
            os.killpg(g, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _terminate(proc: subprocess.Popen) -> None:
    """End a helper's agent with everything it started (node starts opencode, opencode its server...): its process
    group, asked to end now and ended for good STOP_GRACE seconds later, without waiting here."""
    def end():
        # Keep the process handle until cleanup ends: Windows must not reuse its pid for an unrelated program.
        if os.name == "nt" and proc.poll() is not None:
            _close_windows_job(proc.pid)
        else:
            _end_groups({proc.pid})
    threading.Thread(target=end, daemon=True, name=f"end-{proc.pid}").start()


class Manager:
    def __init__(self, net: RomNetwork, home: Path, mcp_url: str):
        self.net, self.home, self.mcp_url = net, home, mcp_url
        from .transcripts import Transcripts
        self.transcripts = Transcripts(net.store)
        self.sessions: dict[str, Session] = {}
        self.assignments: dict[int, str] = {}  # agent id -> project slug
        self._lock = threading.RLock()
        self._metrics_locks = {}
        self._prep: dict[str, threading.Event] = {}  # slug -> giving its code source files before agents claim
        self._found: list[dict] | None = None  # the agents installed, as last found (detected)
        self._finding: threading.Thread | None = None
        self._down = False
        self.on_stop = lambda: None  # set by the server: close tunnels when the app quits
        # set by the server: a helper's options as they are now (an API helper's key comes from its saved endpoint,
        # never from its record), or None when it can't carry on (its endpoint was removed)
        self.options_now = lambda kind, options: options
        with net.store.tx() as db:
            for statement in SCHEMA:
                db.execute(statement)
            if "usage_coverage" not in {r[1] for r in db.execute("PRAGMA table_info(helper_sessions)")}:
                db.execute("ALTER TABLE helper_sessions ADD COLUMN usage_coverage TEXT NOT NULL DEFAULT 'unknown'")
            if "task" not in {r[1] for r in db.execute("PRAGMA table_info(helper_sessions)")}:
                # Sessions from before it was kept take their helper's task now, while its record is there. Ids are
                # reused (helpers.id has no AUTOINCREMENT), so the helper must also be the session's project and
                # agent name: by id alone an old session took a new helper's task. A helper switched from polishing
                # to decompiling counts its polishing as decompiling until it ages out of SPIN_WINDOW. Its options
                # may not be JSON (an empty value, which _restore reads as no options): json_extract of one stopped
                # the app from starting, so json_valid guards it.
                db.execute("ALTER TABLE helper_sessions ADD COLUMN task TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE helper_sessions SET task=COALESCE((SELECT CASE WHEN json_valid(h.options) THEN "
                           "json_extract(h.options, '$.task') END FROM helpers h "
                           "WHERE h.id=helper_sessions.helper_id AND h.project=helper_sessions.project "
                           "AND h.name=helper_sessions.agent), '')")
        # End process accounting; supported CLIs can still resume their saved conversation.
        with net.store.tx() as db:
            db.execute("UPDATE helper_sessions SET ended_at=?, outcome='interrupted' WHERE ended_at IS NULL",
                       (time.time(),))
        self._restore()
        self.refresh_detected()

    def budget(self) -> float:
        return 0.0  # set by the server from the settings

    # -- the records

    def _restore(self) -> None:
        """The helpers from before the app started, as they were. One that was working when the app closed (it quit,
        or crashed) is stopped, with what it left running ended, and carries on in resume(). Every helper's claim is
        released now: its process is gone, and the function waited for the claim to expire meanwhile."""
        crashed: set[int] = set()
        for r in self.net.store.all("SELECT * FROM helpers ORDER BY id"):
            options = json.loads(r["options"] or "{}")
            s = Session(str(r["id"]), r["kind"], r["label"], r["project"], r["name"], options, state=r["state"],
                        why=r["why"], todo=r["todo"], until=r["until"], stopped_by=r["stopped_by"],
                        last_message=r["last_message"], started_at=r["started_at"] or time.time(),
                        restarts=r["restarts"], runs=r["sessions"], usd=r["usd"], tokens=r["tokens"])
            if s.kind not in {a.id for a in agents.AGENTS}:
                continue
            if s.state in LIVE:
                from .session_recovery import checkpoint
                checkpoint(self.net, s)
                if r["pid"] and r["pid_start"] is not None and _proc_start(r["pid"]) == r["pid_start"]:
                    crashed.add(r["pid"])
                s.state, s.stopped_by, s.until = "stopped", BY_QUIT, None
                s.why = "decompdecomp closed while it worked"
                self._save(s)
            self.sessions[s.id] = s
        _end_groups(crashed | self._strays())
        # helpers, and the runners from before records (named like helpers): with a note on each function, and a
        # 'stopped' event that ends their run (what they released in it no longer keeps them off it)
        self.net.release_local_claims("the app restarted")

    def _save(self, s: Session) -> None:
        pid = s.proc.pid if s.proc is not None and s.proc.poll() is None else None
        with self.net.store.tx() as db:
            db.execute("UPDATE helpers SET label=?, state=?, why=?, todo=?, until=?, stopped_by=?, last_message=?, "
                       "started_at=?, restarts=?, sessions=?, usd=?, tokens=?, pid=?, pid_start=?, options=? WHERE id=?",
                       (s.label, s.state, s.why, s.todo, s.until, s.stopped_by, s.last_message, s.started_at,
                        s.restarts, s.runs, s.usd, s.tokens, pid, s.pid_start if pid else None,
                        json.dumps({k:v for k,v in s.options.items() if k != "api_key"}), s.id))

    def _record(self, slug: str, kind: str, name: str, label: str, options: dict) -> Session:
        """The helper `name` on this project: its record from before (it carries on its numbers), or a new one."""
        kept = {k: v for k, v in options.items() if k != "api_key"}  # the key stays in the settings
        s = next((x for x in self.sessions.values() if x.slug == slug and x.agent_name == name), None)
        if s is not None:
            s.kind, s.label, s.options = kind, label, dict(options)
            with self.net.store.tx() as db:
                db.execute("UPDATE helpers SET kind=?, model=?, label=?, options=? WHERE id=?",
                           (kind, s.model, label, json.dumps(kept), s.id))
            return s
        with self.net.store.tx() as db:
            # helper_sessions survives Remove; never recycle its helper_id for a new record.
            rid = db.execute("SELECT MAX(id) + 1 FROM (SELECT id FROM helpers UNION ALL "
                             "SELECT helper_id AS id FROM helper_sessions)").fetchone()[0] or 1
            db.execute("INSERT INTO helpers(id, project, kind, model, name, label, options, state, started_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?)", (rid, slug, kind, str(options.get("model") or ""), name, label,
                                                         json.dumps(kept), "starting", time.time()))
        s = self.sessions[str(rid)] = Session(str(rid), kind, label, slug, name, dict(options))
        return s

    def _taken(self) -> set[str]:
        """Names in use: helpers working anywhere, and stopped ones still winding down (their thread releases their
        name's claim when it ends)."""
        return {s.agent_name for s in self.sessions.values()
                if s.state in LIVE or (s.thread is not None and s.thread.is_alive())}

    def _label(self, kind: str, name: str, options: dict) -> str:
        """What the GUI calls a helper: its name, unless its agent says otherwise (an API helper: its endpoint)."""
        used = {s.label for s in self.sessions.values() if s.kind == kind and s.state in LIVE}
        return agents.get(kind).label(name, options, used)

    # -- control

    def start(self, kind: str, slug: str, count: int = 1, options: dict | None = None) -> list[str]:
        if getattr(self, 'source_update_pending', False):
            raise ValueError('A source update is queued. Cancel it before adding new agents.')
        report_start("Validating worker selection…")
        options = dict(options or {})
        options["selection"] = self.net.validate_selection(options.get("selection"))
        from . import focused_agents
        options = focused_agents.configure(self.net,slug,options,count)
        agent = agents.get(kind)  # (an unknown kind is a ValueError that says which there are)
        if agent.id != kind:
            kind = agent.id
            options.pop("model", None)  # a Gemini CLI model name may mean nothing to Antigravity: use its default
        if self._down:
            raise ValueError("decompdecomp is quitting")
        ids = []
        report_start("Preparing the project before creating workers…")
        if options.get("task") != "readable":
            self._prepare(slug)
        report_start("Waiting for the worker registry, then reserving new identities…")
        with self._lock:
            for index in range(max(1, min(int(count), MAX_AT_ONCE))):
                report_start(f"Creating worker {index+1} / {max(1, min(int(count), MAX_AT_ONCE))}…")
                model = options.get("model") or agent.config_model() or ""
                if options.get("effort") and "#" not in model:  # an effort is a record of its own, as with the others
                    model = f"{model}#{options['effort']}"
                # A new helper must never inherit attempts credited to an old identity.
                reserved = self._taken() | {x.agent_name for x in self.sessions.values()}
                reserved |= {r['name'] for r in self.net.store.all('SELECT name FROM agents')}
                reserved |= {r['agent'] for r in self.net.store.all('SELECT DISTINCT agent FROM helper_sessions')}
                name = helper_name(kind, model, reserved)
                s = self._record(slug, kind, name, self._label(kind, name, options), options)
                s.restarts = 0
                report_start(f"Launching {s.label}…")
                self._launch(s)
                ids.append(s.id)
        return ids

    def rename(self, sid: str, label: str) -> str:
        """Change the display name without changing tokens, claims or attribution."""
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80 or any(ord(c) < 32 for c in label):
            raise ValueError("Enter an agent name of 1–80 characters without control characters.")
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                raise ValueError("This agent no longer exists. Refresh the agents list.")
            s.label = label.strip()
            self._save(s)
            return s.label

    def retry_paused(self, sid, limit=5):
        from . import retry_paused
        with self._lock:
            session=self.sessions.get(sid)
            if session is None:raise ValueError('This agent no longer exists. Refresh the agents list.')
            if session.options.get('task')=='readable':raise ValueError('Retry paused functions applies to matching agents.')
            agent,_=self.net.local_agent(session.agent_name)
            return retry_paused.retry(self.net,session.slug,agent,limit)

    def set_selection(self, sid: str, policy: dict, per_session: int | None = None) -> dict:
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                raise ValueError("This agent no longer exists. Refresh the agents list.")
            if per_session is not None and (type(per_session) is not int or not 1 <= per_session <= 20):
                raise ValueError("Functions per session must be a whole number from 1 to 20.")
            policy = self.net.validate_selection(policy)
            if 'disabled_tools' not in policy and 'disabled_tools' in (s.options.get('selection') or {}):
                policy = {**policy, 'disabled_tools':s.options['selection']['disabled_tools']}
            policy = self.net.set_selection(s.slug, s.agent_name, policy)
            if per_session is not None:
                s.options["per_session"] = per_session
            s.options["selection"] = policy
            with self.net.store.tx() as db:
                kept = {k: v for k, v in s.options.items() if k != "api_key"}
                db.execute("UPDATE helpers SET options=? WHERE id=?", (json.dumps(kept), s.id))
            return policy

    def set_prompts(self, sid, overrides):
        from .prompt_settings import validate
        overrides = validate(overrides)
        with self._lock:
            session = self.sessions.get(sid)
            if session is None:
                raise ValueError('This agent no longer exists. Refresh the agents list.')
            session.options['prompts'] = overrides
            with self.net.store.tx() as db:
                kept = {k:v for k,v in session.options.items() if k != 'api_key'}
                db.execute('UPDATE helpers SET options=? WHERE id=?', (json.dumps(kept), session.id))

    def app_instructions(self, session):
        from .prompt_settings import for_session
        return for_session(self.net, session)['instructions']

    def instructions_for(self, agent, slug):
        for session in list(self.sessions.values()):
            if session.agent_name == agent.name and session.slug == slug:
                return self.app_instructions(session)
        from .prompt_settings import effective
        return effective(self.net, {})['instructions']

    def set_tool(self, sid, name, enabled):
        from .tool_policy import validate
        validate([name])
        if type(enabled) is not bool:
            raise ValueError('Tool enabled must be true or false.')
        with self._lock:
            session = self.sessions.get(sid)
            if session is None:
                raise ValueError('This agent no longer exists. Refresh the agents list.')
            policy = dict(session.options.get('selection') or {})
            blocked = set(policy.get('disabled_tools', []))
            blocked.discard(name) if enabled else blocked.add(name)
            return self.set_selection(sid, {**policy, 'disabled_tools':sorted(blocked)})

    def _launch(self, s: Session) -> None:
        self.net.set_selection(s.slug, s.agent_name, s.options.get("selection"))
        s.stop = threading.Event()
        s.state, s.why, s.todo, s.until, s.stopped_by, s.error = "starting", "", "", None, "", ""
        s.signed_out = s.throttled = s.limited = s.auth_failed = False
        s.configuration_error = False
        s.cooldown_observed = 0
        s.continuation_attempts, s.continuation_note = 0, ""
        s.started_at = time.time()
        if s.options.get('focus_id'):
            s.options.setdefault('focus_until',s.started_at+s.options['focus_minutes']*60)
            with self.net.store.tx() as db:
                db.execute('UPDATE helpers SET options=? WHERE id=?',(json.dumps({k:v for k,v in s.options.items() if k!='api_key'}),s.id))
        self._save(s)
        s.thread = threading.Thread(target=self._run, args=(s,), daemon=True, name=f"agent-{s.agent_name}")
        s.thread.start()

    def restart(self, sid: str) -> None:
        """Start a stopped or paused helper again, by hand."""
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                raise ValueError("no such helper")
            if s.state in LIVE:
                return
            if s.thread is not None and s.thread.is_alive():
                raise ValueError(f"{s.agent_name} is still stopping: try again in a moment")
            if s.agent_name in self._taken():
                raise ValueError(f"{s.agent_name} works on another game right now: start a new helper instead")
            options = self.options_now(s.kind, dict(s.options))
            if options is None:
                raise ValueError("its API endpoint was removed: start a new helper on another one")
            options.pop('focus_until',None)  # A manual restart explicitly begins a fresh time allowance.
            s.options, s.restarts = options, 0
            if s.options.get("task") != "readable":
                self._prepare(s.slug)
            self._launch(s)

    def resume(self) -> list[str]:
        """Resume saved helpers without holding the UI's agent-state lock during project synchronization."""
        ids = []
        with self._lock:
            pending = [s for s in self.sessions.values() if resumable(s)]
        for s in pending:
            if self._down:
                break
            try:
                self.net.project_row(s.slug)
            except NetworkError:
                continue
            options = self.options_now(s.kind, dict(s.options))
            if options is not None and options.get("task") != "readable":
                self._prepare(s.slug)
            with self._lock:
                if self._down or not resumable(s):
                    continue
                if options is None:
                    s.stopped_by, s.why = BY_ITSELF, "its API endpoint was removed"
                    self._save(s)
                    continue
                s.options = options
                self._launch(s)
                ids.append(s.id)
        return ids

    def _prepare(self, slug: str) -> None:
        """Before agents claim: free matches from identical code (twins, in the background: what they'd match is
        left for last meanwhile), and on a GameCube/Wii decomp where code has no source file yet, files for it
        (start_files, checked against the game's checksum), or they'd all share the few hard functions left."""
        try:
            report_start("Refreshing identical-function matches; saved-file verification may need to finish first…")
            self.net.twins.refresh(slug)
            report_start("Preparing background permutation work…")
            self.net.permuter.start(slug)  # near misses agents leave, on spare CPU
        except Exception:  # agents work without them
            log.exception("twins or the permuter didn't start for %s", slug)
        with self._lock:
            if slug in self._prep:
                return
        try:
            if not self.net.fork_slug(slug):
                return
            _, fp, cfg = self.net.forks.get(slug)
            if not (self.net.forks.uncovered(slug) or {}).get("functions") or not fp.linkable():
                return
        except Exception:  # agents work on what has files
            return
        with self._lock:
            if slug in self._prep:
                return
            done = self._prep[slug] = threading.Event()

        def run() -> None:
            try:
                self.net.forks.start_files(slug, log=lambda *_: None)
            except Exception:  # the agents still work on what has files
                log.exception("start_files failed for %s", slug)
            finally:
                done.set()
                with self._lock:
                    self._prep.pop(slug, None)

        threading.Thread(target=run, daemon=True, name=f"start-files-{slug}").start()

    def running(self) -> list[str]:
        """The helpers working right now."""
        return [s.id for s in self.sessions.values() if s.state in LIVE]

    def stop(self, sid: str | None = None, by: str = BY_YOU) -> None:
        """Stop one helper (by the user, unless `by` says otherwise), or all of them (no `sid`: the app is quitting,
        and they carry on when it starts again). Its function is free at once, not when its thread notices (an API
        helper waits on its model meanwhile, and its claim showed up as a "connected agent")."""
        by = BY_QUIT if sid is None else by
        for s in list(self.sessions.values()):
            if sid not in (None, s.id):
                continue
            if s.state in LIVE:
                s.stopped_by = by
                s.push('termination', 'App requested termination: '+{BY_YOU: 'user stopped this helper', BY_QUIT: 'app shutdown/restart'}.get(by, str(by)))
            if by == BY_QUIT and s.state in LIVE:
                from .session_recovery import checkpoint
                checkpoint(self.net, s)
                self._save(s)
            s.stop.set()
            proc = s.proc
            if proc is not None and proc.poll() is None:
                _terminate(proc)
            with self._lock:
                if s.state in LIVE:
                    s.state, s.stopped_by, s.until, s.todo = "stopped", by, None, ""
                    s.why = {BY_YOU: "stopped by you", BY_QUIT: "decompdecomp closed while it worked"}.get(by, s.why)
                    self._save(s)
            self._release(s)

    def stop_project(self, slug: str) -> None:
        """Stop every helper working on one project (by the user)."""
        for s in list(self.sessions.values()):
            if s.slug == slug and s.state in LIVE:
                self.stop(s.id)

    def remove(self, sid: str) -> None:
        """Stop a helper and forget its record (its matches stay credited to its name)."""
        self.stop(sid)
        with self.net.store.tx() as db:
            db.execute("DELETE FROM helpers WHERE id=?", (sid,))
            db.execute("DELETE FROM settings WHERE key=?", ("helper-conversation:" + sid,))
        self.sessions.pop(sid, None)

    def remove_paused(self, slug: str) -> int:
        """Forget only paused helpers on this project, without removing one restarted meanwhile."""
        with self._lock:
            paused = [s.id for s in self.sessions.values() if s.slug == slug and s.state == "paused"]
            for sid in paused:
                self.remove(sid)
        return len(paused)

    def costs(self, slug: str) -> list[dict]:
        """What each model's helpers spent on a project and what they matched (helper_sessions): which models to run
        more of. Cheapest per match first; models with no match last. A model spinning in the last SPIN_WINDOW gets
        `advice` (spin_advice) and the `advice_model` it is about, from its decompiling sessions only: polishing
        compiles matched code and matches nothing new, so a model run only to polish an Xbox game was told its 1,282
        compiles of a day had made no match. The GUI dismisses advice by model (#131): its sentence carries counts that
        change as helpers work."""
        query = ("SELECT kind, model, COUNT(*) AS sessions, SUM(usd) AS usd, SUM(tokens) AS tokens, SUM(matches) AS "
                 "matches, SUM(compiles) AS compiles, SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
                 "SUM(cache_write_tokens) AS cache_write_tokens, SUM(output_tokens) AS output_tokens FROM helper_sessions WHERE project=? AND started_at >= ? {} "
                 "GROUP BY kind, model")
        rows = self.net.store.all(query.format(""), slug, 0)
        recent = [dict(r) for r in self.net.store.all(query.format("AND task != 'readable'"), slug,
                                                      time.time() - SPIN_WINDOW)]
        advice = spin_advice(recent)
        out = []
        for r in rows:
            said = advice.get((r["kind"], r["model"]), "")
            out.append(dict(r) | {"usd": r["usd"] or 0.0, "tokens": r["tokens"] or 0, "matches": r["matches"] or 0,
                                  "advice": said, "advice_model": (r["model"] or r["kind"]) if said else ""})
        # a model without a price (the API helper counts only tokens) isn't free: priced models by dollars a match,
        # then the rest by tokens a match, then those that matched nothing
        return sorted(out, key=lambda r: (r["matches"] == 0, r["usd"] == 0,
                                          (r["usd"] or r["tokens"]) / r["matches"] if r["matches"] else 0))

    def forget_project(self, slug: str) -> None:
        """A project is removed: its helpers stop and their records go with it."""
        for s in list(self.sessions.values()):
            if s.slug == slug:
                self.remove(s.id)

    def shutdown(self) -> None:
        """The app quits: every helper stops (they carry on when the app starts again), each with its whole process
        group, and so does anything a helper started that left its group (an `opencode serve` service): nothing the
        app started keeps running, or spending, after it. Waits up to STOP_GRACE seconds for them to end."""
        if self._down:
            return
        self._down = True
        processes = [s.proc for s in list(self.sessions.values())]  # retain Windows handles until _end_groups returns
        groups = {proc.pid for proc in processes if proc is not None and proc.poll() is None}
        self.stop()
        _end_groups(groups | self._strays())
        try:
            self.on_stop()
        except Exception:  # the app quits anyway
            log.exception("closing rooms while quitting failed")

    def _strays(self) -> set[int]:
        """Process groups of processes this app's helpers started, found by HELPER_ENV in their environment."""
        if sys.platform == "darwin":
            try:
                processes = subprocess.run(["ps", "eww", "-axo", "pid=,pgid=,command="], capture_output=True,
                                           text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                return set()
            if processes.returncode:
                return set()
            mark = f"{HELPER_ENV}={self.home}"
            groups = set()
            for line in processes.stdout.splitlines():
                parts = line.split(None, 2)
                if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[0]) != os.getpid():
                    if mark + " " in parts[2] or parts[2].endswith(mark):
                        groups.add(int(parts[1]))
            return groups
        # Linux exposes child environments through /proc. On Windows there is no equivalent filesystem, so there
        # are no stray groups to recover here; the live sessions are still stopped through their Popen handles.
        if not PROCFS.is_dir():
            return set()
        mark = f"{HELPER_ENV}={self.home}".encode()
        out = set()
        for d in PROCFS.iterdir():
            if not d.name.isdigit() or int(d.name) == os.getpid():
                continue
            try:
                if mark in (d / "environ").read_bytes().split(b"\0"):
                    out.add(os.getpgid(int(d.name)))
            except (OSError, ProcessLookupError):
                continue
        return out

    def reset_metrics(self, sid: str) -> None:
        from .agent_metrics import reset
        reset(self, sid)

    def snapshot(self, since: dict | None = None, *, project: str | None = None) -> list[dict]:
        from .agent_metrics import results
        since = since or {}
        sessions = [s for s in list(self.sessions.values()) if project is None or s.slug == project]
        board = {}
        for slug in {s.slug for s in sessions}:
            selected = [s for s in sessions if s.slug == slug]
            # A global overview and a project poll can overlap. Share one refresh per project.
            with self._metrics_locks.setdefault(slug, threading.RLock()):
                cache = getattr(self, '_metrics_cache', {})
                key = tuple((s.id, s.agent_name, s.options.get('_metrics', {}).get('attempt', 0),
                             s.options.get('_metrics', {}).get('event', 0)) for s in selected)
                pid = self.net.project_row(slug)['id']
                revision = self.net.store.one('SELECT revision FROM history_revisions WHERE project_id=?', pid)
                signature = revision['revision'] if revision else 0
                old = cache.get(slug)
                if old is None or old[0] != (key, signature) or time.monotonic() - old[1] > 5:
                    old = ((key, signature), time.monotonic(), results(self.net, slug, selected))
                    cache[slug] = old
                board.update(old[2])
                self._metrics_cache = cache
        out = []
        for s in sessions:
            row = s.snapshot(int(since.get(s.id, 0)))
            records = self.net.store.all("SELECT * FROM helper_sessions WHERE helper_id=? AND agent=? AND ended_at IS NOT NULL",
                                         s.id, s.agent_name)
            estimates = [agents.get(s.kind).estimate_cost(r["model"], {
                k: r[k + "_tokens"] for k in TOKEN_KINDS} | {"tokens": r["tokens"]}) for r in records if r["tokens"]]
            if s.record and s.session_id not in {r["id"] for r in records} and s.usage["tokens"]:
                active = self.net.store.one("SELECT model FROM helper_sessions WHERE id=?", s.session_id)
                model = active["model"] if active else s.model
                estimates.append(agents.get(s.kind).estimate_cost(model, s.usage))
            recorded_tokens = sum(r["tokens"] for r in records)
            if s.record and s.session_id not in {r["id"] for r in records}:
                recorded_tokens += s.usage["tokens"]
            row["estimated_cost"] = (sum(estimates) if estimates and all(x is not None for x in estimates)
                                     and recorded_tokens == s.tokens else None)
            baseline = s.options.get('_metrics', {})
            row['metrics'] = board.get(s.agent_name, {})
            row['metrics_since'] = baseline.get('at')
            from .session_efficiency import for_sessions
            row['efficiency']=for_sessions(self.net,[r for r in records if r['started_at']>=baseline.get('at',0)])
            row['metrics_after'] = baseline.get('attempt', 0)
            row['lifetime_tokens'] = s.tokens
            for key in ('tokens', 'usd', 'runs', 'restarts'):
                row[key] = max(0, row[key] - baseline.get(key, 0))
            if baseline:
                # Provider totals may arrive only at session end. The display counts
                # usage reported after reset, never guesses how to split an active call.
                old_cost = baseline.get('estimated_cost')
                cost = row['estimated_cost']
                row['estimated_cost'] = (max(0, cost - old_cost) if cost is not None and old_cost is not None
                                         else 0 if row['tokens'] == 0 else None)
            out.append(row)
        return out

    def project_of(self, agent_id: int) -> str | None:
        return self.assignments.get(agent_id)

    # -- which agents are installed

    def detected(self) -> list[dict]:
        """The agents installed and their models, as last found, at once: finding out runs every agent's CLI (seconds),
        so it happens in the background, at start and on refresh_detected(). Before the first look ends: where the
        agents are, without their models."""
        with self._lock:
            found = self._found
        return detect(quick=True) if found is None else found

    def detecting(self) -> bool:
        return self._finding is not None and self._finding.is_alive()

    def refresh_detected(self, wait: bool = False) -> None:
        """Look again which agents are installed (an agent installed, a sign-in added a model), in the background."""
        def find() -> None:
            try:
                found = detect()
            except Exception:  # the launcher still shows what's installed
                log.exception("finding the installed agents failed")
                found = detect(quick=True)
            with self._lock:
                self._found = found

        with self._lock:
            if not self.detecting():
                self._finding = threading.Thread(target=find, daemon=True, name="detect-agents")
                self._finding.start()
            finding = self._finding
        if wait:
            finding.join()

    # -- the loop

    def _progress(self, agent_id: int) -> int:
        """Recorded attempts plus completed diagnostic builds; administrative events are not compilation."""
        row = self.net.store.one(
            "SELECT COUNT(*) AS n FROM attempts WHERE agent_id=?", agent_id)
        return row["n"] + getattr(self.net, "_session_diagnostic_compiles", {}).get(agent_id, 0)

    def _left(self, slug: str, task: str = "") -> int:
        """What the session's task has left in the project: open functions, or for polishing, matched ones it can
        still take (app/readable.py)."""
        proj = self.net.project_row(slug)
        if task == "readable":
            return self.net.readability.left(proj)
        row = self.net.store.one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='open'", proj["id"])
        return row["n"]

    def account(self, s: Session, usage: dict) -> None:
        """Add what a session spent to the helper, the session and the project (kept in its config)."""
        if s.record:
            s.record("usage", usage)
        if usage.get('coverage') in {'partial','complete'}:
            s.usage['coverage'] = usage['coverage']
        usd = float(usage.get("usd") or 0)
        parts = {k: int(usage.get(k) or 0) for k in TOKEN_KINDS}
        total = sum(parts.values()) or int(usage.get("tokens") or 0)
        for k, n in parts.items():
            s.usage[k] += n
        s.usage["usd"] += usd
        s.usage["tokens"] += total
        s.usage["turns"] += int(usage.get("turns") or 0)
        s.usd += usd
        s.tokens += total
        if usd or total:
            self.net.add_spend(s.slug, usd, total)

    def over_budget(self, slug: str) -> float | None:
        """The budget (USD) when the project's agents have spent it, else None."""
        cap = self.budget()
        spent = (self.net.spend(slug) or {}).get("usd", 0)
        return cap if cap and spent >= cap else None

    def _set(self, s: Session, state: str, why: str = "", until: float | None = None, todo: str = "") -> None:
        with self._lock:  # (stop() changes the state under it too)
            if s.stop.is_set() and state in LIVE:
                return  # stopped meanwhile: never back to work
            s.state, s.why, s.until, s.todo = state, why, until, todo
        self._save(s)

    def _wait(self, s: Session, why: str, until: float, todo: str = "") -> None:
        """Wait (unless stopped meanwhile), saying why and until when."""
        self._set(s, "waiting", why, until, todo)
        s.push("info", f"waiting until {datetime.fromtimestamp(until).strftime('%H:%M')}: {why}")
        s.stop.wait(max(0.0, until - time.time()))

    def _pause(self, s: Session, why: str, todo: str = "") -> None:
        """It can't work until the user does something: it stops trying, says why and what to do."""
        self._set(s, "paused", why, None, todo)
        s.push("error", f"paused: {why}" + (f" {todo}" if todo else ""))
        self.net.record_event(s.slug, "paused", why, s.agent_name)  # the notification centre tells the user

    def _finish(self, s: Session, why: str) -> None:
        """It stopped by itself: nothing to do here any more."""
        s.stopped_by = BY_ITSELF
        self._set(s, "stopped", why)
        s.push("info", f"stopping: {why}")
        self.net.record_event(s.slug, "stopped", why, s.agent_name)

    def _room_away(self, s: Session) -> bool:
        """In a room whose host is away (or that hasn't let us in yet): wait, don't fail."""
        link = self.net.links.get(s.slug)
        if link is None or (not link.error and time.time() - link.last_ok <= ROOM_STALE):
            return False
        why = f"waiting for the room's host: {link.error or 'not reachable right now'}"
        if s.state != "waiting" or s.why != why:
            s.push("info", why)
            self._set(s, "waiting", why)
        try:
            link.sync()
        except Exception:  # still away: look again in a while
            pass
        s.stop.wait(ROOM_RETRY)
        return True

    def _run(self, s: Session) -> None:
        from .focused_agents import run
        run(self,s)

    def _run_work(self, s: Session) -> None:
        from . import focused_agents
        agent, token = self.net.local_agent(s.agent_name)
        self.net.set_selection(s.slug, agent.name, s.options.get("selection"))
        self.assignments[agent.id] = s.slug
        focused_agents.bind(self.net,s,agent)
        if not hasattr(self.net, '_agent_tool_tasks'): self.net._agent_tool_tasks = {}
        self.net._agent_tool_tasks[agent.id] = 'polishing' if s.options.get('task') == 'readable' else 'matching'

        workdir = self.home / "agents" / s.agent_name
        workdir.mkdir(parents=True, exist_ok=True)
        s.push("info", f"{s.label} starting on {s.slug}")
        # Polishing consumes matched C, so matching preparation cannot unlock its work.
        polishing = s.options.get("task") == "readable"
        if not polishing and (prep := self._prep.get(s.slug)) is not None:
            self._set(s, "starting", "first giving the game's code without a source file one (about a minute)")
            prep.wait(PREP_WAIT)
        twins = getattr(getattr(self.net, "twins", None), "jobs", {}).get(s.slug)
        if not polishing and twins is not None and not twins.ordered.is_set():
            self._set(s, "starting", "first setting hand-written assembly aside and putting the functions in order "
                                     "(seconds; a minute the first time)")
            twins.ordered.wait(ORDER_WAIT)
        s.tool_handshake_failures = 0
        failures = fast = throttles = limits = auths = connection_failures = 0
        while not s.stop.is_set():
            if not focused_agents.ready(self,s,agent):continue
            if getattr(self, 'source_update_pending', False):
                why = 'Update ready: brief restart check (up to 15 seconds)'
                if s.why != why:
                    self._set(s, 'waiting', why, None,
                              'Your work is saved. Sessions resume after restart, or within 15 seconds if active work needs longer.')
                s.stop.wait(1)
                continue
            if self._left(s.slug, task := s.options.get("task") or "") == 0:
                self._finish(s, f"nothing left to {'polish' if task == 'readable' else 'decompile'} here 🎉")
                break
            policy = self.net.selection(s.slug, agent.name)
            if task != "readable" and (policy.get("match_below") is not None or policy.get("max_tries") or policy.get("model_untried")):
                if not self.net.selection_remaining(s.slug, agent, include_paused=True) and not self.net.store.one(
                        "SELECT 1 FROM claims WHERE agent_id=?", agent.id):
                    below = policy.get("match_below")
                    why = f"no functions remain below {below:g}% match" if below is not None else "no functions remain unchecked by this model" if policy.get("model_untried") else "no functions remain within max tries"
                    self._pause(s, why + " for this worker (unscored functions also count)",
                                "Change Work selection, then press Start again.")
                    break
            if not s.options.get('focus_id') and task != 'readable' and not self.net.selection_remaining(s.slug,agent) and not self.net.store.one(
                    'SELECT 1 FROM claims WHERE agent_id=?',agent.id):
                why, todo = self.net.selection_wait_reason(s.slug, agent)
                self._wait(s, why, time.time()+30, todo)
                continue
            if (cap := self.over_budget(s.slug)) is not None:
                self._finish(s, f"the agents on this project have spent the ${cap:.2f} budget (Setup)")
                break
            if self._room_away(s):
                continue
            if (broken := self._build_problem(s.slug)) is not None:
                self._pause(s, f"the project doesn't build on this computer, so no session starts: {broken}",
                            "Fix the project's setup (its page says what's wrong), then press Start again.")
                break
            from . import session_followups
            if (rejected := session_followups.configuration_error(self, s)):
                s.configuration_error = True
                self._pause(s, rejected, 'Choose another model; this configuration was rejected for this account. Restart the app after changing account access.')
                break
            if (shared_until := session_followups.cooldown(self, s)) is not None:
                s.cooldown_observed = shared_until
                self._wait(s, "This provider account's usage limit is reached", shared_until + session_followups.stagger(s),
                           "Helpers using this account resume automatically after reset.")
                continue
            s.runs += 1
            with self._lock:
                if getattr(self, "source_update_pending", False):
                    continue
                self._set(s, "working")
            began = time.time()
            before = self._progress(agent.id)
            activity_before = self.net.store.one('SELECT COUNT(*) AS n FROM events WHERE agent_id=?',agent.id)['n'] if task == 'readable' else 0
            row = self._session_begin(s)
            kind = agents.get(s.kind)
            try:
                ok = self._run_cli(s, token, workdir) if kind.command else kind.run(self, s, agent)
                while (ok and kind.command and kind.focus_resume and s.options.get('focus_id')
                       and s.options.get('focus_conversation') and not s.stop.is_set()
                       and not s.limited and not s.throttled and not s.auth_failed
                       and not focused_agents.completion(self,s)
                       and not getattr(self, 'source_update_pending', False)):
                    s.push('info','Continuing the same focused conversation; provider context compaction stays enabled.')
                    if s.stop.wait(SESSION_GAP):break
                    ok = self._run_cli(s, token, workdir)
            except ToolConnectionUnavailable as error:
                connection_failures += 1
                s.error = str(error)
                s.push('error', s.error)
                self._session_end(s, row, agent, began, 'connection_retry')
                delay = min(60, 5 * 2 ** min(connection_failures-1, 4))
                self._wait(s, f'Local tool connection unavailable; retry {connection_failures} in {delay}s. '+('The CLI session did not complete its handshake.' if s.tool_handshake_failures else 'No model request started.'),
                           time.time()+delay, 'This helper reconnects automatically; saved work is retained.')
                continue
            except Fatal as e:  # needs the user first: retrying can't help
                if agents.get(s.kind).fatal(str(e)):
                    s.configuration_error = True
                    session_followups.configuration_error(self, s, str(e))
                s.push("error", str(e))
                self._session_end(s, row, agent, began, "paused")
                s.error = str(e)
                self._pause(s, str(e), 'Choose another model in Add agents; this configuration cannot recover by retrying.' if s.configuration_error else '')
                break
            except Exception as e:  # keep the runner alive; show what happened
                log.exception("helper %s failed", s.agent_name)
                s.error = f"{type(e).__name__}: {e}"
                s.push("error", s.error)
                ok = False
            if ok and not s.stop.is_set() and not s.limited and not s.throttled and not s.auth_failed:
                from .session_recovery import completed
                completed(s, self.net)
                self._save(s)
            connection_failures = 0
            compiled = self._progress(agent.id) - before
            investigation = session_followups.investigation_outcome(self.net, agent, began) if compiled == 0 else None
            followup = ok and not investigation and session_followups.premature(self, s, agent, compiled)
            if compiled >= 2:
                s.continuation_attempts = 0
            if followup:
                s.continuation_attempts += 1
                s.continuation_note = session_followups.CONTINUATION
                s.push('info', 'Session ended early; continuing once with explicit compilation guidance.')
            readable_progress = task == 'readable' and self.net.store.one('SELECT COUNT(*) AS n FROM events WHERE agent_id=?',agent.id)['n'] > activity_before
            work_remains = self._left(s.slug,task) > 0
            if ok and not investigation and not followup and not readable_progress and work_remains and not s.stop.is_set() and not s.rotated and compiled == 0:
                s.error = "the session ended without compiling anything (its messages are in its details)"
                s.push("error", s.error)
                ok = False
            if investigation and ok:
                s.push("info", investigation+"; no compilation was needed for this report.")
            self._session_end(s, row, agent, began, "stopped" if s.stop.is_set() else "rotated" if s.rotated else "early_finish" if followup else "investigated" if ok and investigation else "ok" if ok else
                              "limit" if s.limited else "signin" if s.auth_failed else "failed")
            if s.stop.is_set():
                break
            if s.context_budget and s.context_budget.waiting:
                why,todo=self.net.selection_wait_reason(s.slug,agent)
                self._wait(s,why,time.time()+30,todo)
                continue
            if s.signed_out:
                self._pause(s, s.error)
                break
            if s.auth_failed:  # not a failure of the helper's: tried again a few times, then a new sign-in
                s.auth_failed, auths = False, auths + 1
                if auths >= AUTH_RETRIES:
                    self._pause(s, f"{kind.name} needs you to sign in again: its sign-in failed {auths} times in a "
                                   f"row ({s.error})", kind.signin)
                    break
                s.restarts += 1
                self._wait(s, f"{kind.name}'s sign-in failed ({s.error}); trying again, and if it keeps failing "
                              "this helper will ask you to sign in again", time.time() + AUTH_WAIT * 2 ** (auths - 1),
                           kind.signin)
                continue
            auths = 0
            if s.limited:  # the plan's allowance is used up for now: it resets, so wait as long as it takes
                limits += 1
                until = s.limit_until or time.time() + min(LIMIT_WAIT_LONGEST, LIMIT_WAIT_FIRST * 2 ** (limits - 1))
                until = session_followups.remember_limit(self, s, until + LIMIT_RESET_MARGIN)
                s.cooldown_observed = until
                s.limited, s.limit_until = False, None
                s.restarts += 1
                self._wait(s, f"{kind.name}'s usage limit is reached", until + session_followups.stagger(s),
                           "it resets by itself; this helper carries on then")
                continue
            if s.throttled and throttles < THROTTLES_IN_A_ROW:  # the provider's limit, not the agent's failure
                s.throttled, throttles = False, throttles + 1
                self._wait(s, "the model's provider says too many requests at once", time.time() + THROTTLE_WAIT,
                           "fewer agents at once on this account would wait less")
                continue
            throttles = 0
            if ok:
                failures = fast = limits = 0
                self._set(s, "waiting", "Session ended early; continuing with compilation guidance." if followup else "Session completed; helper is continuing. The next session starts in a moment.")
                s.stop.wait(SESSION_GAP)
                continue
            failures += 1
            fast = fast + 1 if time.time() - began < FAST_FAILURE else 0
            if fast >= FAST_FAILURES_TO_PAUSE:
                hint = kind.signin if _SIGNIN.search(s.error) else ""  # not after every failure: it misleads
                self._pause(s, f"it failed {fast} times in a row, each soon after it started: {s.error}", hint)
                break
            s.restarts += 1
            self._wait(s, f"starting again after an error: {s.error}",
                       time.time() + min(BACKOFF_LONGEST, BACKOFF_FIRST * 2 ** (failures - 1)))
        s.proc = None
        self._save(s)
        self._release(s, agent)

    def _build_problem(self, slug: str) -> str | None:
        """Before each session: why the project can't build here, after its setup ran again (ForkEngine.preflight),
        or None. Helpers spent their compiles on a build.ninja pointing at another home's python, then quit."""
        try:
            if not self.net.fork_slug(slug):
                return None
            return self.net.forks.preflight(slug, log=lambda m: log.info("%s", m))
        except Exception:  # the check itself failing mustn't stop the helpers
            log.exception("pre-flight of %s failed", slug)
            return None

    def _release(self, s: Session, agent=None) -> None:
        """Free the function a helper holds (in a room, the host holds the real claim). A stop may happen before its
        runner notices, so recover the helper's identity to tell the host too."""
        if agent is None:
            row = self.net.store.one("SELECT id, name, owner, trusted FROM agents WHERE name=?", s.agent_name)
            if row is None:
                return
            agent = network.Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))
        agent_id = agent.id
        held = self.net.store.one("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                                  agent_id)
        link = self.net.links.get(s.slug)
        if held and link is not None:
            try:
                link.call("/collab/release", agent, function=held["name"], notes="")
            except Exception:  # the host lets the claim expire
                pass
        # its claims go now, with a note, rather than when their lease runs out; and its next run starts afresh
        self.net.release_stopped([agent_id], (s.why or "it stopped")[:200])

    def _session_begin(self, s: Session) -> int:
        from .prompt_settings import effective
        s.prompt_config = effective(self.net, s.options)
        s.startup_context = None
        s.usage = _fresh_usage()
        with self.net.store.tx() as db:
            row = db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, task) "
                              "VALUES(?,?,?,?,?,?,?)", (s.id, s.slug, s.kind, s.model or agents.get(s.kind).config_model() or "",
                                                      s.agent_name, time.time(), s.options.get("task") or "")).lastrowid
        from .session_efficiency import context
        snapshot=context(self.net,s.slug,s.options)
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('session-efficiency:'+str(row),json.dumps(snapshot)))
        s.session_id = row
        from .session_budget import start
        agent_row=self.net.store.one('SELECT id FROM agents WHERE name=?',s.agent_name)
        s.context_budget=start(self.net,agent_row['id'],s.slug,s.options) if agent_row else None
        s.rotated=False
        s.record = lambda kind, value: self.transcripts.append(row, kind, value, (s.options.get('api_key'),))
        return row

    def record_agent(self, agent, slug, kind, value):
        for session in list(self.sessions.values()):
            if session.agent_name == agent.name and session.slug == slug and session.record:
                session.record(kind, value)
                break

    def transcript(self, slug, helper, session=0, after=0, before=0, whole=False):
        result = self.transcripts.read(slug, helper, session, after, before, whole=whole)
        for live in list(self.sessions.values()):
            if result['session'] and live.session_id == result['session']['id'] and live.record:
                for key in TOKEN_KINDS:
                    result['session'][key + '_tokens'] = live.usage[key]
                result['session']['usd'] = live.usage['usd']
                result['session']['tokens'] = live.usage['tokens']
                result['session']['usage_coverage'] = live.usage.get('coverage','unknown')
        if result.get('session'):
            from .session_efficiency import for_sessions
            result['efficiency']=for_sessions(self.net,[result['session']]) if result['session']['ended_at'] else []
        return result

    def _session_end(self, s: Session, row: int, agent, began: float, outcome: str) -> None:
        """What a session did and spent, on its row: to compare models on cost per match."""
        compiles = self.net.store.one("SELECT COUNT(*) AS n FROM attempts WHERE agent_id=? AND created_at>=?",
                                      agent.id, began)["n"]
        matches = self.net.store.one("SELECT COUNT(*) AS n FROM events WHERE agent_id=? AND kind='match' AND "
                                     "created_at>=?", agent.id, began)["n"]
        from .session_handoffs import capture
        handoff = capture(self.net, agent, s.slug, began, session=row)
        if s.record:
            s.record('handoff', handoff)
        u = s.usage
        with self.net.store.tx() as db:
            db.execute("UPDATE helper_sessions SET ended_at=?, outcome=?, usd=?, input_tokens=?, output_tokens=?, "
                       "cache_read_tokens=?, cache_write_tokens=?, tokens=?, turns=?, tool_calls=?, compiles=?, "
                       "matches=? WHERE id=?", (time.time(), outcome, u["usd"], u["input"], u["output"],
                                                u["cache_read"], u["cache_write"], u["tokens"], u["turns"],
                                                u["tool_calls"], compiles, matches, row))
        with self.net.store.tx() as db:
            db.execute('UPDATE helper_sessions SET usage_coverage=? WHERE id=?', (s.usage.get('coverage','unknown'),row))
        from .session_efficiency import finish
        finish(self.net,agent,s.slug,row,began)
        spent = f", ${u['usd']:.2f}" if u["usd"] else f", {u['tokens'] // 1000}k tokens" if u["tokens"] else ""
        ending = 'ended early; helper continues with compilation guidance' if outcome == 'early_finish' else 'completed; helper continues' if outcome in ('ok', 'rotated', 'investigated') and not s.stop.is_set() else 'ended ('+outcome+')'
        s.push("info", f"session {s.runs} {ending}: {u['tool_calls']} tool calls, {compiles} compiles, {matches} "
                       f"matched{spent}")
        self._save(s)
        from .. import agent_notes
        with self.net.store.tx() as db:
            agent_notes.finish(db, row)
        s.record = None

    def prepare_session(self, s: Session, agent=None, *, token=None) -> None:
        """Preload facts and one ordinary claim, once; previews never claim work."""
        if s.startup_context is not None:
            return
        if agent is None:
            agent = self.net.authenticate(token)
        if (agent.id, s.slug) in getattr(self.net, '_pending_tool_handshakes', set()):
            s.startup_context = 'Connection check required: first call project_info(detail=false). Then claim_function to receive your function. No new claim has been handed over by this startup.'
            return
        facts = tools._project_info(self.net, s.slug, detail=False).split('Next:')[0][:STARTUP_FACTS_CHARS]
        parts = ['App-provided startup context (already fetched; no project_info call needed):', facts]
        if s.options.get('task') != 'readable':
            # A fresh provider session has no copy of a previous session's brief/C.
            getattr(self.net, '_claim_packets', {}).pop((agent.id, s.slug), None)
            packet = tools._claim(self.net, agent, s.slug, '', '', count=1)
            if len(packet) > STARTUP_BRIEF_CHARS:
                packet = packet[:STARTUP_BRIEF_CHARS].rsplit('\n', 1)[0] + '\n[Startup brief abbreviated. Read get_function(section=brief) or get_candidate for omitted evidence before editing.]'
            parts += ['The app already called claim_function(count=1) for you. Start with the brief below. '
                      'Do not claim again before working on or releasing this function. '
                      'If no function was available, do not invent one; end the session.', packet]
        s.startup_context = '\n\n'.join(parts)

    def prompt(self, s: Session) -> str:
        from . import focused_agents
        from .session_handoffs import previous
        text = (focused_agents.prompt(s) if s.options.get('focus_id') else self._prompt(s)) + previous(self.net, s.id, s.slug)
        if s.options.get('focus_id'):
            text += (f"\nUSER FOCUS: Work only on {s.options['focus_function']} until the app's deadline. "
                     'Do not move to other functions, even after repeated failures. Read callees as needed, save improved candidates and notes. '
                     'Keep this conversation running; compact older context rather than ending the session. The app stops this worker on a match or timeout.\n')
        if s.continuation_note:
            text += '\n\nSession continuation guidance:\n' + s.continuation_note
            s.continuation_note = ""
        # Keep detailed guides on demand; the work prompt already teaches the matching loop.
        ov = self.net.overview(s.slug)  # (only its platform's parts: tools.for_platform)
        text += "\nGuides to load when needed:\n\n" + tools.up_front(
            ov.get("platform") or "", ov.get("kind") or "", ov["compiler"]["toolchain"])
        knowledge_agent = self.net.store.one('SELECT id,name,owner,trusted FROM agents WHERE name=?',s.agent_name)
        knowledge_agent = network.Agent(knowledge_agent['id'],knowledge_agent['name'],knowledge_agent['owner'],bool(knowledge_agent['trusted'])) if knowledge_agent else None
        if (known := self.net.knowledge.for_prompt(s.slug, knowledge_agent)):
            text += f"\n{known}\n"
        if (wish := self.net.instructions(s.slug)):
            text += f"\nThe user's instructions for this project (they come first):\n{wish}\n"
        if s.options.get('task') != 'readable':
            from . import agent_tokens, local_experiments
            row = self.net.store.one('SELECT id,name,owner,trusted FROM agents WHERE name=?',s.agent_name)
            agent = network.Agent(row['id'],row['name'],row['owner'],bool(row['trusted'])) if row else None
            if agent:
                held = self.net.store.all('SELECT f.* FROM functions f JOIN claims c ON c.function_id=f.id '
                                          'JOIN projects p ON p.id=f.project_id WHERE c.agent_id=? AND p.slug=? LIMIT 6',agent.id,s.slug)
                for fn in held:
                    source,revision = agent_tokens.candidate(self.net,agent,s.slug,fn['name'])
                    text += '\n'+fn['name']+': '+local_experiments.summary(self.net,agent,s.slug,fn,source,revision)
        if s.startup_context:
            text += '\n\n' + s.startup_context
        return text

    def _prompt(self, s: Session) -> str:
        from .prompt_settings import for_session
        templates = for_session(self.net, s)
        ov = self.net.overview(s.slug)
        count = s.options.get("per_session", PER_SESSION)
        if s.options.get("task") == "readable":  # (in either mode: the step that differs is the mode's)
            return templates['initial_readable'].format(title=ov["title"], count=count, names=tools.READABLE_FORK
                                                if ov.get("kind") == "fork" else tools.READABLE_ROM)
        if ov.get("kind") == "fork":
            guides = ["fork", "objdiff", *consoles.get(ov.get("platform")).guides]
            platform = ov.get("platform_name") or "its platform"
            what = tools.FORK_SCRATCH if ov.get("origin") == "scratch" else tools.FORK_PUBLIC
            return templates['initial_project'].format(title=ov["title"], what=what.format(platform=platform),
                                            count=count, budget=network.stall_budget_text(),
                                            guides=", ".join(f'guide("{g}")' for g in guides))
        guide = consoles.get(ov.get("platform")).up_front_guide(ov["compiler"]["toolchain"])
        return templates['initial_rom'].format(title=ov["title"], count=count, budget=network.stall_budget_text(),
                                        compiler_guide=guide)

    def _command(self, s: Session, token: str, workdir: Path) -> tuple[list[str], dict, str | None]:
        """The agent's command line, environment and what goes in on stdin (the prompt; None when its prompt went in
        a file). Each helper connects only to this app's own MCP server, with its own token."""
        k = agents.get(s.kind)
        exe = _which(k.command)
        if not exe:
            raise RuntimeError(f"{k.name} is not installed")
        # zcode has no stdin prompt: its session command attaches a file, and this writes the prompt into it. Every
        # other agent leaves prompt_file at None and keeps getting the prompt on stdin. Written before the command:
        # a prompt zcode cannot attach fails the session before its settings file exists, and pauses the helper
        # instead of retrying a prompt only the user can shorten.
        if s.options.get('focus_id') and s.options.get('focus_conversation') and k.focus_resume:
            from .focused_agents import prompt as focus_prompt
            prompt = focus_prompt(s) + '\nContinue from your existing context; retrieve current saved C if needed.'
        else:
            prompt = self.prompt(s)
        from .session_recovery import handoff
        prompt += handoff(s)
        if s.options.get('resume_conversation') and k.focus_resume:
            prompt += '\nResume this conversation after interruption. Continue from saved context, checking current state first.\n'
        if s.tool_handshake_required:
            prompt += '\nFirst call project_info(detail=false) to confirm tools in this CLI session, before claiming or editing. If unavailable, end immediately and report the connection failure. The app will retry once.'
        try:
            prompt_file = k.prompt_file(workdir, prompt)
        except ValueError as e:
            raise Fatal(str(e)) from e
        cmd, more = (k.focused_session_command(exe, workdir, self.mcp_url, token, s.model, s.options.get('focus_conversation') if s.options.get('focus_id') else s.options.get('resume_conversation'))
                     if k.focus_resume or s.options.get('focus_id') else k.session_command(exe, workdir, self.mcp_url, token, s.model))
        cmd = k.launch_command(agents.cli_argv(cmd))
        # PWD too: opencode 2 works in $PWD, not the folder it starts in, and the app's PWD was the user's home, so
        # Mario Kart 64's helpers worked there without their folder's settings: no tools, and a free shell
        env = dict(os.environ, PWD=str(workdir), **{HELPER_ENV: str(self.home)}, **more)
        return cmd, env, None if prompt_file is not None else k.stdin(prompt)

    def _preflight_tools(self, token: str) -> None:
        """Check local authenticated wiring, without building project facts or making a model request."""
        phase = 'local authentication'
        began = time.monotonic()
        async def check():
            nonlocal phase
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client

            phase = 'opening MCP transport'
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}", "X-Decomp-Connection-Check":"1"}) as http:
                async with streamable_http_client(self.mcp_url, http_client=http) as streams:
                    async with ClientSession(*streams[:2]) as client:
                        phase = 'initializing MCP'
                        await client.initialize()
                        phase = 'listing tools'
                        tools = await client.list_tools()
                        names = {tool.name for tool in tools.tools}
                        if not {"project_info", "claim_function", "compile_and_diff", "release_function"} <= names:
                            raise Fatal('The decomp MCP server is missing required tools. Review tool settings before restarting this helper.')
                        phase = 'authenticated project check'
                        result = await client.call_tool('project_info', {'detail': False})
                        if result.is_error:
                            message = ' '.join(getattr(block, 'text', '') for block in getattr(result, 'content', ()))
                            raise Fatal('Authenticated tool check rejected: '+(message[:400] or 'project access was denied'))
                        phase = 'closing MCP transport'
        try:
            self.net.authenticate(token)
        except NetworkError as error:
            raise Fatal('Local helper authentication failed: '+connection_failure(error, token)+'. No model request was started.') from error
        try:
            asyncio.run(asyncio.wait_for(check(), timeout=15))
        except Exception as error:
            detail = f'Tool connection check failed during {phase} after {time.monotonic()-began:.1f}s: '+connection_failure(error, token)
            # Context-manager cleanup can wrap permanent errors inside an exception group.
            def permanent(exc):
                return (isinstance(exc, Fatal) or getattr(getattr(exc, 'response', None), 'status_code', None) in (401,403,404)
                        or any(permanent(child) for child in getattr(exc, 'exceptions', ())))
            if permanent(error):
                raise Fatal(detail+'. No model request was started.') from error
            raise ToolConnectionUnavailable(detail+'. No model request was started; reconnecting automatically.') from error

    def _run_cli(self, s: Session, token: str, workdir: Path) -> bool:
        k = agents.get(s.kind)
        if why := k.validate_model(s.model):
            raise Fatal(why)
        self._preflight_tools(token)
        if s.context_budget and s.context_budget.waiting:
            s.rotated = True
            s.push('info','No eligible work; waiting without starting a model request.')
            return True
        agent = self.net.authenticate(token)
        pending = self.net.__dict__.setdefault('_pending_tool_handshakes', set())
        if self.net.selection_remaining(s.slug, agent, available_only=True) or self.net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id):
            pending.add((agent.id, s.slug))
            s.tool_handshake_required = True
        self.prepare_session(s, token=token)
        if s.context_budget and s.context_budget.waiting:
            s.rotated=True
            s.push('info','No eligible work; waiting without starting a model request.')
            return True
        cmd, env, prompt = self._command(s, token, workdir)
        if s.record:
            previous = s.record
            s.record = lambda kind, value: previous(kind, (value if isinstance(value, str) else
                                                          json.dumps(value, ensure_ascii=False, indent=2))
                                                   .replace(token, '[redacted]'))
            s.record('prompt', prompt)
            s.record('instructions', self.app_instructions(s))
        s.push("info", f"session {s.runs}: {k.name} started")
        if os.name == "nt":
            import sys

            cmd = [sys.executable, "-I", str(Path(__file__).with_name("winhelper.py")), *cmd]
        # its own session and process group: stopping it ends everything it started
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                start_new_session=True, creationflags=NO_WINDOW)
        if os.name == "nt":
            from ..winprocess import Job

            try:
                job = Job(proc)
            except OSError:
                log.warning("couldn't assign helper %s to a Windows job; stopping it will use taskkill", proc.pid,
                            exc_info=True)
            else:
                with _jobs_lock:
                    _windows_jobs[proc.pid] = job

                def reap_job():
                    proc.wait()
                    _close_windows_job(proc.pid)
                threading.Thread(target=reap_job, daemon=True, name=f"job-{proc.pid}").start()
        s.proc, s.pid_start = proc, _proc_start(proc.pid)
        self._save(s)  # its process, should the app crash: the next start ends it
        errors: collections.deque = collections.deque(maxlen=20)

        def feed_prompt():
            try:
                _feed_stdin(proc, prompt)
            except OSError:  # it exited before reading (stderr says why)
                pass

        def drain_err():
            for line in proc.stderr:
                if (text := k.clean_stderr(line)) and (not errors or errors[-1] != text):
                    errors.append(text)
        threading.Thread(target=feed_prompt, daemon=True).start()
        stderr_thread = threading.Thread(target=drain_err, daemon=True)
        stderr_thread.start()
        saw_tool = False
        said: collections.deque = collections.deque(maxlen=20)
        state: dict = {}
        last_error = refused = ""
        for line in proc.stdout:
            for kind, text in k.parse_line(line, state):
                if kind == "conversation":
                    from .session_recovery import conversation
                    if k.focus_resume and conversation(s, text):
                        self._save(s)
                    continue
                if kind == "usage":
                    self.account(s, json.loads(text))
                    continue
                if kind == "tool":
                    saw_tool = True
                    s.usage["tool_calls"] += 1
                said.append(text)
                if kind == "error":
                    last_error = text
                    refused = refused or (text if k.fatal(text) else "")
                s.push(kind, text)
            if not s.options.get('focus_id') and s.context_budget and s.context_budget.ready() and not s.stop.is_set():
                s.rotated=True
                s.push('info','No eligible work; ending this conversation so the app can wait without model requests.' if s.context_budget.waiting else 'Starting a fresh session to keep context small; saved work and claims are retained.')
            if s.stop.is_set() or s.rotated:
                reason = 'budget rotation' if s.rotated else 'stop request ('+str(s.stopped_by)+')'
                s.push('termination', 'App requested termination: '+reason)
                _terminate(proc)
                break
        code = proc.wait()
        _close_windows_job(proc.pid)
        # Fast failures may close stdout before the stderr reader receives the sign-in error.
        # Bound the wait in case a descendant inherited the pipe.
        stderr_thread.join(timeout=1)
        s.proc = None
        if s.stop.is_set() or s.rotated:
            return True
        everything = " ".join([*said, *errors])
        if (k.focus_resume and proc.returncode and (s.options.get('resume_conversation') or s.options.get('focus_conversation'))
                and re.search(r'(?:no (?:session|conversation|thread) found|(?:session|conversation|thread).{0,100}(?:not found|does not exist|could not be found))', everything, re.I)):
            s.options.pop('resume_conversation', None)
            s.options.pop('focus_conversation', None)
            self._save(s)
            s.push('info','Saved CLI conversation is unavailable. Continuing with saved function handoff and current project state.')
            return False

        if refused:
            raise Fatal(refused + " Review the agent settings and resolve this error before restarting.")  # every retry would meet it the same way
        if LIMIT.search(everything):
            s.limited = True  # whatever the exit code: Claude Code says so in an ordinary result
            s.limit_until = limit_reset(everything)
            # Publish the account cooldown before closing this session, so siblings
            # do not launch another paid request while its exit is being recorded.
            from . import session_followups
            session_followups.remember_limit(self, s, (s.limit_until or time.time()+LIMIT_WAIT_FIRST)+LIMIT_RESET_MARGIN)
            s.error = "Provider usage limit reached; waiting for reset."
            s.push('info', s.error)
            return False
        elif code != 0 and (why := no_credit(everything)):
            raise Fatal(why)  # every agent on this account fails the same way until it's topped up
        if (agent.id, s.slug) not in pending and re.search(r"invalid API token|(?:decomp tools|decomp MCP server)[^\n]{0,100}(?:not available|aren't available|not connected|unavailable)|none of the decomp tools|no decomp tools", everything, re.I):
            raise Fatal("The helper lost access to the app's tools. Restart this helper to reconnect; repeated paid retries are paused.")
        if "Toolchain infrastructure failure" in everything:
            raise Fatal("The compiler/converter setup failed. Fix the toolchain before restarting this helper; repair retries are paused.")
        if code != 0 and THROTTLED.search(everything):
            s.throttled = True
        if code != 0 and _SIGNED_OUT.search(everything):
            s.signed_out = True
            s.error = f"{k.name} isn't signed in on this computer. {k.signin}"
            s.push("error", s.error)
            return False
        if (code != 0 or not saw_tool) and AUTH_FAILED.search(everything):
            s.auth_failed = True
            said_it = next((t for t in [*errors, *said] if AUTH_FAILED.search(t)), "")
            s.error = said_it.strip()[:200] or "its sign-in failed"
            s.push("error", f"{k.name}'s sign-in failed (exit code {code}): {s.error}")
            return False
        if s.throttled:
            return False  # provider concurrency backoff must not spend the handshake retry
        if (agent.id, s.slug) in pending and not s.limited and not s.auth_failed:
            s.tool_handshake_failures += 1
            message = 'CLI tool handshake was not completed: the server preflight passed, but this agent never successfully called project_info. No new function was handed over.'
            if s.tool_handshake_failures <= 1:
                raise ToolConnectionUnavailable(message+' Reconnecting once; this requires another model request.')
            raise Fatal(message+' Automatic retries paused after two attempts. Review the CLI MCP startup diagnostics.')
        s.tool_handshake_failures = 0
        if s.tool_handshake_required and (agent.id, s.slug) not in pending:
            saw_tool = True  # the authenticated server observed the actual CLI, even if its stdout omitted tool events
        if code != 0:
            if code < 0:
                try:
                    signal_name = signal.Signals(-code).name
                except ValueError:
                    signal_name = str(-code)
                s.push('termination', f'Helper exited from signal {signal_name}; no app termination request recorded on this path. '
                       'An external kill or resource pressure is possible; the signal alone does not identify the cause.')
            tail =" · ".join(list(errors)[-3:]) or last_error or f"exited with code {code}"
            s.error = tail[:300]
            s.push("error", f"exited with code {code}: {tail[:600]}" if tail != f"exited with code {code}" else tail)
            return False
        if not saw_tool:
            s.error = "the agent never used the decomp tools" + (f": {list(errors)[-1]}" if errors else "")
            s.push("error", s.error)
            return False
        s.error = ""
        return True
