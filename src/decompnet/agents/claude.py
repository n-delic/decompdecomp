"""Claude Code, Anthropic's agent CLI. Each session runs `claude -p` with the app's MCP server as its only one
(--strict-mcp-config) and only decompdecomp's tools allowed; it prints stream-json, one event a line."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, number, read_json, tool_name, usage_item

# Claude Code's aliases always point at Anthropic's latest model of each kind; the full names below them keep one
# version, newest first (`claude --model` takes either). A model missing here: "Another model…" takes any name.
CLAUDE_MODELS = [("fable", "Fable", "Anthropic's most capable model"), ("opus", "Opus", "very capable"),
                 ("sonnet", "Sonnet", "fast and capable, cheaper"), ("haiku", "Haiku", "fastest, cheapest")]
CLAUDE_VERSIONS = ["claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                   "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
                   "claude-haiku-5-5", "claude-haiku-4-5"]
PINNED_NOTE = "this version only"
# what a session may not do: decompdecomp's tools are all it needs, and it works on the user's computer. Read, Grep
# and Glob too: a session starts in its own folder, not the project's, so they found nothing there (read_file and
# search_code read the project); on one night 340 such calls failed.
DISALLOWED = "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Read,Grep,Glob"
# Its prompt cache: headless Claude Code writes it for an hour, at twice the input price against the five-minute
# cache's 1.25. A session's turns come seconds apart (one night: none more than three minutes apart), so five minutes
# is enough. The user's own setting of it wins.
CACHE_TTL_ENV, CACHE_TTL = "CLAUDE_CODE_PROMPT_CACHE_TTL", "5m"
LOADS_TOOLS = "ToolSearch"  # Claude Code loading tools it was told about: not worth a line in the feed


def version_name(model_id: str) -> str:
    """claude-opus-4-6 -> Opus 4.6."""
    _, family, *version = model_id.split("-")
    return f"{family.capitalize()} {'.'.join(version)}"


class ClaudeCode(Agent):
    id = "claude"
    name = "Claude Code"
    command = "claude"
    vendor = "Anthropic"
    install = "curl -fsSL https://claude.ai/install.sh | bash"
    windows_install = "irm https://claude.ai/install.ps1 | iex"
    signin = "Open a terminal, run `claude` and sign in with /login."
    docs = "https://docs.claude.com/en/docs/claude-code"
    family = "claude"
    uses_accounts = True
    takes_effort = True  # `claude --effort`: the launcher sends it with the model's name ("opus#high")
    efforts = ("low", "medium", "high", "xhigh", "max")

    def models(self, path: str | None, run: bool = True) -> list[dict]:
        """Its aliases (always the newest of each kind), then each version on its own."""
        return ([{"id": i, "name": n, "note": note} for i, n, note in CLAUDE_MODELS]
                + [{"id": i, "name": version_name(i), "note": PINNED_NOTE} for i in CLAUDE_VERSIONS])

    def config_model(self) -> str | None:
        folder = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
        return read_json(Path(folder) / "settings.json").get("model") or None

    def default_model(self, options: dict) -> str | None:
        if not options.get("account_dir"):
            return self.config_model()
        return read_json(Path(options["account_dir"]) / "settings.json").get("model") or None

    def session_environment(self, options: dict, env: dict) -> dict:
        """#45: bind each helper to its named subscription without changing the app or other helpers."""
        if not options.get("account_id"):
            return env  # the existing default sign-in keeps the user's environment
        # #45: inherited credentials outrank a directory's subscription login in headless Claude Code. Refuse
        # them rather than silently charging a different account. Settings within the selected directory can
        # also choose another credential, so these profiles must be signed in as subscriptions.
        settings = read_json(Path(options["account_dir"]) / "settings.json")
        configured_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        conflicts = [key for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                                    "ANTHROPIC_PROFILE") if env.get(key) or configured_env.get(key)]
        if settings.get("apiKeyHelper"):
            conflicts.append("apiKeyHelper")
        if conflicts:
            raise ValueError("The selected account is overridden by " + ", ".join(conflicts)
                             + ". Remove these overrides from the environment or account settings to use its subscription login.")
        return env | {"CLAUDE_CONFIG_DIR": options["account_dir"]}

    def account_signin(self, config_dir: str) -> str:
        if sys.platform == "win32":
            return "$env:CLAUDE_CONFIG_DIR='" + config_dir.replace("'", "''") + "'; claude"
        return "CLAUDE_CONFIG_DIR=" + shlex.quote(config_dir) + " claude"

    def signin_for(self, options: dict) -> str:
        if options.get("account_id"):
            return "Open a terminal and sign in with /login using: " + self.account_signin(options["account_dir"])
        return self.signin

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        cfg = workdir / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"decomp": {"type": "http", "url": mcp_url,
                                                              "headers": {"Authorization": f"Bearer {token}"}}}}),
                       encoding="utf-8")
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg),
               "--strict-mcp-config", "--allowedTools", "mcp__decomp", "--disallowedTools", DISALLOWED]
        model, _, effort = (model or "").partition("#")
        env = {} if CACHE_TTL_ENV in os.environ else {CACHE_TTL_ENV: CACHE_TTL}
        return cmd + (["--model", model] if model else []) + (["--effort", effort] if effort else []), env

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t == "assistant" and isinstance(event.get("message"), dict):
            for c in event["message"].get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append(("text", c["text"].strip()))
                elif c.get("type") == "thinking" and c.get("thinking", "").strip():
                    out.append(("think", c["thinking"].strip()))
                elif c.get("type") == "tool_use" and c.get("name") != LOADS_TOOLS:
                    out.append(("tool", f"{tool_name(c.get('name', ''))}({args_brief(c.get('input'))})"))
        elif t == "result":  # the end of the session
            cost, turns = event.get("total_cost_usd"), event.get("num_turns")
            if event.get("is_error") or event.get("status") == "error":
                out.append(("error", str(event.get("result") or event.get("error") or "session failed")[:SAID_CHARS]))
            else:
                bits = [f"{turns} turns" if turns else "", f"${cost:.2f}" if isinstance(cost, (int, float)) else ""]
                out.append(("info", "session finished" + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "")))
            # #45: a session can spend tokens before hitting its account limit; errors still report that usage.
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
            out.append(usage_item(usd=cost, input=number(usage, "input_tokens"), output=number(usage, "output_tokens"),
                                  cache_read=number(usage, "cache_read_input_tokens"),
                                  cache_write=number(usage, "cache_creation_input_tokens"),
                                  tokens=0 if usage else number(stats, "total_tokens"), turns=turns))
        return out


CLAUDE = ClaudeCode()
