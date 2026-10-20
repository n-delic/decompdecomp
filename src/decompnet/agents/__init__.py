"""The connector: how decompdecomp talks to a helper agent. Every agent the app can start is a module in this folder
(agents/claude.py, codex.py, antigravity.py, opencode.py, api.py) that makes an `Agent` below and fills in its parts;
the list at the end of this file registers them. The rest of the app (the helpers' manager in app/agents.py, the
routes, the GUI) asks the agent of a helper, `agents.get(kind)`, and never compares agent names. A person or an agent
adding support for another agent reads its module and this file, nothing else.

An agent is one of two sorts:
- A program the user installs and signs in to (Claude Code, Codex, Antigravity CLI, opencode). The app runs it
  headless, once per session, in a private working folder, connected only to the app's own MCP endpoint on 127.0.0.1
  with a token of its own (`session_command`): MCP is the app's private wiring to it. The prompt goes in on stdin
  (`stdin`); what it prints, one JSON object a line, is read into the helper's feed (`parse`).
- An agent built into the app (the API helper): no program, a loop in the app that talks to a model's API itself and
  calls the tools directly (`run`).

What's the same for every agent (starting and restarting helpers, waiting out usage limits, their records, ending
everything they started when the app quits) is in app/agents.py. Adding an agent: copy agents/template.py, fill in
its parts, add it to the list at the end of this file, and make tests/test_agent_kinds.py pass (CONTRIBUTING.md has
the steps).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

# An agent's `--version` and its list of models are asked at most this often (seconds): running an agent takes
# seconds, and `agy models` starts the MCP servers in Antigravity's settings
VERSION_CACHE = 5 * 60
MODELS_CACHE = 10 * 60
CLI_TIMEOUT = 15
MODELS_TIMEOUT = 30
MODELS_LISTED = 200
VERSION_CHARS = 60  # of the first line `--version` prints
SAID_CHARS = 400  # what the feed keeps of an error, or of a line that isn't JSON
STDERR_CHARS = 300  # of an error an agent printed on stderr in its own format

# what each agent said its version is, and the models it listed, with when it was asked: {id: (time, answer)}
known_versions: dict[str, tuple[float, str | None]] = {}
_models: dict[str, tuple[float, list[dict]]] = {}


class Agent:
    """One agent: the facts below, and the parts the app calls. A part an agent doesn't need keeps the default here,
    which does nothing or says the agent can't do it."""

    id = ""  # what a helper's record stores as its kind ("claude"): keep it once helpers exist (add old ones to aliases)
    name = ""  # what the app calls it: "Claude Code"
    command = ""  # its program, looked for on PATH ("claude"); "" for an agent built into the app (see `run`)
    vendor = ""  # who makes it, or whose models it runs, for the launcher: "Anthropic"
    install = ""  # the shell command that installs it ("Install it for me" runs it); "" when there's nothing to install
    signin = ""  # how to sign in to it, in a sentence: shown when it isn't signed in, and when it fails on a sign-in
    docs = ""  # its documentation, linked from the launcher
    aliases: tuple[str, ...] = ()  # ids older versions stored for it ("gemini" for "antigravity")
    family = ""  # who made the models it runs, when that's one maker ("claude"); "": the model in a helper's name says
    models_args: tuple[str, ...] = ()  # the arguments that make it list the models the user's sign-in offers
    tool_meta: dict = {}  # what it reads on the definition of each of decompdecomp's MCP tools (tools.ALWAYS_LOAD)
    recommended = False  # the launcher lists it first, and starts on it until the user picks another
    uses_endpoints = False  # its model and key come from the API endpoints saved in Agents & models, not a sign-in
    takes_effort = False  # the launcher offers a reasoning effort: with the model's name ("provider/model#high"), or
    # for an agent whose model is an endpoint's, as the helper's `effort` option
    efforts: tuple[str, ...] = ("low", "medium", "high", "max")  # the efforts it offers, when it takes one

    def __repr__(self) -> str:
        return f"<agent {self.id}>"

    def describe(self) -> dict:
        """What the GUI shows about it, before what app/agents.py `detect` finds out (installed, version, models)."""
        return {"id": self.id, "name": self.name, "vendor": self.vendor, "signin": self.signin, "docs": self.docs,
                "install": self.install, "recommended": self.recommended, "uses_endpoints": self.uses_endpoints,
                "takes_effort": self.takes_effort, "efforts": list(self.efforts)}

    def label(self, name: str, options: dict, used: set[str]) -> str:
        """What the GUI calls a helper of it (`name`: its identity; `used`: the labels its working helpers have)."""
        return name

    # ------------------------------------------------------------------ what's installed (the launcher)

    def version(self, path: str, run: bool = True) -> str | None:
        """Its version, as `<path> --version` says it, asked at most every VERSION_CACHE seconds (`run` False: only
        what it said before)."""
        cached = known_versions.get(self.id)
        if run and (cached is None or time.time() - cached[0] > VERSION_CACHE):
            try:
                res = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", stdin=subprocess.DEVNULL, timeout=CLI_TIMEOUT)
                said = (res.stdout or res.stderr).strip()
                version = said.splitlines()[0][:VERSION_CHARS] if said else None
            except (OSError, subprocess.TimeoutExpired):
                version = None
            known_versions[self.id] = cached = (time.time(), version)
        return cached[1] if cached else None

    def models(self, path: str | None, run: bool = True) -> list[dict]:
        """Models to offer: [{"id", "name", "note"}]. By default what `<path> <models_args>` lists (what the user's
        sign-in offers), read by read_models, asked at most every MODELS_CACHE seconds (`run` False: only what was
        found before). None listed: the launcher offers the agent's default and any model name typed in."""
        if not self.models_args or not path:
            return []
        cached = _models.get(self.id)
        if cached and (not run or time.time() - cached[0] < MODELS_CACHE):
            return cached[1]
        if not run:
            return []
        try:
            res = subprocess.run([path, *self.models_args], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", stdin=subprocess.DEVNULL, timeout=MODELS_TIMEOUT)
            lines = [line.strip() for line in res.stdout.splitlines()]
        except (OSError, subprocess.TimeoutExpired):
            lines = []
        out = self.read_models(lines)[:MODELS_LISTED]
        _models[self.id] = (time.time(), out)
        return out

    def read_models(self, lines: list[str]) -> list[dict]:
        """The models in what `<path> <models_args>` printed, a line each: [{"id", "name", "note"}]."""
        return []

    def config_model(self) -> str | None:
        """The model the user set as default in the agent's own settings, if any: a helper started without a model
        is named after it."""
        return None

    # ------------------------------------------------------------------ one session of a program the app runs

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        """One session's command line (`exe`: its program, found), and what it adds to the environment. The session
        runs in `workdir`, where it writes its own config files. It connects only to `mcp_url`, the app's MCP server,
        with `token` (never to the user's own MCP servers), and may use only decompdecomp's tools there. `model`: ""
        for the agent's default."""
        raise RuntimeError(f"{self.name} isn't a program the app runs")

    def stdin(self, prompt: str) -> str:
        """What goes in on stdin: the prompt, as the agent reads it. Never on the command line: no length limit, and
        nothing in it is parsed."""
        return prompt

    def parse_line(self, line: str, state: dict | None = None) -> list[tuple[str, str]]:
        """(type, text) items for the helper's feed from one line the agent printed: the same for every agent (a line
        that isn't JSON is shown as it is), with `parse` reading each JSON object."""
        line = line.strip()
        if not line:
            return []
        try:
            event = json.loads(line)
        except ValueError:
            return [("info", line[:SAID_CHARS])]
        if not isinstance(event, dict):
            return []
        return [item for item in self.parse(event, {} if state is None else state) if item and item[1]]

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """The feed items in one JSON object the agent printed: ("text", ...), ("think", ...), ("tool", "name(args)":
        tool_name and args_brief), ("info", ...), ("error", ...), and what the session spent (usage_item). `state`
        carries what spans lines, for one session (Antigravity streams its text in pieces)."""
        return []

    def clean_stderr(self, line: str) -> str:
        """One line of the agent's stderr as worth showing, or "" for noise: the last ones say why a session failed."""
        return line.strip()

    def fatal(self, error: str) -> bool:
        """Whether an error it said is one every try would meet the same way (Antigravity refusing decompdecomp's
        tools): the helper pauses with it, instead of starting again."""
        return False

    # ------------------------------------------------------------------ an agent built into the app

    def run(self, manager, session, agent) -> bool:
        """One session's work, for an agent built into the app (no `command`): the manager (app/agents.py: its
        `net`, `prompt`, `account`), the helper's Session, and its identity in the network. True when the session
        went well; it raises app.agents.Fatal when the user has to change something first."""
        raise RuntimeError(f"{self.name} is a program the app runs: it has no loop of its own")


# ---------------------------------------------------------------------------------- what the agents' modules share
# Reading what an agent prints (`parse`), and its own settings file (`config_model`).

_TOOL_PREFIXES = ("mcp__decomp__", "mcp_decomp_", "decomp_agent.", "decomp_agent__", "decomp__", "decomp_", "decomp.")
ARG_CHARS = 40  # an argument longer than this is cut in the feed


def tool_name(name: str) -> str:
    """A tool's name without the prefix the agent puts on MCP tools ("mcp__decomp__claim_function")."""
    for prefix in _TOOL_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def args_brief(args) -> str:
    """A tool call's arguments in a few words for the feed: C source as its number of lines."""
    if not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        if k == "source":
            parts.append(f"{len(str(v).splitlines())} lines of C")
        elif isinstance(v, str):
            parts.append(v if len(v) < ARG_CHARS else v[:ARG_CHARS - 3] + "…")
        elif v not in (None, "", {}):
            parts.append(str(v))
    return ", ".join(parts)


def error_text(err) -> str:
    """The message in an agent's error object, whatever its shape (opencode nests it under data)."""
    if isinstance(err, dict):
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        msg = err.get("message") or data.get("message") or ""
        name = err.get("name") or err.get("type") or ""
        if msg and name and name not in ("UnknownError", "Error") and name not in msg:
            return f"{name}: {msg}"
        return str(msg or name or json.dumps(err)[:STDERR_CHARS])
    return str(err or "")


def usage_item(**counts) -> tuple[str, str] | None:
    """A `usage` feed item: what a session spent (usd), its tokens (input, output, cache_read, cache_write; or
    `tokens` when the agent only reports a total) and turns. None when it says nothing."""
    said = {k: v for k, v in counts.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v}
    return ("usage", json.dumps(said)) if said else None


def number(d: dict, key: str) -> int:
    """A count in an agent's JSON, 0 when it's missing or isn't a whole number."""
    v = d.get(key)
    return v if isinstance(v, int) else 0


def read_json(path: Path) -> dict:
    """An agent's own JSON settings file, {} when it's missing or broken."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


# ---------------------------------------------------------------------------------- the agents

def _registry() -> list[Agent]:
    from .antigravity import ANTIGRAVITY
    from .api import API
    from .claude import CLAUDE
    from .codex import CODEX
    from .opencode import OPENCODE

    # the order the Agents page lists them in (the launcher puts the recommended one first)
    return [CLAUDE, CODEX, ANTIGRAVITY, OPENCODE, API]


AGENTS: list[Agent] = _registry()
_BY_ID = {a.id: a for a in AGENTS}
_OLD_IDS = {old: a.id for a in AGENTS for old in a.aliases}
# who made a helper's models, by the kind its name starts with (network.model_family)
FAMILIES = {key: a.family for a in AGENTS for key in (a.id, *a.aliases) if a.family}
# what every agent reads on each MCP tool's definition, together (app/tools.py)
TOOL_META = {key: value for a in AGENTS for key, value in a.tool_meta.items()}


def kind_id(kind: str) -> str:
    """The id an agent has now, for one older versions stored ("gemini": "antigravity"); any other stays as it is."""
    return _OLD_IDS.get(kind, kind)


def get(kind: str) -> Agent:
    """The agent a helper's kind names (older ids too). An unknown one is an error that says which agents there are."""
    found = _BY_ID.get(kind_id(kind or ""))
    if found is None:
        raise ValueError(f"decompdecomp has no agent called {kind!r}: start one of "
                         f"{', '.join(a.name for a in AGENTS)}")
    return found
