"""Helper agents: the AI agents decompdecomp starts on a project. Only these work on projects; nobody connects from
outside.

CLI agents (Claude Code, Codex, Antigravity CLI, opencode) run headless in a private working folder, each connected
to this app's MCP endpoint on 127.0.0.1 with a token of its own and allowed to use only its tools: MCP is the app's
private wiring to them. Models reached with an API key (DeepSeek, OpenRouter, a local Ollama, any OpenAI-compatible
endpoint) run in a small built-in tool loop instead. Everything the agents print is parsed into a readable feed.

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
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .. import network
from ..network import NetworkError
from . import tools
from .engine import RomNetwork

log = logging.getLogger("decompdecomp")


class Fatal(RuntimeError):
    """A helper can't work until the user changes something: it pauses with this, instead of trying again."""


@dataclass
class Kind:
    id: str
    name: str
    command: str  # executable, "" for the built-in agent
    vendor: str
    install: str  # the command that installs it
    signin: str
    docs: str


KINDS = {
    "claude": Kind("claude", "Claude Code", "claude", "Anthropic", "curl -fsSL https://claude.ai/install.sh | bash",
                   "Open a terminal, run `claude` and sign in with /login.", "https://docs.claude.com/en/docs/claude-code"),
    "codex": Kind("codex", "Codex CLI", "codex", "OpenAI", "npm install -g @openai/codex",
                  "Run `codex login` once in a terminal.", "https://github.com/openai/codex"),
    "antigravity": Kind("antigravity", "Antigravity CLI", "agy", "Google",
                        "curl -fsSL https://antigravity.google/cli/install.sh | bash",
                        "Open a terminal and run `agy` once to sign in.", "https://antigravity.google/docs/cli"),
    "opencode": Kind("opencode", "opencode", "opencode", "any provider (DeepSeek, Qwen, Kimi, ...)",
                     "curl -fsSL https://opencode.ai/install | bash",
                     "Run `opencode auth login` once to add a provider (DeepSeek, OpenRouter, ...).", "https://opencode.ai"),
    "api": Kind("api", "API key", "", "DeepSeek, OpenRouter, Ollama, any OpenAI-compatible API", "", "",
                "https://api-docs.deepseek.com"),
}

API_PRESETS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": True},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat", "key": True},
    "ollama": {"name": "Ollama (local)", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3-coder", "key": False},
    "lmstudio": {"name": "LM Studio (local)", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "key": False},
    "llamacpp": {"name": "llama.cpp (local)", "base_url": "http://127.0.0.1:8080/v1", "model": "local", "key": False},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-5", "key": True},
    "custom": {"name": "Custom", "base_url": "", "model": "", "key": True},
}
# Google retired Gemini CLI on 2026-06-18; Antigravity CLI (`agy`) replaced it. Helpers and launcher choices saved
# by older versions as "gemini" carry on as Antigravity.
ALIASES = {"gemini": "antigravity", "agy": "antigravity"}
# An agent's error that's about signing in or keys: then the pause says how to sign in
_SIGNIN = re.compile(r"auth|log ?in|sign(ed)? ?in|credential|api.?key|unauthori[sz]ed|\b401\b|\b403\b", re.I)

# decompdecomp's tools are loaded up front in Antigravity (tools.ALWAYS_LOAD) and need no permission. Should a helper
# reach one through agy's generic MCP dispatcher instead, a headless run can't ask, and agy quietly refuses it:
# this rule in the user's agy settings allows decompdecomp's tools (and nothing else).
AGY_ALLOW = "mcp(decomp/*)"
# how long one Antigravity session may run: a session works through several functions (agy's default is 5 minutes)
AGY_PRINT_TIMEOUT = "4h"

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

# finding which agents are installed: DECOMP_DETECT_AGENTS=off never runs one (the tests set it: `agy models` starts
# the MCP servers in Antigravity's settings)
DETECT_ENV = "DECOMP_DETECT_AGENTS"
VERSION_CACHE = 5 * 60
MODELS_CACHE = 10 * 60
CLI_TIMEOUT = 15
MODELS_TIMEOUT = 30
MODELS_LISTED = 200

# The built-in API agent: tool calls per session, the share of the model's context the conversation may fill (the rest
# is the reply's), how often a request the server finds too long is trimmed harder (by API_TRIM_FACTOR) and sent again.
API_STEPS = 120
API_CONTEXT_SHARE = 0.7
API_TRIM_RETRIES = 4
API_TRIM_FACTOR = 0.6
API_TIMEOUT, API_CONNECT_TIMEOUT = 300, 15
MIN_CONTEXT = 2048
LOCAL_CONTEXT = 16384  # what a local server runs when it doesn't say (they often run small contexts)
REMOTE_CONTEXT = 128000
CHARS_PER_TOKEN = 3  # code and assembly run about 3 characters a token
TRIM_ABOVE, TRIM_TO = 300, 240  # characters: an old message longer than this is cut to this

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


def kind_id(kind: str) -> str:
    return ALIASES.get(kind, kind)


def agy_settings() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def endpoint_entry(e: dict) -> dict:
    """A saved API endpoint, with what's left out filled in from its provider (a name: the provider's, or for a
    custom server the address's host, so sessions say which machine they run on)."""
    provider = e.get("provider") if e.get("provider") in API_PRESETS else "custom"
    preset = API_PRESETS[provider]
    base = (e.get("base_url") or preset["base_url"]).strip()
    host = re.sub(r"^\w+://", "", base).split("/", 1)[0]
    moved = provider == "custom" or base.rstrip("/") != preset["base_url"].rstrip("/")  # Ollama on another box...
    out = {"id": str(e.get("id") or ""), "provider": provider, "base_url": base,
           "model": (e.get("model") or preset["model"]).strip(),
           "name": (e.get("name") or "").strip() or (host if moved and host else preset["name"]) or "API"}
    for k in ("api_key", "context_tokens", "max_sessions"):
        if e.get(k):
            out[k] = e[k]
    return out


