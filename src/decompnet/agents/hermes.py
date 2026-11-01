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
session; left running it keeps calling the app with the session's expired token. `end_leftovers` ends every process
that carries the session's HERMES_HOME, before a session starts and after it ends.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, tool_name, usage_item

MCP_TIMEOUT = 900  # seconds a tool call may take: edit_context recompiles the unit's matched functions (300 by default)


def _quoted(value) -> str:
    """A YAML string: a JSON string is one, with every character escaped."""
    return json.dumps(str(value))


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
        cmd = [exe, "chat", "--query-file", "-", "--format", "stream-json"]
        if model:
            cmd += ["-m", model]
        if options.get("effort") in self.efforts:
            cmd += ["--reasoning", str(options["effort"])]
        return cmd, {"HERMES_HOME": str(home)}

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """Its text comes in pieces, gathered until a tool call or the result; a failed tool's output is an error."""
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t in ("tool_use", "result") and state.get("hermes_text", "").strip():
            out.append(("text", state.pop("hermes_text").strip()))
        if t == "text":
            state["hermes_text"] = state.get("hermes_text", "") + str(event.get("text") or "")
        elif t == "tool_use":
            out.append(("tool", f"{tool_name(str(event.get('name') or ''))}({args_brief(event.get('input'))})"))
        elif t == "tool_result" and event.get("is_error"):
            out.append(("error", str(event.get("output") or "the tool failed")[:SAID_CHARS]))
        elif t == "result":
            state.pop("hermes_text", None)
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
        if not home or not sys.platform.startswith("linux"):
            return  # (a daemonised gateway outliving its session was seen on Linux; elsewhere nothing is ended)
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


HERMES = Hermes()
