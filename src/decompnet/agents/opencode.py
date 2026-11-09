"""opencode, an agent CLI for any provider's models (DeepSeek, Qwen, Kimi, OpenCode Zen's free ones...). Each session
runs `opencode run --format json` in a folder whose opencode.json names the app's MCP server and nothing else; it
prints one JSON event a line, and says what failed on stderr (--print-logs)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, read_json, tool_name, usage_item

ZEN = "opencode/"  # OpenCode Zen's models: their names start with it, and opencode's default is one of them
STANDALONE_FROM = 2  # the major version that runs sessions in a shared service unless told --standalone
PLACEHOLDER = "Unexpected server error"  # its JSON error when the log line says what failed
MCP_SERVER = "decompdecomp"  # the app's MCP server, in the config and in the plugin that waits for it
MCP_WAIT_SECONDS = 10  # how long opencode 2's first prompt waits for that server to connect (#24)
MCP_SETTLE_MS = 300  # then for the registry's 100 ms debounce to publish its tools
PLUGIN_DIR = "decompdecomp-wait"  # the plugin's folder in a helper's own folder, next to its opencode.json
_PLUGIN_SOURCE = Path(__file__).with_name("opencode_wait.js")


def _wait_plugin() -> str:
    """opencode 2's plugin for #24, with the app's server name and timings filled in. It waits before the first prompt
    until the app's MCP tools are registered, because opencode connects MCP in the background and its first request
    otherwise carries no tools (upstream #51812, fix #54942). Remove it once opencode waits itself."""
    source = _PLUGIN_SOURCE.read_text(encoding="utf-8")
    return (source.replace("__DECOMP_MCP_SERVER__", json.dumps(MCP_SERVER))
                  .replace("__DECOMP_MCP_DEADLINE_MS__", str(MCP_WAIT_SECONDS * 1000))
                  .replace("__DECOMP_MCP_SETTLE_MS__", str(MCP_SETTLE_MS)))


class Opencode(Agent):
    id = "opencode"
    name = "opencode"
    command = "opencode"
    vendor = "any provider (DeepSeek, Qwen, Kimi, ...)"
    install = "curl -fsSL https://opencode.ai/install | bash"
    windows_install = "npm install -g opencode-ai"
    signin = "Run `opencode auth login` once to add a provider (DeepSeek, OpenRouter, ...)."
    docs = "https://opencode.ai"
    models_args = ("models",)
    takes_effort = True  # opencode 2 picks a model's reasoning effort as a variant of it: provider/model#high

    def read_models(self, lines: list[str]) -> list[dict]:
        """`opencode models`: "<provider>/<model>" per model."""
        rows = [r for r in lines if not self.validate_model(r) and r]
        return [{"id": r, "name": r.split("/", 1)[1], "note": r.split("/", 1)[0]} for r in rows]

    def resolve_known_model(self, model: str, known: list[dict]) -> str:
        model = model.strip()
        base, separator, effort = model.partition('#')
        if base and '/' not in base:
            matches = sorted({r['id'] for r in known if r.get('id','').split('/',1)[-1] == base})
            if len(matches) == 1:
                return matches[0]+(separator+effort if separator else '')
            choices = ' Choose one of: '+', '.join(matches[:8])+'.' if matches else ' Refresh models and select its full ID, for example provider/model.'
            raise ValueError('The opencode model needs a provider/model ID; this short name is ambiguous or was not discovered.'+choices)
        return model

    def validate_model(self, model: str) -> str | None:
        if not model:
            return None
        base, separator, effort = model.partition('#')
        if any(segment in {'.','..'} for segment in base.split('/')) or not re.fullmatch(r'[\w.-]+/[\w.:@+%~-]+(?:/[\w.:@+%~-]+)*',base) or (separator and not re.fullmatch(r'[\w.-]+',effort)):
            return 'The opencode model needs a provider/model ID with nonempty segments. Refresh models or enter the full ID.'
        return None

    def fatal(self, error: str) -> bool:
        return bool(re.search(r'ProviderModelNotFoundError|\bModel not found\b|opencode model needs a provider/model ID',error,re.I))

    def config_model(self) -> str | None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return read_json(base / "opencode" / "opencode.json").get("model") or None

    def standalone(self, exe: str) -> bool:
        """opencode 2 runs every `opencode run` in one background service, which connects the user's own MCP servers
        once, without a helper's session token: both GTA San Andreas helpers worked as the user's one chat agent, on
        one claim, whatever their folders said. --standalone gives each helper a server of its own, started from its
        own process (before 2.0 there's no such service, and the flag would stop it)."""
        text = self.version(exe, run=False) or self.version(exe)
        m = re.search(r"(\d+)\.\d+", text or "")
        return bool(m) and int(m.group(1)) >= STANDALONE_FROM

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        # "deny" takes the tools out of the request, and OpenCode Zen's free models then refuse it ("can only be
        # used from within OpenCode"). "ask" keeps the usual tools, and `opencode run` rejects every ask itself.
        zen = (model or self.config_model() or ZEN).startswith(ZEN)
        never = "ask" if zen else "deny"
        headers = {"Authorization": f"Bearer {token}"}
        if self.standalone(exe):
            # opencode 2: Mario Kart 64's helpers read the user's home and fetched a public decomp's code. Its own
            # format, since the old one loses `codemode`: off, the app's tools are the model's own, and `execute` (a
            # JavaScript runner whose fetch() reaches the internet, and which only "deny" removes) can go, with
            # subagents; nothing outside the helper's own folder either
            deny = {"execute", "subagent", "external_directory"}
            # #24: opencode 2 connects MCP servers asynchronously, so a helper's first model request could carry no
            # decompdecomp tools and the model guessed their names for ~18 s. This plugin waits for the server once,
            # before the first prompt; remove it once opencode waits itself (upstream #51812, fix #54942).
            (workdir / PLUGIN_DIR).mkdir(parents=True, exist_ok=True)
            (workdir / PLUGIN_DIR / "index.js").write_text(_wait_plugin(), encoding="utf-8")
            config = {"mcp": {"servers": {MCP_SERVER: {"type": "remote", "url": mcp_url, "headers": headers,
                                                       "codemode": False}}},
                      "plugins": [f"./{PLUGIN_DIR}"],
                      "permissions": [{"action": a, "resource": "*", "effect": "deny" if a in deny else never}
                                      for a in ("edit", "shell", "webfetch", "websearch", *sorted(deny))]}
        else:
            config = {"mcp": {MCP_SERVER: {"type": "remote", "url": mcp_url, "enabled": True, "headers": headers}},
                      "permission": {"edit": never, "bash": never, "webfetch": never}}
            if zen:  # a rejected ask ends the whole run unless this says otherwise: a free model reaching for `rg`
                # or `ls` on its third step ended every session of a PC game's helpers, 20 seconds in
                config["experimental"] = {"continue_loop_on_deny": True}
        (workdir / "opencode.json").write_text(json.dumps({"$schema": "https://opencode.ai/config.json", **config}),
                                               encoding="utf-8")
        # --print-logs: opencode's JSON error only says "Unexpected server error"; the log line says what failed.
        # (No --dir: it runs in workdir, and OpenCode before 1.4 stops at the flag. No --log-level either: before
        # 2.0 it took only ERROR, from 2.0 only error, and either stops the other at once.)
        cmd = [exe, "run", *(["--standalone"] if self.standalone(exe) else []), "--format", "json", "--print-logs"]
        # Concurrent helpers otherwise write OpenCode's one user database: a busy writer can end a prompt with
        # "database is locked" before it calls an app tool. Reuse one database per helper across its sessions.
        # Override only the database, not HOME/XDG_DATA_HOME: provider sign-in and MCP auth stay in their usual place.
        return cmd + (["-m", model] if model else []), {"OPENCODE_DB": str((workdir / "opencode.db").resolve())}

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t == "error":  # {"error": {"name", "data": {"message"}}}
            text = error_text(event.get("error") or event.get("message"))
            if not text.startswith(PLACEHOLDER):  # its log line on stderr says what failed
                out.append(("error", text[:SAID_CHARS]))
        elif t == "tool_use":
            part = event.get("part") if isinstance(event.get("part"), dict) else event
            name = part.get("tool_name") or part.get("tool") or part.get("name") or ""
            args = part.get("parameters") or (part.get("state") or {}).get("input") or part.get("input")
            out.append(("tool", f"{tool_name(name)}({args_brief(args)})"))
        elif t == "text" and isinstance(event.get("part"), dict):
            text = event["part"].get("text", "").strip()
            if text:
                out.append(("text", text))
        elif t in ("step_finish", "step-finish") and isinstance(event.get("part"), dict):  # one step of the model
            part = event["part"]
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            out.append(usage_item(usd=part.get("cost"), input=number(tokens, "input"),
                                  output=number(tokens, "output") + number(tokens, "reasoning"),
                                  cache_read=number(cache, "read"), cache_write=number(cache, "write"), turns=1))
        return out

    def clean_stderr(self, line: str) -> str:
        """--print-logs: keep the error itself, drop the stack noise."""
        line = line.strip()
        if "level=" not in line:
            return line
        m = re.search(r'\berror="((?:[^"\\]|\\.)*)"', line)
        return m.group(1).replace('\\"', '"') if m and re.search(r"level=error\b", line, re.I) else ""


OPENCODE = Opencode()