def is_local(base: str, resolve: bool = True) -> bool:
    """An address on this computer or the local network (a llama.cpp box, Ollama on another machine): a private,
    loopback, link-local or Tailscale address, a name without dots or under .local/.lan/.internal/.home.arpa, or
    (`resolve`) a name that resolves to such addresses only."""
    import ipaddress
    import socket

    host = re.sub(r"^\w+://", "", base.strip()).split("/", 1)[0].rsplit("@", 1)[-1]
    host = host[1:].split("]", 1)[0] if host.startswith("[") else host.split(":", 1)[0] if host.count(":") == 1 else host
    if not host:
        return False

    def private(ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        tailscale = a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")
        return a.is_private or a.is_loopback or a.is_link_local or tailscale or a.is_unspecified

    if private(host):
        return True
    if "." not in host or re.search(r"\.(local|lan|home|internal|localdomain|home\.arpa)$", host, re.I) \
            or host.lower() == "localhost":
        return True
    if not resolve or re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", host):
        return False
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    return bool(addrs) and all(private(a) for a in addrs)


# what an agent CLI says when nobody signed in to it (Claude Code: "Not logged in · Please run /login")
_SIGNED_OUT = re.compile(r"not logged in|run /login|invalid api key|not authenticated|please (?:log|sign) ?in|"
                         r"auth(?:entication)? method", re.I)
# what a model's provider says when the account has no credit left (DeepSeek: "Insufficient Balance", OpenAI:
# "insufficient_quota", Anthropic: "credit balance is too low", OpenCode: "quota exceeded"): trying again can't help
# until it's topped up
_NO_CREDIT = re.compile(r"insufficient[ _](?:balance|credits?|funds|quota)|exceeded your current quota|"
                        r"credit balance is too low|payment required|out of credits?", re.I)
# and when it's asked too much at once (HTTP 429): it works again in a while
_THROTTLED = re.compile(r"too many requests|rate[ _-]?limit|concurrency limit|\b429\b|overloaded", re.I)
# and when a subscription's allowance is used up for now (Claude Code: "usage limit reached", Codex: "You've hit your
# usage limit", Antigravity: RESOURCE_EXHAUSTED, OpenCode Go: "quota exceeded"): it resets by itself in hours, so a
# helper waits for it instead of stopping (a night's run lost most of its helpers that way)
_LIMIT = re.compile(r"usage limit|(?<!rate )(?<!rate-)(?<!rate_)limit (?:reached|exceeded)|hit your (?:usage )?limit|quota[ _](?:exceeded|exhausted)|"
                    r"resource[ _]exhausted|out of (?:usage|messages)", re.I)
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
        if zone:
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


_versions: dict[str, tuple[float, str | None]] = {}
_models: dict[str, tuple[float, list[dict]]] = {}

# Claude Code's aliases always point at Anthropic's latest model of each kind.
CLAUDE_MODELS = [("fable", "Fable", "Anthropic's most capable model"), ("opus", "Opus", "very capable"),
                 ("sonnet", "Sonnet", "fast and capable, cheaper"), ("haiku", "Haiku", "fastest, cheapest")]


def _codex_config() -> dict:
    """The user's Codex settings (~/.codex/config.toml, or $CODEX_HOME's)."""
    import tomllib

    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        return tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def helper_name(kind: str, model: str | None, taken: set[str]) -> str:
    """The agent identity of a helper: its kind and its model, numbered (opencode-deepseek-v4.1-flash-1). Matches,
    compiles and statistics hang off this name, so a model never inherits another's: helpers used to be opencode-1,
    opencode-2... whatever model they ran, and a re-added helper with another model took over the old one's record.
    The same model again gets the same name back and carries on its record."""
    m = re.sub(r"[^a-z0-9._-]+", "-", (model or "").lower().rsplit("/", 1)[-1]).strip("-._")[:24]
    base = f"{kind}-{m}" if m else kind
    k = 1
    while f"{base}-{k}" in taken:
        k += 1
    return f"{base}-{k}"


def _config_model(kind: str) -> str | None:
    """The model the user set as default in the agent's own settings, if any."""
    home = Path.home()
    try:
        if kind == "claude":
            return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")).get("model") or None
        if kind == "codex":
            return _codex_config().get("model") or None
        if kind == "antigravity":
            return json.loads(agy_settings().read_text(encoding="utf-8")).get("model") or None
        if kind == "opencode":
            base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
            return json.loads((base / "opencode" / "opencode.json").read_text(encoding="utf-8")).get("model") or None
    except (OSError, ValueError, AttributeError):
        return None
    return None


def models(kind: str, path: str | None, run: bool = True) -> list[dict]:
    """Models to offer for this agent: [{"id", "name", "note"}]. Claude Code's aliases; opencode's and Antigravity's
    own lists (what the user's sign-in offers; `run` False: only what was found before); for Codex the user's default
    plus any model name they type."""
    if kind == "claude":
        return [{"id": i, "name": n, "note": note} for i, n, note in CLAUDE_MODELS]
    if kind not in ("opencode", "antigravity") or not path:
        return []
    cached = _models.get(kind)
    if cached and (not run or time.time() - cached[0] < MODELS_CACHE):
        return cached[1]
    if not run:
        return []
    try:
        res = subprocess.run([path, "models"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, timeout=MODELS_TIMEOUT)
        lines = [line.strip() for line in res.stdout.splitlines()]
    except (OSError, subprocess.TimeoutExpired):
        lines = []
    if kind == "opencode":
        rows = [r for r in lines if re.fullmatch(r"[\w.-]+/[\w.:/-]+", r)]
        out = [{"id": r, "name": r.split("/", 1)[1], "note": r.split("/", 1)[0]} for r in rows[:MODELS_LISTED]]
    else:  # agy: "<slug>\t<name>" per model
        rows = [r.split("\t", 1) for r in lines if re.fullmatch(r"[\w.:-]+\t.+", r)]
        out = [{"id": slug, "name": name.strip(), "note": slug.split("-", 1)[0]} for slug, name in rows[:MODELS_LISTED]]
    _models[kind] = (time.time(), out)
    return out


def _which(cmd: str) -> str | None:
    found = shutil.which(cmd)
    if found:
        return found
    home = Path.home()
    for d in (home / ".local" / "bin", home / ".claude" / "local", home / ".opencode" / "bin", home / ".npm-global" / "bin"):
        if (d / cmd).exists():
            return str(d / cmd)
    return None


def _version(kind: str, path: str) -> str | None:
    """An agent's `--version`, asked at most every VERSION_CACHE seconds."""
    cached = _versions.get(kind)
    if cached is None or time.time() - cached[0] > VERSION_CACHE:
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", stdin=subprocess.DEVNULL, timeout=CLI_TIMEOUT)
            said = (res.stdout or res.stderr).strip()
            version = said.splitlines()[0][:60] if said else None
        except (OSError, subprocess.TimeoutExpired):
            version = None
        _versions[kind] = cached = (time.time(), version)
    return cached[1]


def _opencode_2(exe: str) -> bool:
    """opencode 2 runs every `opencode run` in one background service, which connects the user's own MCP servers once,
    without a helper's session token: both GTA San Andreas helpers worked as the user's one chat agent, on one claim,
    whatever their folders said. --standalone gives each helper a server of its own, started from its own process
    (before 2.0 there's no such service, and the flag would stop it)."""
    text = (_versions.get("opencode") or (0, None))[1] or _version("opencode", exe)
    m = re.search(r"(\d+)\.\d+", text or "")
    return bool(m) and int(m.group(1)) >= 2


def detect(quick: bool = False) -> list[dict]:
    """Which agents are installed, their versions and models. `quick` (or DECOMP_DETECT_AGENTS=off) runs none of
    them, only looks where they are: running them takes seconds (`agy models` alone about three, and it starts the MCP
    servers in Antigravity's settings), so the app does that in the background (Manager.detected)."""
    quick = quick or os.environ.get(DETECT_ENV) == "off"
    out = []
    for k in KINDS.values():
        row = {"id": k.id, "name": k.name, "vendor": k.vendor, "signin": k.signin, "docs": k.docs, "install": k.install}
        if not k.command:
            row.update(installed=True, version=None, path=None)
        else:
            path = _which(k.command)
            row.update(installed=path is not None, path=path, version=None)
            if path:
                row.update(version=(_versions.get(k.id) or (0, None))[1] if quick else _version(k.id, path),
                           models=models(k.id, path, run=not quick), default_model=_config_model(k.id))
        if row["install"].startswith("npm ") and not row["installed"] and not shutil.which("npm"):
            row["needs"] = "Node.js (https://nodejs.org) for npm"
        out.append(row)
    return out


# ---------------------------------------------------------------------------------- output parsing

def _tool_name(name: str) -> str:
    for prefix in ("mcp__decomp__", "mcp_decomp_", "decomp_agent.", "decomp_agent__", "decomp__", "decomp_", "decomp."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _args_brief(args) -> str:
    if not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        if k == "source":
            parts.append(f"{len(str(v).splitlines())} lines of C")
        elif isinstance(v, str):
            parts.append(v if len(v) < 40 else v[:37] + "…")
        elif v not in (None, "", {}):
            parts.append(str(v))
    return ", ".join(parts)


def _error_text(err) -> str:
    """The message in an agent's error object, whatever its shape (opencode nests it under data)."""
    if isinstance(err, dict):
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        msg = err.get("message") or data.get("message") or ""
        name = err.get("name") or err.get("type") or ""
        if msg and name and name not in ("UnknownError", "Error") and name not in msg:
            return f"{name}: {msg}"
        return str(msg or name or json.dumps(err)[:300])
    return str(err or "")


def _usage(**counts) -> tuple[str, str] | None:
    """A `usage` feed item: what a session spent (usd), its tokens (TOKEN_KINDS, or `tokens` when the agent only
    reports a total) and turns. None when it says nothing."""
    said = {k: v for k, v in counts.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v}
    return ("usage", json.dumps(said)) if said else None


def _num(d: dict, key: str) -> int:
    v = d.get(key)
    return v if isinstance(v, int) else 0


def _parse_agy(obj: dict, state: dict) -> list[tuple[str, str]]:
    """Antigravity CLI's stream-json: step_update events (text arrives in deltas, gathered per step) and a result.
    Its result counts one turn for the whole session (the one prompt), so turns are the model's steps, counted here:
    every session read "1 turns"."""
    out: list[tuple[str, str]] = []
    ev = obj.get("event")
    if ev == "step_update" and isinstance(obj.get("step_update"), dict):
        st = obj["step_update"]
        step, kind = st.get("step_index"), st.get("step_type")
        if kind in ("agent_response", "thinking", "planner_response"):
            texts = state.setdefault("agy_text", {})
            texts[step] = texts.get(step, "") + str(st.get("text_delta") or "")
            if st.get("state") == "DONE" and texts.get(step, "").strip():
                out.append(("think" if kind == "thinking" else "text", texts.pop(step).strip()))
                state["agy_steps"] = state.get("agy_steps", 0) + (kind != "thinking")
        elif kind == "tool" and st.get("state") == "ACTIVE" and step not in state.setdefault("agy_tools", set()):
            state["agy_tools"].add(step)
            state["agy_steps"] = state.get("agy_steps", 0) + 1
            info = st.get("tool_info") if isinstance(st.get("tool_info"), dict) else {}
            name, params = info.get("name") or st.get("tool_name") or "", info.get("parameters")
            if name == "call_mcp_tool" and isinstance(params, dict):  # agy reaches MCP tools through one dispatcher
                name, params = params.get("ToolName") or name, params.get("Arguments")
            out.append(("tool", f"{_tool_name(name)}({_args_brief(params)})"))
    elif ev == "result" and isinstance(obj.get("result"), dict):
        res = obj["result"]
        usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
        turns = max(_num(res, "num_turns"), state.get("agy_steps", 0))
        split = _num(usage, "input_tokens") or _num(usage, "output_tokens")
        item = _usage(input=_num(usage, "input_tokens"), output=_num(usage, "output_tokens"),
                      tokens=0 if split else _num(usage, "total_tokens"), turns=turns)
        out += [item] if item else []
        denied = [d for d in res.get("denied_actions") or [] if isinstance(d, dict)]
        if any(d.get("action") == "mcp" for d in denied):
            out.append(("error", f"Antigravity refused decompdecomp's tools: a background run can't ask you for permission. "
                                 f"Allow them once: add \"{AGY_ALLOW}\" to \"permissions\": {{\"allow\": [...]}} in "
                                 f"{agy_settings()} (only decompdecomp's tools; shell, files and the web keep asking)."))
        if res.get("status") not in (None, "SUCCESS"):
            out.append(("error", str(res.get("error") or f"session ended: {res.get('status')}")[:400]))
        else:
            out.append(("info", "session finished" + (f" ({turns} turns)" if turns else "")))
    elif ev == "error":
        out.append(("error", _error_text(obj.get("error") or obj.get("message"))[:400]))
    return out


def clean_stderr(kind: str, line: str) -> str:
    """One line of an agent's stderr as worth showing, or "" for noise."""
    line = line.strip()
    if kind == "opencode" and "level=" in line:  # opencode --print-logs: keep the error itself, drop stack noise
        m = re.search(r'\berror="((?:[^"\\]|\\.)*)"', line)
        return m.group(1).replace('\\"', '"') if m and re.search(r"level=error\b", line, re.I) else ""
    if line.startswith("AGY_ERROR:"):  # agy: a structured error line
        try:
            return _error_text(json.loads(line.split(":", 1)[1]))[:300]
        except ValueError:
            return line
    return line


def parse_line(kind: str, line: str, state: dict | None = None) -> list[tuple[str, str]]:
    """(type, text) items for the feed from one line of agent output: text, think, tool, info, error, usage (a JSON
    object: usd, input, output, cache_read, cache_write, tokens, turns). `state` carries what spans lines (Antigravity
    streams text in pieces) for one session."""
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except ValueError:
        return [("info", line[:400])]
    if not isinstance(obj, dict):
        return []
    if "event" in obj and "type" not in obj:  # Antigravity CLI
        return [(k, v) for k, v in _parse_agy(obj, {} if state is None else state) if v]
    t = obj.get("type")
    out: list[tuple[str, str] | None] = []
    if t == "assistant" and isinstance(obj.get("message"), dict):  # Claude Code
        for c in obj["message"].get("content", []):
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append(("text", c["text"].strip()))
            elif c.get("type") == "thinking" and c.get("thinking", "").strip():
                out.append(("think", c["thinking"].strip()))
            elif c.get("type") == "tool_use" and c.get("name") != "ToolSearch":  # ToolSearch: Claude Code loading tools
                out.append(("tool", f"{_tool_name(c.get('name', ''))}({_args_brief(c.get('input'))})"))
    elif t == "result":  # Claude Code end of session
        if obj.get("is_error") or obj.get("status") == "error":
            out.append(("error", str(obj.get("result") or obj.get("error") or "session failed")[:400]))
        else:
            cost = obj.get("total_cost_usd")
            turns = obj.get("num_turns")
            bits = [f"{turns} turns" if turns else "", f"${cost:.2f}" if isinstance(cost, (int, float)) else ""]
            out.append(("info", "session finished" + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "")))
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            stats = obj.get("stats") if isinstance(obj.get("stats"), dict) else {}
            out.append(_usage(usd=cost, input=_num(usage, "input_tokens"), output=_num(usage, "output_tokens"),
                              cache_read=_num(usage, "cache_read_input_tokens"),
                              cache_write=_num(usage, "cache_creation_input_tokens"),
                              tokens=0 if usage else _num(stats, "total_tokens"), turns=turns))
    elif t in ("item.started", "item.completed") and isinstance(obj.get("item"), dict):  # Codex
        item = obj["item"]
        it = item.get("type") or item.get("item_type")
        if it == "agent_message" and t == "item.completed":
            out.append(("text", str(item.get("text", "")).strip()))
        elif it == "reasoning" and t == "item.completed" and item.get("text"):
            out.append(("think", str(item["text"]).strip()))
        elif it == "mcp_tool_call" and t == "item.started":
            out.append(("tool", f"{_tool_name(item.get('tool', ''))}({_args_brief(item.get('arguments'))})"))
        elif it == "command_execution" and t == "item.started":
            out.append(("tool", f"shell: {str(item.get('command', ''))[:80]}"))
        elif it == "error":
            out.append(("error", str(item.get("message", ""))[:400]))
    elif t == "turn.completed":  # Codex: its input tokens include the cached ones
        out.append(("info", "turn finished"))
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
        cached = _num(usage, "cached_input_tokens")
        out.append(_usage(input=max(0, _num(usage, "input_tokens") - cached), cache_read=cached,
                          output=_num(usage, "output_tokens"), turns=1))
    elif t in ("error", "turn.failed"):  # Codex; opencode: {"error": {"name", "data": {"message"}}}
        text = _error_text(obj.get("error") or obj.get("message"))
        if not text.startswith("Unexpected server error"):  # opencode's placeholder: its log line says what failed
            out.append(("error", text[:400]))
    elif t == "tool_use":  # opencode
        part = obj.get("part") if isinstance(obj.get("part"), dict) else obj
        name = part.get("tool_name") or part.get("tool") or part.get("name") or ""
        args = part.get("parameters") or (part.get("state") or {}).get("input") or part.get("input")
        out.append(("tool", f"{_tool_name(name)}({_args_brief(args)})"))
    elif t == "text" and isinstance(obj.get("part"), dict):  # opencode
        text = obj["part"].get("text", "").strip()
        if text:
            out.append(("text", text))
    elif t in ("step_finish", "step-finish") and isinstance(obj.get("part"), dict):  # opencode: one step of the model
        part = obj["part"]
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        out.append(_usage(usd=part.get("cost"), input=_num(tokens, "input"),
                          output=_num(tokens, "output") + _num(tokens, "reasoning"), cache_read=_num(cache, "read"),
                          cache_write=_num(cache, "write"), turns=1))
    return [item for item in out if item and item[1]]


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
        return {"id": self.id, "kind": self.kind, "kind_name": KINDS[self.kind].name, "label": self.label,
                "slug": self.slug, "agent": self.agent_name, "model": self.model, "state": self.state, "why": self.why,
                "todo": self.todo, "until": self.until, "stopped_by": self.stopped_by,
                "last_message": self.last_message, "runs": self.runs, "restarts": self.restarts, "error": self.error,
                "started_at": self.started_at, "usd": round(self.usd, 4), "tokens": self.tokens,
                "feed": [f for f in self.feed if f["seq"] > since]}


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
    threading.Thread(target=_end_groups, args=({proc.pid},), daemon=True, name=f"end-{proc.pid}").start()


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
            if s.kind not in KINDS:
                continue
            if s.state in LIVE:
                if r["pid"] and _proc_start(r["pid"]) == r["pid_start"]:  # still running: the app crashed
                    crashed.add(r["pid"])
                s.state, s.stopped_by, s.until = "stopped", BY_QUIT, None
                s.why = "decompdecomp closed while it worked"
                self._save(s)
            self.sessions[s.id] = s
        _end_groups(crashed | self._strays())
        with self.net.store.tx() as db:  # helpers, and the runners from before records (named like helpers)
            db.execute("DELETE FROM claims WHERE agent_id IN (SELECT id FROM agents WHERE origin='local' "
                       "AND name NOT IN ('you', 'autopilot', 'permuter'))")

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
        """What the GUI calls a helper: its name; an API helper by its endpoint, numbered ("gpu-box #2" says which
        machine it runs on)."""
        if kind != "api":
            return name
        machine = options.get("provider_name") or "API"
        used = {s.label for s in self.sessions.values() if s.kind == "api" and s.state in LIVE}
        n = 1
        while f"{machine} #{n}" in used:
            n += 1
        return f"{machine} #{n}"

    # -- control

    def start(self, kind: str, slug: str, count: int = 1, options: dict | None = None) -> list[str]:
        options = dict(options or {})
        if kind_id(kind) != kind:
            kind = kind_id(kind)
            options.pop("model", None)  # a Gemini CLI model name may mean nothing to Antigravity: use its default
        if kind not in KINDS:
            raise ValueError(f"unknown agent kind {kind!r}")
        if self._down:
            raise ValueError("decompdecomp is quitting")
        ids = []
        self._prepare(slug)
        with self._lock:
            for _ in range(max(1, min(int(count), MAX_AT_ONCE))):
                name = helper_name(kind, options.get("model") or _config_model(kind), self._taken())
                s = self._record(slug, kind, name, self._label(kind, name, options), options)
                s.restarts = 0
                self._launch(s)
                ids.append(s.id)
        return ids

    def _launch(self, s: Session) -> None:
        s.stop = threading.Event()
        s.state, s.why, s.todo, s.until, s.stopped_by, s.error = "starting", "", "", None, "", ""
        s.signed_out = s.throttled = s.limited = False
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
            if s.proc is not None and s.proc.poll() is None:
                _terminate(s.proc)
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
        groups = {s.proc.pid for s in self.sessions.values() if s.proc is not None and s.proc.poll() is None}
        self.stop()
        _end_groups(groups | self._strays())
        try:
            self.on_stop()
        except Exception:  # the app quits anyway
            log.exception("closing rooms while quitting failed")

    def _strays(self) -> set[int]:
        """Process groups of processes this app's helpers started, found by HELPER_ENV in their environment."""
        mark = f"{HELPER_ENV}={self.home}".encode()
        out = set()
        for d in Path("/proc").iterdir():
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

    def _left(self, slug: str) -> int:
        proj = self.net.project_row(slug)
        row = self.net.store.one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='open'", proj["id"])
        return row["n"]

    def _account(self, s: Session, usage: dict) -> None:
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

    def _finish(self, s: Session, why: str) -> None:
        """It stopped by itself: nothing to do here any more."""
        s.stopped_by = BY_ITSELF
        self._set(s, "stopped", why)
        s.push("info", f"stopping: {why}")

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
        failures = fast = throttles = limits = 0
        while not s.stop.is_set():
            if self._left(s.slug) == 0:
                self._finish(s, "nothing left to decompile here 🎉")
                break
            if (cap := self.over_budget(s.slug)) is not None:
                self._finish(s, f"the agents on this project have spent the ${cap:.2f} budget (Setup)")
                break
            if self._room_away(s):
                continue
            s.runs += 1
            self._set(s, "working")
            began = time.time()
            before = self._progress(agent.id)
            row = self._session_begin(s)
            try:
                ok = self._run_api(s, agent) if s.kind == "api" else self._run_cli(s, token, workdir)
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
                              "limit" if s.limited else "failed")
            if s.stop.is_set():
                break
            if s.signed_out:
                self._pause(s, s.error)
                break
            if s.limited:  # the plan's allowance is used up for now: it resets, so wait as long as it takes
                limits += 1
                until = s.limit_until or time.time() + min(LIMIT_WAIT_LONGEST, LIMIT_WAIT_FIRST * 2 ** (limits - 1))
                s.limited, s.limit_until = False, None
                s.restarts += 1
                self._wait(s, f"{KINDS[s.kind].name}'s usage limit is reached", until + LIMIT_RESET_MARGIN,
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
                hint = KINDS[s.kind].signin if _SIGNIN.search(s.error) else ""  # not after every failure: it misleads
                self._pause(s, f"it failed {fast} times in a row, each soon after it started: {s.error}", hint)
                break
            s.restarts += 1
            self._wait(s, f"starting again after an error: {s.error}",
                       time.time() + min(BACKOFF_LONGEST, BACKOFF_FIRST * 2 ** (failures - 1)))
        s.proc = None
        self._save(s)
        self._release(s, agent)

    def _release(self, s: Session, agent=None) -> None:
        """Free the function a helper holds, so others can take it (in a room, the host holds the real claim)."""
        row = self.net.store.one("SELECT id FROM agents WHERE name=?", s.agent_name) if agent is None else None
        agent_id = agent.id if agent is not None else row["id"] if row else None
        if agent_id is None:
            return
        held = self.net.store.one("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                                  agent_id)
        link = self.net.links.get(s.slug)
        if held and link is not None and agent is not None:
            try:
                link.call("/collab/release", agent, function=held["name"], notes="")
            except Exception:  # the host lets the claim expire
                pass
        with self.net.store.tx() as db:
            db.execute("DELETE FROM claims WHERE agent_id=?", (agent_id,))

    def _session_begin(self, s: Session) -> int:
        s.usage = _fresh_usage()
        with self.net.store.tx() as db:
            return db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at) "
                              "VALUES(?,?,?,?,?,?)", (s.id, s.slug, s.kind, s.model or _config_model(s.kind) or "",
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
        ov = self.net.overview(s.slug)
        if ov.get("kind") == "fork":
            # CodeWarrior's register and stack rules decide most of what's left on GameCube, Wii and DS
            topics = ["fork", "objdiff", *(["mwcc"] if ov.get("platform") in ("gc", "wii", "nds") else
                                           ["x86"] if ov.get("platform") == "win32" else [])]
        else:
            topics = ["workflow", {"psx": "psx", "ps2": "ps2", "gba": "agbcc"}.get(ov.get("platform")) or (
                "ido" if ov["compiler"]["toolchain"].startswith("ido") else "gcc-kmc")]
        text += "\nKnow-how you'd otherwise read with guide() (more topics: guide()):\n\n" + "\n\n".join(
            tools._guide(t) for t in topics)
        if (learned := self.net.learnings(s.slug)):
            text += ("\nWhat agents learned on this project so far (compiler quirks, conventions; add yours with "
                     f"add_note and no function):\n{learned}\n")
        if (wish := self.net.instructions(s.slug)):
            text += f"\nThe user's instructions for this project (they come first):\n{wish}\n"
        return text

    def _prompt(self, s: Session) -> str:
        ov = self.net.overview(s.slug)
        count = s.options.get("per_session", PER_SESSION)
        if ov.get("kind") == "fork":
            guides = ["fork", "objdiff", *tools.PLATFORM_GUIDES.get(ov.get("platform") or "", [])]
            return tools.FORK_PROMPT.format(title=ov["title"], platform=ov.get("platform_name") or "its platform",
                                            count=count, budget=network.stall_budget_text(),
                                            guides=", ".join(f'guide("{g}")' for g in guides))
        guide = {"psx": "psx", "ps2": "ps2", "gba": "agbcc"}.get(ov.get("platform")) or (
            "ido" if ov["compiler"]["toolchain"].startswith("ido") else "gcc-kmc")
        return tools.WORK_PROMPT.format(title=ov["title"], count=count, budget=network.stall_budget_text(),
                                        compiler_guide=guide)

    def _command(self, s: Session, token: str, workdir: Path) -> tuple[list[str], dict, str]:
        """The agent's command line, environment and what goes in on stdin (the prompt). Each helper connects only to
        this app's own MCP server, with its own token."""
        k = KINDS[s.kind]
        exe = _which(k.command)
        if not exe:
            raise RuntimeError(f"{k.name} is not installed")
        env = dict(os.environ, **{HELPER_ENV: str(self.home)})
        prompt = self.prompt(s)
        model = s.model
        if s.kind == "claude":
            cfg = workdir / "mcp.json"
            cfg.write_text(json.dumps({"mcpServers": {"decomp": {"type": "http", "url": self.mcp_url,
                                                                  "headers": {"Authorization": f"Bearer {token}"}}}}),
                           encoding="utf-8")
            cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg),
                   "--strict-mcp-config", "--allowedTools", "mcp__decomp", "--disallowedTools",
                   "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"]
            if model:
                cmd += ["--model", model]
        elif s.kind == "codex":
            env["DECOMP_TOKEN"] = token
            # The helper's own server; the user's MCP servers are switched off for the run (`-c` settings merge into
            # the user's config), as --strict-mcp-config does for Claude Code
            cmd = [exe, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "-C", str(workdir), "-s", "read-only"]
            for name in (_codex_config().get("mcp_servers") or {}):
                if re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    cmd += ["-c", f"mcp_servers.{name}.enabled=false"]
            cmd += ["-c", f'mcp_servers.decomp_agent={{url="{self.mcp_url}", bearer_token_env_var="DECOMP_TOKEN", '
                          'default_tools_approval_mode="approve", tool_timeout_sec=180}',
                    "-c", 'approval_policy="never"']
            if model:
                cmd += ["-m", model]
            cmd.append("-")  # the prompt comes from stdin
        elif s.kind == "antigravity":
            # The workspace's own MCP config; tool permission comes from the user's rule for decomp's tools (AGY_ALLOW).
            # TODO: Antigravity CLI 1.1.3 ignored a workspace's .agents/mcp_config.json and loaded only the MCP servers
            # in the user's settings (the old `decomp connect` entry, which the rewrite removes). If agy still does,
            # its helpers get no tools ("the agent never used the decomp tools", then paused): find agy's way to give
            # one run its own MCP server (a flag, or a settings folder of its own that keeps the user's sign-in).
            (workdir / ".agents").mkdir(exist_ok=True)
            (workdir / ".agents" / "mcp_config.json").write_text(json.dumps({"mcpServers": {"decomp": {
                "serverUrl": self.mcp_url, "headers": {"Authorization": f"Bearer {token}"}}}}), encoding="utf-8")
            cmd = [exe, "--output-format", "stream-json", "--input-format", "stream-json", "--print-timeout",
                   AGY_PRINT_TIMEOUT]
            if model:
                cmd += ["--model", model]
            prompt = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
        elif s.kind == "opencode":
            # "deny" takes the tools out of the request, and OpenCode Zen's free models then refuse it ("can only be
            # used from within OpenCode"). "ask" keeps the usual tools, and `opencode run` rejects every ask itself.
            zen = (model or _config_model("opencode") or "opencode/").startswith("opencode/")
            never = "ask" if zen else "deny"
            (workdir / "opencode.json").write_text(json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "mcp": {"decompdecomp": {"type": "remote", "url": self.mcp_url, "enabled": True,
                                         "headers": {"Authorization": f"Bearer {token}"}}},
                "permission": {"edit": never, "bash": never, "webfetch": never},
            }), encoding="utf-8")
            # --print-logs: opencode's JSON error only says "Unexpected server error"; the log line says what failed.
            # (No --dir: it runs in workdir, and OpenCode before 1.4 stops at the flag. No --log-level either: before
            # 2.0 it took only ERROR, from 2.0 only error, and either stops the other at once.)
            cmd = [exe, "run", *(["--standalone"] if _opencode_2(exe) else []), "--format", "json", "--print-logs"]
            if model:
                cmd += ["-m", model]
        else:
            raise RuntimeError(s.kind)
        return cmd, env, prompt

    def _run_cli(self, s: Session, token: str, workdir: Path) -> bool:
        cmd, env, prompt = self._command(s, token, workdir)
        s.push("info", f"session {s.runs}: {KINDS[s.kind].name} started")
        # its own session and process group: stopping it ends everything it started
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                start_new_session=True)
        s.proc, s.pid_start = proc, _proc_start(proc.pid)
        self._save(s)  # its process, should the app crash: the next start ends it
        errors: collections.deque = collections.deque(maxlen=20)

        def feed_prompt():
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()  # end of input: the agent works on this one prompt, then exits
            except OSError:  # it exited before reading (stderr says why)
                pass

        def drain_err():
            for line in proc.stderr:
                if (text := clean_stderr(s.kind, line)) and (not errors or errors[-1] != text):
                    errors.append(text)
        threading.Thread(target=feed_prompt, daemon=True).start()
        threading.Thread(target=drain_err, daemon=True).start()
        saw_tool = False
        said: collections.deque = collections.deque(maxlen=20)
        state: dict = {}
        last_error = refused = ""
        for line in proc.stdout:
            for kind, text in parse_line(s.kind, line, state):
                if kind == "usage":
                    self._account(s, json.loads(text))
                    continue
                if kind == "tool":
                    saw_tool = True
                    s.usage["tool_calls"] += 1
                said.append(text)
                if kind == "error":
                    last_error = text
                    refused = refused or (text if text.startswith("Antigravity refused") else "")
                s.push(kind, text)
            if s.stop.is_set():
                _terminate(proc)
                break
        code = proc.wait()
        s.proc = None
        if s.stop.is_set():
            return True
        everything = " ".join([*said, *errors])
        if refused:
            raise Fatal(refused)  # every retry would be refused the same way
        if _LIMIT.search(everything):
            s.limited = True  # whatever the exit code: Claude Code says so in an ordinary result
            s.limit_until = limit_reset(everything)
        elif code != 0 and (why := no_credit(everything)):
            raise Fatal(why)  # every agent on this account fails the same way until it's topped up
        if code != 0 and _THROTTLED.search(everything):
            s.throttled = True
        if code != 0 and _SIGNED_OUT.search(everything):
            s.signed_out = True
            s.error = f"{KINDS[s.kind].name} isn't signed in on this computer. {KINDS[s.kind].signin}"
            s.push("error", s.error)
            return False
        if code != 0:
            tail = " · ".join(list(errors)[-3:]) or last_error or f"exited with code {code}"
            s.error = tail[:300]
            s.push("error", f"exited with code {code}: {tail[:600]}" if tail != f"exited with code {code}" else tail)
            return False
        if not saw_tool:
            s.error = "the agent never used the decomp tools" + (f": {list(errors)[-1]}" if errors else "")
            s.push("error", s.error)
            return False
        s.error = ""
        return True

    # -- built-in agent for OpenAI-compatible APIs

    def _run_api(self, s: Session, agent) -> bool:
        """The agent loop for any OpenAI-compatible API. Once the helper is done with a function (it matched or
        released everything it held), that function's messages fold into one line: the conversation stays small
        without changing anything before that point, so the provider's prompt cache keeps serving the start of it (a
        conversation trimmed in place was sent again whole every step: 240K tokens per match on Ridge Racer V).
        Should one function's work still outgrow the model's context (`context_tokens`, or what a local llama.cpp
        server says it has), its older tool output is trimmed, harder when the server still says it's too long."""
        base = (s.options.get("base_url") or "").rstrip("/")
        model = s.options.get("model") or ""
        if not base or not model:
            raise RuntimeError("set the API address and model first")
        headers = {"Content-Type": "application/json"}
        if s.options.get("api_key"):
            headers["Authorization"] = f"Bearer {s.options['api_key']}"
        schema = api_tool_schemas(fork=bool(self.net.fork_slug(s.slug)))
        messages = [{"role": "system", "content": tools.INSTRUCTIONS},
                    {"role": "user", "content": self.prompt(s)}]
        count = int(s.options.get("per_session", PER_SESSION))
        used_tool, start, done = False, len(messages), 0
        with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT, connect=API_CONNECT_TIMEOUT)) as client:
            window = context_window(client, base, s.options)
            s.push("info", f"session {s.runs}: {model} via {base} ({window // 1000}K tokens of context)")
            budget = int(window * API_CONTEXT_SHARE) - _tokens(schema)  # room for the reply and the tools' definitions
            for _step in range(int(s.options.get("max_steps", API_STEPS))):
                if s.stop.is_set():
                    return True
                for _attempt in range(API_TRIM_RETRIES):
                    fit(messages, budget, keep=start)
                    r = client.post(f"{base}/chat/completions", headers=headers,
                                    json={"model": model, "messages": messages, "tools": schema, "tool_choice": "auto"})
                    if r.status_code == 200 or not _too_long(r):
                        break
                    budget = int(budget * API_TRIM_FACTOR)  # the server counts differently, or knows better: trim more
                if r.status_code != 200:
                    if (why := no_credit(r.text)) or r.status_code == 402:
                        raise Fatal(why or no_credit("payment required"))
                    s.limited = bool(_LIMIT.search(r.text))
                    s.limit_until = (limit_reset(r.text) or _retry_after(r)) if s.limited else None
                    s.throttled = not s.limited and (r.status_code == 429 or bool(_THROTTLED.search(r.text)))
                    s.error = f"API error {r.status_code}: {r.text[:300]}"
                    if _too_long(r):
                        s.error = ("the model's context is too small for this work even after trimming: give it at "
                                   "least 16K tokens (llama.cpp: -c 16384; Ollama: OLLAMA_CONTEXT_LENGTH=16384; LM "
                                   "Studio: Context Length) and set it in Agents & models")
                    s.push("error", s.error)
                    return False
                reply = r.json()
                msg = reply["choices"][0]["message"]
                if isinstance(reply.get("usage"), dict):
                    self._account(s, api_usage(reply["usage"]))
                if msg.get("reasoning_content"):
                    s.push("think", msg["reasoning_content"][:2000])
                if msg.get("content"):
                    s.push("text", msg["content"])
                calls = msg.get("tool_calls") or []
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
                if not calls:
                    return used_tool
                finishing = False
                for c in calls:
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    used_tool = True
                    s.usage["tool_calls"] += 1
                    s.push("tool", f"{name}({_args_brief(args)})")
                    result = tools.call(self.net, agent, s.slug, name, args)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                    finishing |= name in ("compile_and_diff", "release_function")
                # Done with what it held (in a room the host holds the claim: nothing here says when): what led up
                # to this turn folds into a line, and this turn stays whole (a fork's match shows the file's data to
                # fix next) until the next fold
                if finishing and s.slug not in self.net.links and not self._holds_claim(agent.id):
                    done += 1
                    turn = len(messages) - len(calls) - 1
                    if start < turn:
                        fold(messages, start, turn, done, self._done_line(s, messages[start:], done, count))
                        start += FOLDED
                    if done >= count:  # the session's claims are done: the next one starts fresh
                        return True
        return used_tool

    def _holds_claim(self, agent_id: int) -> bool:
        return self.net.store.one("SELECT 1 FROM claims WHERE agent_id=?", agent_id) is not None

    def _done_line(self, s: Session, span: list[dict], done: int, count: int) -> str:
        """One line for a function the API helper is done with: what became of it, and what's next."""
        names, compiles = [], 0
        for m in span:
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                if fn["name"] not in ("compile_and_diff", "release_function"):
                    continue
                compiles += fn["name"] == "compile_and_diff"
                try:
                    said = json.loads(fn.get("arguments") or "{}").get("function") or ""
                except ValueError:
                    said = ""
                names += [n.strip() for n in str(said).split(",") if n.strip() and n.strip() not in names]
        matched = []
        for name in names:
            try:
                if self.net._function(name, s.slug)["status"] == "matched":
                    matched.append(name)
            except NetworkError:
                continue
        aside = [n for n in names if n not in matched]
        what = "; ".join(bit for bit in (f"matched {', '.join(matched)}" if matched else "",
                                         f"set {', '.join(aside)} aside with notes" if aside else "") if bit)
        nxt = "claim the next one" if done < count else "that was the last one for this session: stop now"
        return (f"(The work up to your last call, folded into this line to keep the conversation short: "
                f"{plural(compiles, 'compile')}; {what or 'nothing held any more'}.) {done} of {count} claims this "
                f"session are done: {nxt}.")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def api_usage(usage: dict) -> dict:
    """An OpenAI-compatible reply's usage as `usage` items count it: its prompt tokens include those served from the
    provider's cache (OpenAI: prompt_tokens_details.cached_tokens, DeepSeek: prompt_cache_hit_tokens)."""
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = _num(details, "cached_tokens") or _num(usage, "prompt_cache_hit_tokens")
    prompt, output = _num(usage, "prompt_tokens"), _num(usage, "completion_tokens")
    if not (prompt or output):
        return {"tokens": _num(usage, "total_tokens"), "turns": 1}
    return {"input": max(0, prompt - cached), "cache_read": cached, "output": output, "turns": 1}


