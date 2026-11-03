"""Codex CLI, OpenAI's agent CLI. Each session runs `codex exec --json`, read-only, with the app's MCP server added
and the user's own MCP servers switched off for the run; it prints one JSON event a line."""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, tool_name, usage_item

TOKEN_ENV = "DECOMP_TOKEN"  # the session's token reaches Codex through its environment, never its command line
TOOL_TIMEOUT = 180  # seconds a tool call may take (a fork's build)
COMMAND_SHOWN = 80  # characters of a shell command the feed shows


def codex_config() -> dict:
    """The user's Codex settings (~/.codex/config.toml, or $CODEX_HOME's)."""
    import tomllib

    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        return tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Codex(Agent):
    id = "codex"
    name = "Codex CLI"
    command = "codex"
    vendor = "OpenAI"
    install = "npm install -g @openai/codex"
    signin = "Run `codex login` once in a terminal."
    docs = "https://github.com/openai/codex"
    family = "gpt"
    takes_effort = True  # Codex's model_reasoning_effort
    efforts = ("minimal", "low", "medium", "high", "xhigh", "max")

    def fatal(self, error: str) -> bool:
        # A rejected model/effort setting cannot recover by retrying the same command.
        return ('model_not_found' in error.lower()
                or bool(re.search(r"model.{0,180}(?:not supported|does not exist)", error, re.I))
                or 'unsupported_value' in error and 'reasoning.effort' in error
                or bool(re.search(r"(?:unsupported value|not supported).*(?:reasoning|effort)|(?:reasoning|effort).*not supported", error, re.I)))

    def estimate_cost(self, model: str, usage: dict) -> float | None:
        # Standard API USD / million tokens, checked 2026-10-26:
        # https://developers.openai.com/api/docs/pricing
        # An estimate of equivalent API usage, never subscription billing. Cache writes,
        # long-context premiums, fast mode and regional pricing are not reported by this CLI.
        rates = {
            "gpt-6-astra": (10, 1, 50), "gpt-6.1-sol": (2, .1, 10),
            "gpt-6.1-terra": (1, .05, 5), "gpt-6-luna": (.1, .01, .5),
            "gpt-6-sol": (2, .2, 10), "gpt-5.6-sol": (4, .4, 20),
            "gpt-5.6-terra": (2, .2, 12), "gpt-5.6-luna": (.2, .02, 1.2),
            "gpt-5.3-codex": (1.75, .175, 14),
        }
        rate = rates.get(model.partition("#")[0])
        counts = [usage.get(k, 0) or 0 for k in ("input", "cache_read", "output")]
        if rate is None or usage.get("cache_write") or sum(counts) != (usage.get("tokens") or 0) or not sum(counts):
            return None
        return sum(n * price for n, price in zip(counts, rate)) / 1_000_000

    def config_model(self) -> str | None:
        return codex_config().get("model") or None

    def investigation_command(self, exe: str, prompt: str) -> list[str]:
        return [exe, prompt]

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        # The helper's own server; the user's MCP servers are switched off for the run (`-c` settings merge into the
        # user's config), as --strict-mcp-config does for Claude Code
        cmd = [exe, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "-C", str(workdir), "-s", "read-only"]
        for name in (codex_config().get("mcp_servers") or {}):
            if re.fullmatch(r"[A-Za-z0-9_-]+", name):
                cmd += ["-c", f"mcp_servers.{name}.enabled=false"]
        cmd += ["-c", f'mcp_servers.decomp_agent={{url="{mcp_url}", bearer_token_env_var="{TOKEN_ENV}", '
                      f'default_tools_approval_mode="approve", tool_timeout_sec={TOOL_TIMEOUT}}}',
                "-c", 'approval_policy="never"',
                "-c", 'web_search="disabled"']  # the read-only sandbox keeps its shell offline; this, its own search
        model, _, effort = (model or "").partition("#")
        if effort in self.efforts:  # (one of its own words only: it goes into a TOML value)
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        if model:
            cmd += ["-m", model]
        cmd.append("-")  # the prompt comes from stdin
        return cmd, {TOKEN_ENV: token}

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        t = event.get("type")
        out: list[tuple[str, str] | None] = []
        if t in ("item.started", "item.completed") and isinstance(event.get("item"), dict):
            item = event["item"]
            it = item.get("type") or item.get("item_type")
            if it == "agent_message" and t == "item.completed":
                out.append(("text", str(item.get("text", "")).strip()))
            elif it == "reasoning" and t == "item.completed" and item.get("text"):
                out.append(("think", str(item["text"]).strip()))
            elif it == "mcp_tool_call" and t == "item.started":
                out.append(("tool", f"{tool_name(item.get('tool', ''))}({args_brief(item.get('arguments'))})"))
            elif it == "command_execution" and t == "item.started":
                out.append(("tool", f"shell: {str(item.get('command', ''))[:COMMAND_SHOWN]}"))
            elif it == "error":
                out.append(("error", str(item.get("message", ""))[:SAID_CHARS]))
        elif t == "turn.completed":  # its input tokens include the cached ones
            out.append(("info", "turn finished"))
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            cached = number(usage, "cached_input_tokens")
            out.append(usage_item(input=max(0, number(usage, "input_tokens") - cached), cache_read=cached,
                                  output=number(usage, "output_tokens"), turns=1))
        elif t in ("error", "turn.failed"):
            out.append(("error", error_text(event.get("error") or event.get("message"))[:SAID_CHARS]))
        return out


CODEX = Codex()
