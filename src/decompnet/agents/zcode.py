"""ZCode CLI (`zcode`, the npm `zcode-app-cli`), Z.ai's agent CLI. Each session runs `zcode --output-format
stream-json` in a private folder, with the app's MCP server as its only one and every built-in tool removed.

zcode has no stdin prompt (`-p/--prompt` is a text argument), so the prompt goes in a Markdown file the session
attaches with `--attach`: zcode reads that file into the model's context as text (not a path for a Read tool),
which keeps the prompt off the command line and out of the OS argument limit. The fixed `-p` sentence is ours, so
zcode cannot read the prompt as a slash command either. `--disallowed-tools` removes everything but the app's MCP
tools; the settings file replaces the user's MCP servers and plugins for the session.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, tool_name, usage_item

MCP_SERVER = "decomp"  # the app's MCP server, and the prefix of its tools (mcp__decomp__claim_function)
PROMPT_FILE = "prompt.md"  # a text-like extension: zcode inlines it instead of handing the model a path to read
PROMPT_TEXT = "Follow the instructions in the attached file."
SETTINGS_FILE = "setting.json"
# zcode's attachment limits (vendor/zcode.cjs: m3 = 256 KiB, CM = 25,000 estimated tokens). Past either,
# it stops inlining: a path placeholder or a partial view, which our disallowed Read tool cannot recover. Refuse
# the session up front instead of letting a helper work without its instructions.
ATTACHMENT_BYTES = 256 * 1024
ATTACHMENT_TOKENS = 25_000
TOKEN_CHARS = 3  # zcode's estimate (qf): CJK counts 2, every other character 1, divided by 3, rounded up
_CJK = re.compile(r"[\u4e00-\u9fff]")


def estimated_tokens(text: str) -> int:
    """zcode's own estimate (vendor/zcode.cjs, `qf`), in UTF-16 code units as JavaScript counts them."""
    cjk = len(_CJK.findall(text))
    units = len(text.encode("utf-16-le")) // 2
    return (cjk * 2 + (units - cjk) + TOKEN_CHARS - 1) // TOKEN_CHARS


# zcode's own tools are not the app's: a helper works through decompdecomp's MCP tools only. The list is zcode's
# built-ins (its runtime's ZCODE_KNOWN_TOOL_NAMES) plus the Node REPL tools bundled with it.
DISALLOWED = " ".join((
    "Read", "Write", "Edit", "ApplyPatch", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "web_search",
    "TodoRead", "TodoWrite", "GoalRead", "ReadSessionContext", "AskUserQuestion", "SendMessage",
    "RespondToCoordinator", "TaskOutput", "TaskStop", "js", "js_reset", "js_add_node_module_dir",
    "mcp__node_repl__js", "mcp__node_repl__js_reset", "mcp__node_repl__js_add_node_module_dir",
    "Agent", "Task", "Skill", "CreateWorkflow", "AmendWorkflow", "FillWorkflowHole", "submit_result",
))
# zcode has no `models` command: these are the Z.ai plans' models, as agent-watch lists them. Provider ids are
# account/plan-specific, so the launcher also takes a typed name.
ZCODE_MODELS = (("account:zai-start-plan/GLM-5.3", "GLM-5.3", "Start Plan"),
                ("account:zai-start-plan/GLM-5.3-Flash", "GLM-5.3-Flash", "Start Plan"),
                ("account:zai-individual-coding-plan/GLM-5.3", "GLM-5.3", "Coding Plan"),
                ("account:zai-individual-coding-plan/GLM-5.3-Flash", "GLM-5.3-Flash", "Coding Plan"))
# zcode's permanent model/effort errors: trying again with the same helper can't help.
FATAL = ("Unsupported reasoning effort", "does not support reasoning effort", "Invalid prompt model selection",
         "Cannot apply --effort without a current model")