def _retry_after(r) -> float | None:
    """A server's Retry-After header (in seconds) as a time."""
    said = (r.headers.get("retry-after") or "").strip()
    return time.time() + float(said) if re.fullmatch(r"\d+(?:\.\d+)?", said) else None


FOLDED = 2  # messages a fold leaves: one call and its result


def fold(messages: list[dict], start: int, end: int, n: int, line: str) -> None:
    """Replace messages[start:end] (a finished function's work) with one call and `line` as its result: a call and
    its result keep the conversation valid for every API, and what came before stays as it was sent."""
    call = f"folded-{n}"
    messages[start:end] = [{"role": "assistant", "content": None, "tool_calls": [
                               {"id": call, "type": "function", "function": {"name": "project_info", "arguments": "{}"}}]},
                           {"role": "tool", "tool_call_id": call, "content": line}]


def _tokens(obj) -> int:
    """A cautious token estimate (CHARS_PER_TOKEN)."""
    return len(obj if isinstance(obj, str) else json.dumps(obj)) // CHARS_PER_TOKEN + 4


def _too_long(r) -> bool:
    """The server refused the request because it doesn't fit the model's context."""
    return r.status_code in (400, 413, 422, 500) and bool(re.search(
        r"context|too long|exceed|maximum.*tokens|n_ctx|prompt is too|reduce the length", r.text[:2000], re.I))


