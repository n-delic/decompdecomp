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

import collections
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decompnet.hostenv import NO_WINDOW

from .. import agents, consoles, network
from ..network import NetworkError
from . import tools
from .engine import RomNetwork

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


# An agent's error that's about signing in or keys: then the pause says how to sign in
_SIGNIN = re.compile(r"auth|log ?in|sign(ed)? ?in|credential|api.?key|unauthori[sz]ed|\b401\b|\b403\b", re.I)

# Claims per helper session. Each session starts with a small, clean context (a function's assembly, drafts and
# diffs would otherwise ride along into the next one), but pays for its system prompt, tools and guides again:
# with 2 per session that was most of what small functions cost.
PER_SESSION = 8
MAX_AT_ONCE = 8  # helpers started with one click
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
      matches INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_helper_sessions_model ON helper_sessions(project, model)",
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
                         r"token (?:has )?expired", re.I)
AUTH_RETRIES = 3  # failed sign-ins in a row before the helper pauses and asks for a new one
AUTH_WAIT = 60  # seconds before the first try again, doubling
# what a model's provider says when the account has no credit left (DeepSeek: "Insufficient Balance", OpenAI:
# "insufficient_quota", Anthropic: "credit balance is too low", OpenCode: "quota exceeded"): trying again can't help
# until it's topped up
_NO_CREDIT = re.compile(r"insufficient[ _](?:balance|credits?|funds|quota)|exceeded your current quota|"
                        r"credit balance is too low|payment required|out of credits?", re.I)
