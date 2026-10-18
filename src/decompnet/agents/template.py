"""A new agent, to copy: `cp agents/template.py agents/<id>.py`, then fill in what's marked FILL IN. It isn't
registered, so the app never sees it until it's in the list at the end of agents/__init__.py.

Write in this docstring what the agent is (who makes it, how a session runs, what it prints), as agents/claude.py
does: the next person who works on it starts here.

The parts below are the connector's (agents/__init__.py, class Agent, says what each one is for). A part this agent
doesn't need can go: the default does nothing, or says the agent can't do it. An agent that is no program but a loop
in the app (like agents/api.py) leaves `command` empty and fills in `run` instead of the session's parts.
CONTRIBUTING.md, "Adding an agent", has the whole checklist.
"""

from __future__ import annotations

from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, read_json, tool_name, usage_item


class Example(Agent):
    id = "example"  # FILL IN: what helpers' records store (keep it once helpers exist; add old ids to aliases)
    name = "Example CLI"  # what the app shows
    command = "example"  # its program on PATH
    vendor = "Example Inc."  # who makes it, or whose models it runs
    install = "curl -fsSL https://example.com/install.sh | bash"  # FILL IN: what "Install it for me" runs
    signin = "Run `example login` once in a terminal."  # FILL IN: how the user signs in
    docs = "https://example.com/docs"
    family = ""  # who made its models, when it runs only one maker's ("gpt"); "": the model in a helper's name says
    models_args = ()  # ("models",) when `example models` lists what the user's sign-in offers (then read_models)

    def config_model(self) -> str | None:
        """FILL IN: the default model in the agent's own settings file, if it has one."""
        return read_json(Path.home() / ".example" / "settings.json").get("model") or None

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        """FILL IN: run one session headless, reading the prompt on stdin and printing JSON, one event a line. Write
        an MCP config in `workdir` that names only decompdecomp's server (`mcp_url`, with `token` as a bearer token),
        switch off the user's own MCP servers for the run, and allow only decompdecomp's tools: no shell, no file
        edits, no web."""
        cmd = [exe, "run", "--json", "--mcp-url", mcp_url]
        return cmd + (["--model", model] if model else []), {"EXAMPLE_TOKEN": token}

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """FILL IN: the feed items in one JSON event it prints. The tool calls matter most: a session with none
        counts as failed. The usage items let the Agents view compare models on cost per match."""
        t = event.get("type")
        if t == "message":
            return [("text", str(event.get("text", "")).strip())]
        if t == "tool_call":
            return [("tool", f"{tool_name(event.get('name', ''))}({args_brief(event.get('arguments'))})")]
        if t == "error":
            return [("error", error_text(event.get("error"))[:SAID_CHARS])]
        if t == "done":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            return [usage_item(input=number(usage, "input_tokens"), output=number(usage, "output_tokens"), turns=1)]
        return []

    # Also, as this agent needs them (each is explained on Agent): read_models, version, stdin, clean_stderr,
    # fatal, label; an agent built into the app: run.


EXAMPLE = Example()