def fit(messages: list[dict], budget: int, keep: int = 2) -> int:
    """Shrink the conversation to `budget` tokens, only from `keep` on (what's before it, finished functions folded
    into a line each, stays as it was sent, for the provider's prompt cache): older tool output goes first (a
    function's assembly and diffs from several steps ago), then older reasoning. The latest exchange stays whole.
    Returns the estimate after."""
    total = sum(_tokens(m) for m in messages)
    keep_from = max(keep, len(messages) - 2)
    for kinds in (("tool",), ("assistant",)):
        for m in messages[max(2, keep):keep_from]:
            if total <= budget:
                return total
            text = m.get("content")
            if m.get("role") in kinds and isinstance(text, str) and len(text) > TRIM_ABOVE:
                before = _tokens(m)
                m["content"] = text[:TRIM_TO] + "\n[... trimmed to save context: call the tool again if you need this]"
                total -= before - _tokens(m)
    return total


def context_window(client, base: str, options: dict) -> int:
    """The model's context in tokens: what the user set, else what a llama.cpp server reports (/props), else a
    guess (local servers often run small contexts)."""
    try:
        if int(options.get("context_tokens") or 0) >= MIN_CONTEXT:
            return int(options["context_tokens"])
    except (TypeError, ValueError):
        pass
    if is_local(base):
        try:
            r = client.get(re.sub(r"/v1$", "", base) + "/props", timeout=5)
            n = (r.json().get("default_generation_settings") or {}).get("n_ctx") if r.status_code == 200 else None
            if isinstance(n, int) and n >= MIN_CONTEXT:
                return n
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return LOCAL_CONTEXT
    return REMOTE_CONTEXT


