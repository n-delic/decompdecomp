"""Antigravity CLI (`agy`), Google's agent CLI, which replaced Gemini CLI. Each session runs `agy` with stream-json in
and out, with the app's MCP server in the workspace's own MCP config. It reaches MCP tools through one generic
dispatcher (`call_mcp_tool`), and streams its text in pieces, gathered per step."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import SAID_CHARS, STDERR_CHARS, Agent, args_brief, error_text, number, read_json, tool_name, usage_item

# decompdecomp's tools are loaded up front in Antigravity (tool_meta, tools.ALWAYS_LOAD) and need no permission.
# Should a helper reach one through agy's generic MCP dispatcher instead, a headless run can't ask, and agy quietly
# refuses it: this rule in the user's agy settings allows decompdecomp's tools (and nothing else).
AGY_ALLOW = "mcp(decomp/*)"
# how long one Antigravity session may run: a session works through several functions (agy's default is 5 minutes)
AGY_PRINT_TIMEOUT = "4h"
REFUSED = "Antigravity refused decompdecomp's tools"  # every session after it would be refused the same way
ERROR_LINE = "AGY_ERROR:"  # how agy starts a structured error on stderr
TEXT_STEPS = ("agent_response", "thinking", "planner_response")


def agy_settings() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


class Antigravity(Agent):
    id = "antigravity"
    name = "Antigravity CLI"
    command = "agy"
    vendor = "Google"
    install = "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    signin = "Open a terminal and run `agy` once to sign in."
    docs = "https://antigravity.google/docs/cli"
    # Google retired Gemini CLI on 2026-06-18; Antigravity CLI replaced it. Helpers and launcher choices saved by
    # older versions as "gemini" carry on as Antigravity.
    aliases = ("gemini", "agy")
    family = "gemini"
    models_args = ("models",)
    # it otherwise finds MCP tools on demand (an extra step each, through its generic dispatcher): decompdecomp's
    # tools are all there is to the job, so they are loaded up front
    tool_meta = {"antigravity/alwaysLoad": True}

    def read_models(self, lines: list[str]) -> list[dict]:
        """`agy models`: "<slug>\\t<name>" per model."""
        rows = [r.split("\t", 1) for r in lines if re.fullmatch(r"[\w.:-]+\t.+", r)]
        return [{"id": slug, "name": name.strip(), "note": slug.split("-", 1)[0]} for slug, name in rows]

    def config_model(self) -> str | None:
        return read_json(agy_settings()).get("model") or None

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        # The workspace's own MCP config; tool permission comes from the user's rule for decomp's tools (AGY_ALLOW).
        # Antigravity CLI 1.1.3 ignored it and loaded only the user's settings; 1.4.2 reads it (October 2026: its
        # helpers compiled and matched through it on the owner's machine). An older agy gets no tools: its helpers
        # then pause after five failures, saying the agent never used the decomp tools.
        (workdir / ".agents").mkdir(exist_ok=True)
        (workdir / ".agents" / "mcp_config.json").write_text(json.dumps({"mcpServers": {"decomp": {
            "serverUrl": mcp_url, "headers": {"Authorization": f"Bearer {token}"}}}}), encoding="utf-8")
        cmd = [exe, "--output-format", "stream-json", "--input-format", "stream-json", "--print-timeout",
               AGY_PRINT_TIMEOUT]
        return cmd + (["--model", model] if model else []), {}

    def stdin(self, prompt: str) -> str:
        """One stream-json user message."""
        return json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """step_update events (text arrives in deltas, gathered per step) and a result. Its result counts one turn for
        the whole session (the one prompt), so turns are the model's steps, counted here: every session read "1
        turns"."""
        out: list[tuple[str, str] | None] = []
        ev = event.get("event")
        if ev == "step_update" and isinstance(event.get("step_update"), dict):
            st = event["step_update"]
            step, kind = st.get("step_index"), st.get("step_type")
            if kind in TEXT_STEPS:
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
                out.append(("tool", f"{tool_name(name)}({args_brief(params)})"))
        elif ev == "result" and isinstance(event.get("result"), dict):
            res = event["result"]
            usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
            turns = max(number(res, "num_turns"), state.get("agy_steps", 0))
            split = number(usage, "input_tokens") or number(usage, "output_tokens")
            out.append(usage_item(input=number(usage, "input_tokens"), output=number(usage, "output_tokens"),
                                  tokens=0 if split else number(usage, "total_tokens"), turns=turns))
            denied = [d for d in res.get("denied_actions") or [] if isinstance(d, dict)]
            if any(d.get("action") == "mcp" for d in denied):
                out.append(("error", f"{REFUSED}: a background run can't ask you for permission. Allow them once: add "
                                     f"\"{AGY_ALLOW}\" to \"permissions\": {{\"allow\": [...]}} in {agy_settings()} "
                                     "(only decompdecomp's tools; shell, files and the web keep asking)."))
            if res.get("status") not in (None, "SUCCESS"):
                out.append(("error", str(res.get("error") or f"session ended: {res.get('status')}")[:SAID_CHARS]))
            else:
                out.append(("info", "session finished" + (f" ({turns} turns)" if turns else "")))
        elif ev == "error":
            out.append(("error", error_text(event.get("error") or event.get("message"))[:SAID_CHARS]))
        return out

    def clean_stderr(self, line: str) -> str:
        line = line.strip()
        if line.startswith(ERROR_LINE):  # a structured error line
            try:
                return error_text(json.loads(line.split(":", 1)[1]))[:STDERR_CHARS]
            except ValueError:
                return line
        return line

    def fatal(self, error: str) -> bool:
        return error.startswith(REFUSED)


ANTIGRAVITY = Antigravity()
