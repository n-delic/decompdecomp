"""Oh My Pi (`omp`), an agent CLI for any provider's models (Anthropic, OpenAI, z.ai, Mistral, ...). Each session runs
`omp -p --mode json` in a folder whose `.mcp.json` names the app's MCP server and nothing else, with a settings file
the app writes that turns the user's own omp setup off for the run; it prints one JSON event a line.

omp keeps its sign-in and its MCP servers in one folder (`~/.omp/agent`), so the app can't hand it a config file the
way zcode takes one: it leaves the user's sign-in and models where they are (`PI_CODING_AGENT_DIR` untouched, so the
helper uses the sign-in they already have) and takes the user's servers out of omp's discovery instead, with omp's own
settings. `disabledProviders: [native]` stops omp's own `mcp.json` — the user's MCP servers — from loading, and
`mcp.enableProjectConfig: true` lets the session folder's `.mcp.json` through, the app's server named as the only one.
`--no-tools` leaves no built-in tool (no shell, no file edits, no web), `--no-extensions --no-skills --no-rules
--no-context-files --no-lsp` leave its extras out, and the settings overlay switches advisor, memory, autolearn and its
other background work off, so a session reads the project through decompdecomp's tools and nothing else.

What it prints (omp 18): a `session` line, then `turn_start`, `message_start`/`message_end` for every message, streamed
`message_update`s (text and thinking deltas, tool calls), `tool_execution_start`/`tool_execution_end`, and at the end
`agent_end`. A message's full text, thinking and usage come with its `message_end`; a failed turn ends with
`stopReason: "error"` and `errorMessage`. omp names MCP tools `mcp__<server>_<tool>` (`mcp__decomp_claim_function`).
"""

from __future__ import annotations

import json
import os
import re
from itertools import zip_longest
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, number, tool_name, usage_item

MCP_SERVER = "decomp"  # the app's MCP server, and the prefix of its tools (mcp__decomp_claim_function)
MCP_FILE = ".mcp.json"  # omp reads a bare .mcp.json in the folder it starts in ("root fallback"), whatever else is on
SETTINGS_FILE = "omp-settings.yml"  # the session's own settings, loaded with --config
READY_ENV = "OMP_MCP_REQUIRE_READY"  # fail before the first turn when the app's MCP server isn't connected
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")  # what --thinking takes (and a
                                                                                  # model's trailing ":max")
# The session's settings: the CLI overlay wins over the user's own config.yml for this run (docs: settings precedence).
# `native` is omp's own MCP discovery, so switching it off drops the user's MCP servers; the folder's .mcp.json is a
# fallback source, not that one, so it stays. Everything else that would work in the background or reach the internet
# is off, and tool approval is yolo so a headless run never waits for a prompt.
SETTINGS = """\
enabledProviders: []
disabledProviders:
  - native
mcp:
  enableProjectConfig: true
tools:
  approvalMode: yolo
advisor:
  enabled: false
memory:
  backend: "off"
autolearn:
  enabled: false
ttsr:
  enabled: false
hindsight:
  autoRecall: false
  autoRetain: false
startup:
  checkUpdate: false
  setupWizard: false
skills:
  enabled: false
lsp:
  enabled: false
bash:
  enabled: false
browser:
  enabled: false
web_search:
  enabled: false
generate_image:
  enabled: false
"""
_CODE_FRAME = re.compile(r"^\s*(?:\d+\s*\||at\s|\^)")  # the code and stack omp prints after an error
NODE_INTERNALS = "B:/~BUN/root/"  # where an error in omp's own bundle came from


def agent_dir() -> Path:
    """The folder omp keeps its settings, sign-in and mcp.json in: `PI_CODING_AGENT_DIR`, a profile's (OMP_PROFILE),
    or the user's own `~/.omp/agent`. `PI_CONFIG_DIR` moves the `.omp` root."""
    if own := os.environ.get("PI_CODING_AGENT_DIR"):
        return Path(own)
    root = Path(os.environ.get("PI_CONFIG_DIR") or Path.home() / ".omp")
    profile = os.environ.get("OMP_PROFILE") or os.environ.get("PI_PROFILE")
    return root / "profiles" / profile / "agent" if profile else root / "agent"