def api_tool_schemas(fork: bool = False) -> list[dict]:
    """OpenAI-style tool definitions for the built-in agent, from the same tools CLI agents get."""
    s = lambda **props: {"type": "object", "properties": props, "required": [k for k, v in props.items() if v.pop("req", False)]}
    st = lambda desc, req=False: {"type": "string", "description": desc, "req": req}
    it = lambda desc: {"type": "integer", "description": desc}
    bo = lambda desc: {"type": "boolean", "description": desc}
    defs = {
        "project_info": ("Which game, compiler and progress.", s()),
        "claim_function": ("Claim the next function (or one by name/address). Returns assembly, data, best attempt, "
                           "notes and a draft. count > 1: a batch of small functions to judge together.",
                           s(function=st("optional name or address"), strategy=st("finish | easiest | closest | hardest | random"),
                             count=it("up to 6 small functions at once"))),
        "get_function": ("Show any function by name or address.", s(function=st("name or address", True))),
        "compile_and_diff": ("Compile C for the function, link it at its address and diff against the ROM. "
                             "Score 0 = matched. Several functions: names comma-separated, all definitions in source.",
                             s(function=st("function name, or several: a, b, c", True), source=st("C source", True))),
        "release_function": ("Give up a function with notes for the next agent.",
                             s(function=st("function", True), notes=st("what you tried, what is left", True))),
        "get_context": ("The shared header included in every compile.", s()),
        "edit_context": ("Replace `old` (exactly once) with `new` in the shared header, or append when old is empty.",
                         s(old=st("text to replace, or empty", True), new=st("replacement", True),
                           message=st("why", True))),
        "rename_symbol": ("Give a function or data address a real name.",
                          s(old=st("current name", True), new=st("new name", True), message=st("why"))),
        "split_function": ("Split a merged function at an address.",
                           s(function=st("function", True), at=st("address like 0x80001234", True), new_name=st("name"))),
        "read_data": ("Hex dump of the original's data at an address or symbol.", s(what=st("address or symbol", True),
                                                                                    length=it("bytes, up to 512"))),
        "get_class": ("C++ class layout found in the game's code: fields by offset and virtual functions by slot. "
                      "Give a vtable address or one of its functions; empty lists the classes.",
                      s(what=st("vtable address, or a virtual function / constructor"))),
        "compiler_map": ("Which compiler built each part of the game, where it differs from today's. survey=true starts "
                         "the judge's survey of other compilers in the background; check_ranges=true looks at the "
                         "applied ranges again.", s(survey=bo("start the survey"), check_ranges=bo("check applied ranges"))),
        "list_functions": ("List functions by status.", s(status=st("open | matched | all"), limit=it("max rows"))),
        "add_note": ("Leave a note for other agents.", s(text=st("note", True), function=st("function"))),
        "suggest": ("Suggest a setup change for the user (another compiler or flags), with evidence.",
                    s(text=st("what and why", True), compiler=st("toolchain id"), flags=st("flags"),
                      function=st("a function whose best attempt should match with it: checked now"),
                      ranges=st("address ranges it built, when not the whole game: 0x10BFD0-0x10CD40, ..."),
                      source=st("the C to check it with (default: the best attempt)"))),
        "try_compiler": ("Compile a function's C with another compiler and flags, recording nothing: test before suggesting.",
                         s(function=st("function", True), compiler=st("toolchain id", True), flags=st("flags"),
                           source=st("the C (default: the best attempt)"))),
        "search_notes": ("Search notes.", s(query=st("text", True))),
        "find_similar": ("Functions that look like this one, matched ones first, with the best one's source.",
                         s(function=st("function", True), limit=it("how many"))),
        "guide": ("Decompilation know-how; no topic lists topics.", s(topic=st("topic"))),
        "read_file": ("Read a project file with line numbers.", s(path=st("path from the project root", True),
                                                                 start=it("first line"), count=it("lines"))),
        "search_code": ("Regex search over the project's C/C++ sources and headers.",
                        s(pattern=st("regular expression", True), glob=st("optional file glob"))),
        "edit_file": ("Replace `old` (exactly once) with `new` in a source/header file, or append when old is empty. "
                      "Undone if any match breaks.", s(path=st("file", True), old=st("text to replace, or empty", True),
                                                       new=st("replacement", True), message=st("why", True))),
        "file_status": ("How far a source file (or a function's file) is from being linked: functions left, its data "
                        "next to the original's, why the last link failed.", s(path=st("file or function", True))),
        "split_file": ("Divide a source file at a function: it and the rest of the file's code (and data from the given "
                       "offsets) go to a new file. Kept only if the game still builds identically.",
                       s(path=st("file", True), at=st("function name or .text offset", True),
                         new_name=st("new file path (optional)"), data=st("e.g. .rodata:0x88 .data:0x28 (optional)"))),
    }
    if fork:
        defs["compile_and_diff"] = ("Write one complete function definition into its source file, build it and diff "
                                    "with objdiff. 100% / score 0 = matched.",
                                    s(function=st("function name", True), source=st("the function definition", True)))
    names = set(tools.tool_names(fork))
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
            for n, (d, p) in defs.items() if n in names]