# and when it's asked too much at once (HTTP 429): it works again in a while
THROTTLED = re.compile(r"too many requests|rate[ _-]?limit|concurrency limit|\b429\b|overloaded", re.I)
# and when a subscription's allowance is used up for now (Claude Code: "usage limit reached", "You've hit your
# session limit", Codex: "You've hit your usage limit", Antigravity: RESOURCE_EXHAUSTED, OpenCode Go: "quota
# exceeded"): it resets by itself in hours, so a helper waits for it instead of stopping (a night's run lost most of
# its helpers that way; Claude Code's "session limit" wording once paused them as five fast failures)
LIMIT = re.compile(r"(?:usage|session|weekly) limit|(?<!rate )(?<!rate-)(?<!rate_)limit (?:reached|exceeded)|"
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
    return (f"the model's provider says the account is out of credit (“{m.group(0)}”): top it up, then "
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
    if os.name == "nt":
        directories.append(Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "npm")
        directories.append(Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "agy" / "bin")
    for d in directories:
        if os.name == "nt":
            found = shutil.which(cmd, path=str(d))
            if found:
                return found
        if (d / cmd).exists():
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

def _fresh_usage() -> dict:
    return {"usd": 0.0, "tokens": 0, "turns": 0, "tool_calls": 0} | {k: 0 for k in TOKEN_KINDS}


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
    error: str = ""  # the last error
    feed: collections.deque = field(default_factory=lambda: collections.deque(maxlen=FEED_LINES))
    seq: int = 0
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
    limit_until: float | None = None  # when the agent said that limit resets

    @property
    def model(self) -> str:
        return str(self.options.get("model") or "")

    def push(self, kind: str, text: str) -> None:
        self.seq += 1
        self.feed.append({"seq": self.seq, "t": time.time(), "type": kind, "text": text[:FEED_TEXT]})
        if kind == "text":
            self.last_message = text[:LAST_WORDS]
        if kind in ("info", "error"):  # starts, waits, stops and errors outlive the session: why a night went as it did
            log.info("helper %s (%s): %s", self.agent_name, self.slug, text[:500])

    def snapshot(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "kind_name": agents.get(self.kind).name, "label": self.label,
                "task": self.options.get("task") or "",  # "readable": it polishes matched code
                "slug": self.slug, "agent": self.agent_name, "model": self.model, "state": self.state, "why": self.why,
                "todo": self.todo, "until": self.until, "stopped_by": self.stopped_by,
                "last_message": self.last_message, "runs": self.runs, "restarts": self.restarts, "error": self.error,
                "started_at": self.started_at, "usd": round(self.usd, 4), "tokens": self.tokens,
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
        self.sessions: dict[str, Session] = {}
        self.assignments: dict[int, str] = {}  # agent id -> project slug
        self._lock = threading.RLock()
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
                if r["pid"] and _proc_start(r["pid"]) == r["pid_start"]:  # still running: the app crashed
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
                       "started_at=?, restarts=?, sessions=?, usd=?, tokens=?, pid=?, pid_start=? WHERE id=?",
                       (s.label, s.state, s.why, s.todo, s.until, s.stopped_by, s.last_message, s.started_at,
                        s.restarts, s.runs, s.usd, s.tokens, pid, s.pid_start if pid else None, s.id))

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
            rid = db.execute("INSERT INTO helpers(project, kind, model, name, label, options, state, started_at) "
                             "VALUES(?,?,?,?,?,?,?,?)", (slug, kind, str(options.get("model") or ""), name, label,
                                                         json.dumps(kept), "starting", time.time())).lastrowid
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
        options = dict(options or {})
        agent = agents.get(kind)  # (an unknown kind is a ValueError that says which there are)
        if agent.id != kind:
            kind = agent.id
            options.pop("model", None)  # a Gemini CLI model name may mean nothing to Antigravity: use its default
        if self._down:
            raise ValueError("decompdecomp is quitting")
        ids = []
        self._prepare(slug)
        with self._lock:
            for _ in range(max(1, min(int(count), MAX_AT_ONCE))):
                model = options.get("model") or agent.config_model() or ""
                if options.get("effort") and "#" not in model:  # an effort is a record of its own, as with the others
                    model = f"{model}#{options['effort']}"
                name = helper_name(kind, model, self._taken())
                s = self._record(slug, kind, name, self._label(kind, name, options), options)
                s.restarts = 0
                self._launch(s)
                ids.append(s.id)
        return ids

    def _launch(self, s: Session) -> None:
        s.stop = threading.Event()
        s.state, s.why, s.todo, s.until, s.stopped_by, s.error = "starting", "", "", None, "", ""
        s.signed_out = s.throttled = s.limited = s.auth_failed = False
        s.started_at = time.time()
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
            s.options, s.restarts = options, 0
            self._prepare(s.slug)
            self._launch(s)

    def resume(self) -> list[str]:
        """Helpers the app stopped by quitting carry on, as their options are now (options_now)."""
        ids = []
        with self._lock:
            for s in list(self.sessions.values()):
                if s.state != "stopped" or s.stopped_by != BY_QUIT:
                    continue
                try:
                    self.net.project_row(s.slug)
                except NetworkError:
                    continue
                options = self.options_now(s.kind, dict(s.options))
                if options is None:
                    s.stopped_by, s.why = BY_ITSELF, "its API endpoint was removed"
                    self._save(s)
                    continue
                s.options = options
                self._prepare(s.slug)
                self._launch(s)
                ids.append(s.id)
        return ids

    def _prepare(self, slug: str) -> None:
        """Before agents claim: free matches from identical code (twins, in the background: what they'd match is
        left for last meanwhile), and on a GameCube/Wii decomp where code has no source file yet, files for it
        (start_files, checked against the game's checksum), or they'd all share the few hard functions left."""
        try:
            self.net.twins.refresh(slug)
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
        `advice` (spin_advice)."""
        query = ("SELECT kind, model, COUNT(*) AS sessions, SUM(usd) AS usd, SUM(tokens) AS tokens, SUM(matches) AS "
                 "matches, SUM(compiles) AS compiles FROM helper_sessions WHERE project=? AND started_at >= ? "
                 "GROUP BY kind, model")
        rows = self.net.store.all(query, slug, 0)
        recent = [dict(r) for r in self.net.store.all(query, slug, time.time() - SPIN_WINDOW)]
        advice = spin_advice(recent)
        out = [dict(r) | {"usd": r["usd"] or 0.0, "tokens": r["tokens"] or 0, "matches": r["matches"] or 0,
                          "advice": advice.get((r["kind"], r["model"]), "")} for r in rows]
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

    def snapshot(self, since: dict | None = None) -> list[dict]:
        since = since or {}
        return [s.snapshot(int(since.get(s.id, 0))) for s in self.sessions.values()]

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
        """Compiles + claims + notes by this agent: a session that adds none of these made no progress."""
        row = self.net.store.one(
            "SELECT (SELECT COUNT(*) FROM attempts WHERE agent_id=?) + (SELECT COUNT(*) FROM events WHERE agent_id=?) AS n",
            agent_id, agent_id)
        return row["n"]

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
        agent, token = self.net.local_agent(s.agent_name)
        self.assignments[agent.id] = s.slug
        workdir = self.home / "agents" / s.agent_name
        workdir.mkdir(parents=True, exist_ok=True)
        s.push("info", f"{s.label} starting on {s.slug}")
        if (prep := self._prep.get(s.slug)) is not None:
            self._set(s, "starting", "first giving the game's code without a source file one (about a minute)")
            prep.wait(PREP_WAIT)
        twins = getattr(getattr(self.net, "twins", None), "jobs", {}).get(s.slug)
        if twins is not None and not twins.ordered.is_set():
            self._set(s, "starting", "first setting hand-written assembly aside and putting the functions in order "
                                     "(seconds; a minute the first time)")
            twins.ordered.wait(ORDER_WAIT)
        failures = fast = throttles = limits = auths = 0
        while not s.stop.is_set():
            if self._left(s.slug, task := s.options.get("task") or "") == 0:
                self._finish(s, f"nothing left to {'polish' if task == 'readable' else 'decompile'} here 🎉")
                break
            if (cap := self.over_budget(s.slug)) is not None:
                self._finish(s, f"the agents on this project have spent the ${cap:.2f} budget (Setup)")
                break
            if self._room_away(s):
                continue
            if (broken := self._build_problem(s.slug)) is not None:
                self._pause(s, f"the project doesn't build on this computer, so no session starts: {broken}",
                            "Fix the project's setup (its page says what's wrong), then press Start again.")
                break
            s.runs += 1
            self._set(s, "working")
            began = time.time()
            before = self._progress(agent.id)
            row = self._session_begin(s)
            kind = agents.get(s.kind)
            try:
                ok = self._run_cli(s, token, workdir) if kind.command else kind.run(self, s, agent)
            except Fatal as e:  # needs the user first: retrying can't help
                self._session_end(s, row, agent, began, "paused")
                s.error = str(e)
                self._pause(s, str(e))
                break
            except Exception as e:  # keep the runner alive; show what happened
                log.exception("helper %s failed", s.agent_name)
                s.error = f"{type(e).__name__}: {e}"
                s.push("error", s.error)
                ok = False
            if ok and not s.stop.is_set() and self._progress(agent.id) == before:
                s.error = "the session ended without compiling anything (its messages are in its details)"
                s.push("error", s.error)
                ok = False
            self._session_end(s, row, agent, began, "stopped" if s.stop.is_set() else "ok" if ok else
                              "limit" if s.limited else "signin" if s.auth_failed else "failed")
            if s.stop.is_set():
                break
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
                s.limited, s.limit_until = False, None
                s.restarts += 1
                self._wait(s, f"{kind.name}'s usage limit is reached", until + LIMIT_RESET_MARGIN,
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
                self._set(s, "waiting", "the next session starts in a moment")
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
        s.usage = _fresh_usage()
        with self.net.store.tx() as db:
            return db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at) "
                              "VALUES(?,?,?,?,?,?)", (s.id, s.slug, s.kind, s.model or agents.get(s.kind).config_model() or "",
                                                      s.agent_name, time.time())).lastrowid

    def _session_end(self, s: Session, row: int, agent, began: float, outcome: str) -> None:
        """What a session did and spent, on its row: to compare models on cost per match."""
        compiles = self.net.store.one("SELECT COUNT(*) AS n FROM attempts WHERE agent_id=? AND created_at>=?",
                                      agent.id, began)["n"]
        matches = self.net.store.one("SELECT COUNT(*) AS n FROM events WHERE agent_id=? AND kind='match' AND "
                                     "created_at>=?", agent.id, began)["n"]
        u = s.usage
        with self.net.store.tx() as db:
            db.execute("UPDATE helper_sessions SET ended_at=?, outcome=?, usd=?, input_tokens=?, output_tokens=?, "
                       "cache_read_tokens=?, cache_write_tokens=?, tokens=?, turns=?, tool_calls=?, compiles=?, "
                       "matches=? WHERE id=?", (time.time(), outcome, u["usd"], u["input"], u["output"],
                                                u["cache_read"], u["cache_write"], u["tokens"], u["turns"],
                                                u["tool_calls"], compiles, matches, row))
        spent = f", ${u['usd']:.2f}" if u["usd"] else f", {u['tokens'] // 1000}k tokens" if u["tokens"] else ""
        s.push("info", f"session {s.runs} done: {u['tool_calls']} tool calls, {compiles} compiles, {matches} "
                       f"matched{spent}")
        self._save(s)

    def prompt(self, s: Session) -> str:
        text = self._prompt(s)
        # the know-how every session reads first, given up front (saves each session its first tool calls)
        ov = self.net.overview(s.slug)  # (only its platform's parts: tools.for_platform)
        text += "\nKnow-how you'd otherwise read with guide() (more topics: guide()):\n\n" + tools.up_front(
            ov.get("platform") or "", ov.get("kind") or "", ov["compiler"]["toolchain"])
        if (known := self.net.knowledge.for_prompt(s.slug)):
            text += f"\n{known}\n"
        if (wish := self.net.instructions(s.slug)):
            text += f"\nThe user's instructions for this project (they come first):\n{wish}\n"
        return text

    def _prompt(self, s: Session) -> str:
        ov = self.net.overview(s.slug)
        count = s.options.get("per_session", PER_SESSION)
        if s.options.get("task") == "readable":  # (in either mode: the step that differs is the mode's)
            return tools.READABLE_PROMPT.format(title=ov["title"], count=count, names=tools.READABLE_FORK
                                                if ov.get("kind") == "fork" else tools.READABLE_ROM)
        if ov.get("kind") == "fork":
            guides = ["fork", "objdiff", *consoles.get(ov.get("platform")).guides]
            platform = ov.get("platform_name") or "its platform"
            what = tools.FORK_SCRATCH if ov.get("origin") == "scratch" else tools.FORK_PUBLIC
            return tools.FORK_PROMPT.format(title=ov["title"], what=what.format(platform=platform),
                                            count=count, budget=network.stall_budget_text(),
                                            guides=", ".join(f'guide("{g}")' for g in guides))
        guide = consoles.get(ov.get("platform")).up_front_guide(ov["compiler"]["toolchain"])
        return tools.WORK_PROMPT.format(title=ov["title"], count=count, budget=network.stall_budget_text(),
                                        compiler_guide=guide)

    def _command(self, s: Session, token: str, workdir: Path) -> tuple[list[str], dict, str]:
        """The agent's command line, environment and what goes in on stdin (the prompt). Each helper connects only to
        this app's own MCP server, with its own token."""
        k = agents.get(s.kind)
        exe = _which(k.command)
        if not exe:
            raise RuntimeError(f"{k.name} is not installed")
        cmd, more = k.session_command(exe, workdir, self.mcp_url, token, s.model)
        cmd = agents.cli_argv(cmd)
        # PWD too: opencode 2 works in $PWD, not the folder it starts in, and the app's PWD was the user's home, so
        # Mario Kart 64's helpers worked there without their folder's settings: no tools, and a free shell
        env = dict(os.environ, PWD=str(workdir), **{HELPER_ENV: str(self.home)}, **more)
        return cmd, env, k.stdin(self.prompt(s))

    def _run_cli(self, s: Session, token: str, workdir: Path) -> bool:
        k = agents.get(s.kind)
        cmd, env, prompt = self._command(s, token, workdir)
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
                proc.stdin.write(("\x01" if os.name == "nt" else "") + prompt)
                proc.stdin.close()  # end of input: the agent works on this one prompt, then exits
            except OSError:  # it exited before reading (stderr says why)
                pass

        def drain_err():
            for line in proc.stderr:
                if (text := k.clean_stderr(line)) and (not errors or errors[-1] != text):
                    errors.append(text)
        threading.Thread(target=feed_prompt, daemon=True).start()
        threading.Thread(target=drain_err, daemon=True).start()
        saw_tool = False
        said: collections.deque = collections.deque(maxlen=20)
        state: dict = {}
        last_error = refused = ""
        for line in proc.stdout:
            for kind, text in k.parse_line(line, state):
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
            if s.stop.is_set():
                _terminate(proc)
                break
        code = proc.wait()
        _close_windows_job(proc.pid)
        s.proc = None
        if s.stop.is_set():
            return True
        everything = " ".join([*said, *errors])
        if refused:
            raise Fatal(refused)  # every retry would meet it the same way
        if LIMIT.search(everything):
            s.limited = True  # whatever the exit code: Claude Code says so in an ordinary result
            s.limit_until = limit_reset(everything)
        elif code != 0 and (why := no_credit(everything)):
            raise Fatal(why)  # every agent on this account fails the same way until it's topped up
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
        if code != 0:
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