def default_model(path: Path) -> str | None:
    """`modelRoles.default` in omp's config.yml, read without a YAML library (the file is omp's, and one key is all
    the app wants): the `default:` line of the `modelRoles:` block."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    inside = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not inside:
            inside = re.fullmatch(r"modelRoles:\s*", line) is not None
            continue
        if not line[:1].isspace():
            break  # the next top-level key: modelRoles had no default
        match = re.fullmatch(r"\s+default:\s*(.+?)\s*", line)
        if match:
            return match.group(1).split(" #", 1)[0].strip().strip("'\"") or None
    return None


class OhMyPi(Agent):
    id = "omp"
    name = "Oh My Pi"
    command = "omp"
    vendor = "any provider (Anthropic, OpenAI, z.ai, ...)"
    install = "curl -fsSL https://omp.sh/install | sh"
    windows_install = "irm https://omp.sh/install.ps1 | iex"
    signin = "Run `omp login` once in a terminal and pick a provider."
    docs = "https://github.com/can1357/oh-my-pi"
    models_args = ("models", "--json")
    takes_effort = True  # omp --thinking: with the model's name ("provider/model#high"), or its own ":high"

    def read_models(self, lines: list[str]) -> list[dict]:
        """`omp models --json`: {"models": [{"selector", "name", "provider", "kind", ...}]}, one JSON object.

        It lists each provider's models in turn, alphabetically, and the launcher keeps the first MODELS_LISTED:
        taken as they come, all 200 were one provider's (`charm-hyper`), so nothing else could be picked. One model
        per provider per round instead, so the list covers every provider the user is signed in to."""
        try:
            found = json.loads("\n".join(lines) or "{}")
        except ValueError:
            return []
        rows = found.get("models") if isinstance(found, dict) else None
        groups: dict[str, list[dict]] = {}
        for model in rows if isinstance(rows, list) else []:
            if not isinstance(model, dict) or model.get("kind", "chat") != "chat":
                continue
            selector = model.get("selector")
            if not isinstance(selector, str) or not selector:
                continue
            provider = str(model.get("provider") or "")
            groups.setdefault(provider, []).append(
                {"id": selector, "name": str(model.get("name") or selector.split("/", 1)[-1]), "note": provider})
        return [row for round_ in zip_longest(*groups.values()) for row in round_ if row is not None]

    def config_model(self) -> str | None:
        """The default model in omp's settings: a helper started without a model is named after it."""
        return default_model(agent_dir() / "config.yml") or default_model(agent_dir() / "config.yaml")

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        (workdir / MCP_FILE).write_text(
            json.dumps({"mcpServers": {MCP_SERVER: {"type": "http", "url": mcp_url,
                                                     "headers": {"Authorization": f"Bearer {token}"}}}}),
            encoding="utf-8")
        settings = workdir / SETTINGS_FILE
        settings.write_text(SETTINGS, encoding="utf-8")
        model, _, effort = (model or "").partition("#")
        name, _, level = model.partition(":")
        if level in THINKING:  # omp's own thinking suffix (its config's default carries one: provider/model:max)
            model, effort = name, effort or level
        cmd = [exe, "-p", "--mode", "json", "--no-session", "--no-extensions", "--no-skills", "--no-rules",
               "--no-context-files", "--no-lsp", "--no-tools", "--no-title", "--config", str(settings)]
        return (cmd + (["--model", model] if model else []) + (["--thinking", effort] if effort else []),
                {READY_ENV: "1"})

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t == "message_end":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if message.get("role") != "assistant":
                return out
            said = {"text": [], "thinking": []}
            for part in message.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get(part.get("type")), str) \
                        and part.get("type") in said and (body := part[part["type"]].strip()):
                    said[part["type"]].append(body)
            if said["thinking"]:
                out.append(("think", "\n".join(said["thinking"])))
            if said["text"]:
                out.append(("text", "\n".join(said["text"])))
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
            if number(usage, "totalTokens") or cost.get("total"):  # a failed request spends nothing
                out.append(usage_item(usd=cost.get("total"), input=number(usage, "input"),
                                      output=number(usage, "output"), cache_read=number(usage, "cacheRead"),
                                      cache_write=number(usage, "cacheWrite"), turns=1))
            if message.get("stopReason") == "error":
                state["failed"] = True
                said = str(message.get("errorMessage") or "the model's request failed")
                out.append(("error", said[:SAID_CHARS]))
        elif t == "tool_execution_start":
            out.append(("tool", f"{tool_name(str(event.get('toolName') or ''))}({args_brief(event.get('args'))})"))
        elif t == "tool_execution_end":
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            if event.get("isError") or result.get("isError"):
                text = " ".join(str(c.get("text") or "") for c in result.get("content") or [] if isinstance(c, dict))
                out.append(("error", (text.strip() or "the tool failed")[:SAID_CHARS]))
        elif t == "agent_end" and not state.get("failed"):
            out.append(("info", "session finished"))
        return out

    def clean_stderr(self, line: str) -> str:
        """omp prints an error as `error: ...`, then the line of its bundle it came from and a stack: keep the words."""
        if not line.strip() or _CODE_FRAME.match(line) or NODE_INTERNALS in line:
            return ""
        return line.strip()


OMP = OhMyPi()