class Zcode(Agent):
    id = "zcode"
    name = "ZCode CLI"
    command = "zcode"
    vendor = "Z.ai"
    install = "npm install -g zcode-app-cli"
    signin = "Run `zcode login` once and sign in through the browser."
    docs = "https://github.com/kingsword09/zcode-cli"
    family = "glm"
    takes_effort = True
    efforts = ("low", "high", "max")  # Z.ai's GLM models' levels (zcode's --effort also takes auto)

    def models(self, path: str | None, run: bool = True) -> list[dict]:
        """The Z.ai plans' models; a user on another plan types its provider/model."""
        return [{"id": i, "name": n, "note": note} for i, n, note in ZCODE_MODELS]

    def session_command(self, exe: str, workdir: Path, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        settings = {
            "features": {"mcp": True, "subagent": False, "skill": False, "compact": False, "rewind": False},
            "mcp": {"servers": {MCP_SERVER: {"type": "http", "url": mcp_url,
                                             "headers": {"Authorization": f"Bearer {token}"}}}},
            "plugins": {"enabled": False},
            "skills": {"enabled": False},
            "hooks": {"enabled": False},
        }
        (workdir / SETTINGS_FILE).write_text(json.dumps(settings), encoding="utf-8")
        model, _, effort = (model or "").partition("#")
        cmd = [exe, "--output-format", "stream-json", "--mode", "yolo", "--disallowed-tools", DISALLOWED]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        cmd += ["--attach", str(workdir / PROMPT_FILE), "-p", PROMPT_TEXT]
        return cmd, {"ZCODE_CLI_SETTINGS_FILE": str(workdir / SETTINGS_FILE)}

    def prompt_file(self, workdir: Path, prompt: str) -> Path | None:
        """zcode reads the prompt from the file it attaches; `-p` only says to follow it. A prompt past zcode's
        attachment limits is refused here: past them zcode would replace the content with a path placeholder or a
        partial view, and with Read disallowed a helper would work without its instructions."""
        if len(prompt.encode("utf-8")) > ATTACHMENT_BYTES or estimated_tokens(prompt) > ATTACHMENT_TOKENS:
            raise ValueError("this project's prompt is too long for zcode, which only reads attachments up to "
                             "about 25,000 tokens (256 KiB): shorten the project's instructions, or use another "
                             "agent")
        path = workdir / PROMPT_FILE
        path.write_bytes(prompt.encode("utf-8"))  # exact bytes: write_text would translate newlines on Windows
        return path

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """model.streaming (text and reasoning in deltas, then a tool call), tool failures, turn.failed, and the
        final result, whose usage counts the cached input in inputTokens."""
        out: list[tuple[str, str] | None] = []
        if event.get("sessionId"):
            state["zcode_session"] = event["sessionId"]
        t = event.get("type")
        if t == "model.streaming" and isinstance(event.get("payload"), dict):
            p = event["payload"]
            kind = p.get("kind")
            if kind == "reasoning_delta":
                state["zcode_think"] = state.get("zcode_think", "") + str(p.get("delta") or "")
            elif kind == "reasoning_end":
                text = state.pop("zcode_think", "").strip()
                if text:
                    out.append(("think", text))
            elif kind == "text_delta":
                state["zcode_text"] = state.get("zcode_text", "") + str(p.get("delta") or "")
            elif kind in ("text_end", "finish"):
                text = state.pop("zcode_text", "").strip()
                if text:
                    out.append(("text", text))
            elif kind == "tool_call":
                out.append(("tool", f"{tool_name(p.get('toolName', ''))}({args_brief(p.get('input'))})"))
        elif t == "tool.updated" and isinstance(event.get("payload"), dict):
            p = event["payload"]
            result = p.get("result") if isinstance(p.get("result"), dict) else {}
            if p.get("kind") == "result" and not result.get("success", True):
                out.append(("error", str(result.get("error") or result.get("content") or "tool failed")[:SAID_CHARS]))
        elif t == "turn.failed":
            p = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            out.append(("error", error_text(p.get("error") or p.get("message") or p)[:SAID_CHARS]))
        elif t == "result":  # the end of the session; provider input counts the cached tokens in inputTokens
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            cached, written = number(usage, "cacheReadTokens"), number(usage, "cacheWriteTokens")
            out.append(usage_item(input=max(0, number(usage, "inputTokens") - cached - written),
                                  output=number(usage, "outputTokens"), cache_read=cached, cache_write=written,
                                  turns=number(usage, "modelRequestCount")))
        return out

    def clean_stderr(self, line: str) -> str:
        """zcode writes `Error: ...` on stderr; keep the message, drop the prefix."""
        line = line.strip()
        return line[len("Error: "):] if line.startswith("Error: ") else line

    def fatal(self, error: str) -> bool:
        return error.startswith(FATAL)


ZCODE = Zcode()
