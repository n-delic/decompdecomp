"""Hermes Agent (Nous Research), run against an API endpoint the user saved in Agents & models: a local llama.cpp or
sglang server, GLM, DeepSeek, any OpenAI-compatible API. Hermes drives the endpoint itself (its "custom" provider with
the endpoint's address), so a model the built-in API helper struggles to drive gets Hermes' own agent loop, with the
endpoint's model, address and key, as the API helper's do (`uses_endpoints`).

A session is one `hermes chat --query-file - --format stream-json` in the helper's working folder: the prompt comes in
on stdin, and Hermes prints one JSON event a line (`system` init, `text` in pieces, `tool_use`, `tool_result`, and a
`result` with the session's tokens). The app writes Hermes a home of its own there (HERMES_HOME), whose config.yaml
names the endpoint and only the app's MCP server, with the session's token as a bearer header (never on the command
line), and switches Hermes' own toolsets off (`platform_toolsets.cli: []`): no terminal, file or web tools, only
decompdecomp's. Hermes keeps its dependencies per home: a helper's first session prepares them in its folder (a few
minutes, several hundred MB), and its later sessions reuse them.

A Hermes run starts a gateway process that daemonises, so it leaves the session's process group and outlives the
session; left running it keeps calling the app with the session's expired token. `end_leftovers` stops it before a
session starts and after it ends: it runs Hermes' own `gateway stop` for the session's HERMES_HOME (its cross-platform
teardown, reaching the detached gateway on macOS and Windows too), then on Linux also SIGTERMs any process still
carrying that HERMES_HOME as a fallback.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import urllib.parse
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, tool_name, usage_item

MCP_TIMEOUT = 900  # seconds a tool call may take: edit_context recompiles the unit's matched functions (300 by default)
STOP_TIMEOUT = 30  # seconds `hermes gateway stop` may take before end_leftovers falls back to the process sweep


def _quoted(value) -> str:
    """A YAML string: a JSON string is one, with every character escaped."""
    return json.dumps(str(value))


def _is_openrouter(base_url: str) -> bool:
    """Whether the endpoint is OpenRouter. Hermes picks the key environment variable by the endpoint's host: an
    openrouter.ai URL reads OPENROUTER_API_KEY, any other (local or custom) endpoint reads OPENAI_API_KEY and never
    the OpenRouter key. So the adapter must put the saved key in the variable the resolved host reads."""
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


class Hermes(Agent):
    id = "hermes"
    name = "Hermes Agent"
    command = "hermes"
    vendor = "Nous Research (runs a saved API endpoint)"
    install = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    windows_install = "irm https://hermes-agent.nousresearch.com/install.ps1 | iex"
    signin = "Nothing to sign in to: it runs the API endpoint you pick for it in the launcher."
    docs = "https://hermes-agent.nousresearch.com/docs"
    uses_endpoints = True
    takes_effort = True

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str,
                        options: dict | None = None) -> tuple[list[str], dict]:
        """`options`: the helper's endpoint (base_url, api_key, effort), as the API helper gets them."""
        options = options or {}
        model = model or str(options.get("model") or "")
        home = workdir / ".hermes"
        home.mkdir(exist_ok=True)
        endpoint = ""
        if options.get("base_url"):
            endpoint = (f"  base_url: {_quoted(str(options['base_url']).rstrip('/'))}\n"
                        f"  api_key: {_quoted(options.get('api_key') or 'none')}\n")
        (home / "config.yaml").write_text(
            "model:\n"
            f"  default: {_quoted(model)}\n"
            '  provider: "custom"\n'
            f"{endpoint}"
            "platform_toolsets:\n"
            "  cli: []\n"  # only decompdecomp's tools: no terminal, file or web
            "mcp_servers:\n"
            "  decomp:\n"
            f"    url: {_quoted(mcp_url)}\n"
            f"    timeout: {MCP_TIMEOUT}\n"
            "    headers:\n"
            f"      Authorization: {_quoted('Bearer ' + token)}\n", encoding="utf-8")
        try:  # the file holds the endpoint key and the bearer token: owner-only, like settings.json
            os.chmod(home, 0o700)
            os.chmod(home / "config.yaml", 0o600)
        except OSError:
            pass
        cmd = [exe, "chat", "--query-file", "-", "--format", "stream-json"]
        if model:
            cmd += ["-m", model]
        if options.get("effort") in self.efforts:
            cmd += ["--reasoning", str(options["effort"])]
        env = {"HERMES_HOME": str(home)}
        base = str(options.get("base_url") or "").rstrip("/")
        if base:
            # Bind the chosen endpoint through the environment Hermes actually reads, so nothing the app inherited
            # can redirect the helper. Hermes resolves a custom endpoint's base_url as explicit > CUSTOM_BASE_URL >
            # config, so an inherited CUSTOM_BASE_URL would otherwise beat config.yaml; pin it to the selection. It
            # picks the key by the resolved host (OpenRouter -> OPENROUTER_API_KEY, else OPENAI_API_KEY), not from
            # model.api_key, so put the key in the host's variable. Blank the rivals (the other base_url and key
            # variables) so a stray inherited value can't win. The key stays out of argv — only here and in config.
            key = str(options.get("api_key") or "")
            env["CUSTOM_BASE_URL"] = base
            env["OPENROUTER_BASE_URL"] = ""
            env["OPENAI_BASE_URL"] = ""
            if _is_openrouter(base):
                env["OPENROUTER_API_KEY"], env["OPENAI_API_KEY"] = key, ""
            else:
                env["OPENAI_API_KEY"], env["OPENROUTER_API_KEY"] = key, ""
        return cmd, env

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """Its text comes in pieces, gathered until a tool call or the result; a failed tool's output is an error."""
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t in ("tool_use", "result") and state.get("hermes_text", "").strip():
            out.append(("text", state.pop("hermes_text").strip()))
            state["hermes_said"] = True
        if t == "text":
            state["hermes_text"] = state.get("hermes_text", "") + str(event.get("text") or "")
        elif t == "tool_use":
            out.append(("tool", f"{tool_name(str(event.get('name') or ''))}({args_brief(event.get('input'))})"))
        elif t == "tool_result" and event.get("is_error"):
            out.append(("error", str(event.get("output") or "the tool failed")[:SAID_CHARS]))
        elif t == "result":
            state.pop("hermes_text", None)
            reply = str(event.get("text") or "").strip()
            if reply and not state.get("hermes_said"):
                out.append(("text", reply))  # a replay_gap streams no text: the whole reply is in the result event
            if event.get("error") or event.get("exit_code"):
                out.append(("error", error_text(event.get("error") or f"exited with code {event.get('exit_code')}")
                            [:SAID_CHARS]))
            tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
            split = number(tokens, "input") or number(tokens, "output")
            out.append(usage_item(input=number(tokens, "input"), output=number(tokens, "output"),
                                  cache_read=number(tokens, "cache_read"), cache_write=number(tokens, "cache_write"),
                                  tokens=0 if split else number(tokens, "total")))
        return out

    def end_leftovers(self, env: dict) -> None:
        home = env.get("HERMES_HOME")
        if not home:
            return
        # Ask Hermes to stop the gateway for this home: `gateway stop` is its own cross-platform teardown (it
        # parks a profile's gateway, macOS launchd and Windows service included), so it reaches the detached
        # gateway where a POSIX-only process sweep can't. Best-effort: the gateway may already be gone.
        exe = self.resolve_exe()
        if exe:
            try:
                subprocess.run([exe, "gateway", "stop"], env={**os.environ, **env}, timeout=STOP_TIMEOUT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        # Belt and braces on Linux: SIGTERM any process still carrying this HERMES_HOME (a gateway that
        # daemonised out of the session's process group that `gateway stop` didn't reach).
        if not sys.platform.startswith("linux"):
            return
        needle = f"HERMES_HOME={home}\0".encode()
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                with open(f"/proc/{entry.name}/environ", "rb") as fh:
                    if needle not in fh.read() + b"\0":
                        continue
                os.kill(int(entry.name), signal.SIGTERM)
            except OSError:
                continue


    def search_dirs(self) -> list[Path]:
        """Where Hermes' installer stages the program off PATH: the Windows install.ps1 puts it in $HermesHome\\bin,
        default %LOCALAPPDATA%\\hermes\\bin. On other systems that path doesn't exist, so the entry is harmless there
        (the unix install.sh's ~/.local/bin, off PATH for a Finder-launched macOS app, is searched too)."""
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        dirs = [Path(local) / "hermes" / "bin", Path.home() / ".local" / "bin"]
        home = os.environ.get("HERMES_HOME")
        if home:
            dirs.insert(0, Path(home) / "bin")
        return dirs


HERMES = Hermes()
