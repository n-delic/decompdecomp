"""Helper agents: each CLI's output read into the feed (Antigravity CLI, opencode errors), prompts sent on stdin,
helpers as records that survive a restart, starting again by themselves (and waiting out usage limits), quitting
ending everything they started, what each session spent, and the compact context agents get (assembly, diffs)."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from decompnet import agents as kinds
from decompnet import network, scoring
from decompnet.agents import antigravity, ohmy, opencode, zcode
from decompnet.app import agents, session_followups
from decompnet.app.engine import RomNetwork


def test_antigravity_stream_json_is_read():
    state: dict = {}
    lines = [
        {"event": "init", "conversation_id": "c", "init": {"cwd": "/w", "tools": ["call_mcp_tool"]}},
        {"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "agent_response",
                                                 "text_delta": "Claiming "}},
        {"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "agent_response",
                                                 "text_delta": "the next one."}},
        {"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "agent_response",
                                                 "text_delta": "\n"}},
        {"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool",
                                                 "tool_name": "call_mcp_tool", "tool_info": {"name": "call_mcp_tool",
                                                 "parameters": {"ServerName": "decomp", "ToolName": "claim_function",
                                                                "Arguments": {"strategy": "easiest"}}}}},
        {"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool",
                                                 "tool_name": "call_mcp_tool", "tool_info": {"name": "call_mcp_tool",
                                                 "parameters": {"ServerName": "decomp", "ToolName": "claim_function",
                                                                "Arguments": {"strategy": "easiest"}}}}},
        {"event": "result", "result": {"status": "SUCCESS", "num_turns": 1, "usage": {"total_tokens": 1200}}},
    ]
    out = [item for line in lines for item in kinds.get("antigravity").parse_line(json.dumps(line), state)]
    # its result counts the one prompt as one turn ("1 turns" for every session): the model's steps are its turns
    assert out == [("text", "Claiming the next one."), ("tool", "claim_function(easiest)"),
                   ("usage", json.dumps({"tokens": 1200, "turns": 2})), ("info", "session finished (2 turns)")]


def test_antigravity_refusing_decomps_tools_says_how_to_allow_them():
    line = json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "",
                                                     "denied_actions": [{"action": "mcp", "display_name": "CallMcpTool"}]}})
    errors = [t for k, t in kinds.get("antigravity").parse_line(line, {}) if k == "error"]
    assert errors and errors[0].startswith("Antigravity refused") and antigravity.AGY_ALLOW in errors[0]


def test_opencode_errors_say_what_failed():
    """Issue #3: opencode's error event nests the message under data; the feed showed "None"."""
    line = json.dumps({"type": "error", "sessionID": "s", "error": {"name": "ProviderAuthError",
                                                                    "data": {"message": "no key for deepseek"}}})
    assert kinds.get("opencode").parse_line(line) == [("error", "ProviderAuthError: no key for deepseek")]
    generic = json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": "Unexpected server error."}}})
    assert kinds.get("opencode").parse_line(generic) == []  # a placeholder: the log line on stderr says what failed
    assert kinds.get("codex").parse_line(json.dumps({"type": "turn.failed", "error": {"message": "quota"}})) == [("error", "quota")]
    log = ('timestamp=2026-10-14T14:25:35.946Z level=ERROR run=89d82ac2 message=failed ref=err_ab91f09e '
           'error="ProviderModelNotFoundError: Model not found: nosuch/model." cause="ProviderModelNotFoundError: x\\n at y"')
    assert kinds.get("opencode").clean_stderr(log) == "ProviderModelNotFoundError: Model not found: nosuch/model."
    assert kinds.get("opencode").clean_stderr("timestamp=1 level=WARN message=slow") == ""
    agy = 'AGY_ERROR: {"message": "quota exhausted", "code": 429}'
    assert kinds.get("antigravity").clean_stderr(agy) == "quota exhausted"












def test_omp_stream_json_is_read():
    """omp 18's --mode json: a message's text, thinking and usage come with its message_end, tool calls with
    tool_execution_start (omp names MCP tools mcp__decomp_claim_function), a failed tool as an error item, and the
    end of the session once it went through."""
    state: dict = {}
    usage = {"input": 2380, "output": 92, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2472,
             "cost": {"input": 0.000357, "output": 5.52e-05, "cacheRead": 0, "cacheWrite": 0, "total": 0.0004122}}
    lines = [
        {"type": "session", "version": 3, "id": "s", "cwd": "/w"},
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
        {"type": "message_start", "message": {"role": "assistant", "content": [{"type": "text", "text": "I"}]}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "I'll claim it."}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "usage": usage,
                                            "content": [{"type": "text", "text": "Claiming the next one."},
                                                        {"type": "toolCall", "name": "mcp__decomp_claim_function",
                                                         "arguments": {"count": 4}}]}},
        {"type": "tool_execution_start", "toolName": "mcp__decomp_claim_function",
         "args": {"strategy": "easiest", "count": 4}},
        {"type": "tool_execution_end", "toolName": "mcp__decomp_claim_function", "isError": True,
         "result": {"content": [{"type": "text", "text": "Error: the tool exploded"}], "isError": True}},
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "usage": usage,
                                            "content": [{"type": "thinking", "thinking": "The tool failed.",
                                                         "thinkingSignature": "reasoning_content"},
                                                        {"type": "text", "text": "It failed."}]}},
        {"type": "agent_end", "isTerminal": True},
    ]
    out = [item for line in lines for item in kinds.get("omp").parse_line(json.dumps(line), state)]
    spent = json.dumps({"usd": 0.0004122, "input": 2380, "output": 92, "turns": 1})
    assert out == [("text", "Claiming the next one."), ("usage", spent),
                   ("tool", "claim_function(easiest, 4)"), ("error", "Error: the tool exploded"),
                   ("think", "The tool failed."), ("text", "It failed."), ("usage", spent),
                   ("info", "session finished")]
    # every one of omp's own error words: the message, then its stack and the lines of its bundle
    assert kinds.get("omp").clean_stderr("error: No API key found for openai.") == \
        "error: No API key found for openai."
    assert kinds.get("omp").clean_stderr("16480 | ` + \"Then use /model\"") == ""
    assert kinds.get("omp").clean_stderr("      at #$n (B:/~BUN/root/chunks/cli.js:16485:15)") == ""
    assert kinds.get("omp").clean_stderr("Use /login, set an API key environment variable") == \
        "Use /login, set an API key environment variable"


def test_omp_a_failed_turn_is_an_error_and_not_a_finished_session():
    state: dict = {}
    line = json.dumps({"type": "message_end", "message": {
        "role": "assistant", "stopReason": "error", "content": [],
        "errorStatus": 401, "errorMessage": "401 Incorrect API key provided: sk-x"}})
    out = kinds.get("omp").parse_line(line, state)
    assert [k for k, _ in out] == ["error"] and "Incorrect API key" in out[0][1]
    assert kinds.get("omp").parse_line(json.dumps({"type": "agent_end"}), state) == []


def test_omp_sessions_read_only_through_decomp_and_write_no_secret_to_argv(tmp_path, monkeypatch):
    """omp keeps its sign-in and its own mcp.json in one folder, so the session leaves the user's sign-in alone and
    switches their own MCP servers off in its settings instead: the folder's .mcp.json names only the app's server,
    with the token in the file (never on the command line, where every process can read it)."""
    m = _manager(tmp_path, monkeypatch, "omp")
    s = agents.Session("omp-1", "omp", "o", "demo", "omp-1",
                       {"model": "opencode-go/deepseek-v4.1-flash#high"})
    work = tmp_path / "omp"
    work.mkdir()
    cmd, env, stdin = m._command(s, "tok", work)
    assert stdin == m.prompt(s) and not any("line one" in arg for arg in cmd)
    assert cmd[:3] == [cmd[0], "-p", "--mode"] and cmd[cmd.index("--mode") + 1] == "json"
    for flag in ("--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-context-files", "--no-lsp",
                 "--no-tools", "--no-title"):
        assert flag in cmd, flag
    assert cmd[cmd.index("--model") + 1] == "opencode-go/deepseek-v4.1-flash"
    assert cmd[cmd.index("--thinking") + 1] == "high"
    assert not any("tok" in arg for arg in cmd)
    settings = (work / ohmy.SETTINGS_FILE).read_text(encoding="utf-8")
    assert "enabledProviders: []" in settings and "- native" in settings       # the user's own MCP servers: off
    assert "enableProjectConfig: true" in settings                            # this folder's .mcp.json: on
    assert "approvalMode: yolo" in settings and "backend: \"off\"" in settings  # no prompts, no memory
    servers = json.loads((work / ohmy.MCP_FILE).read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == [ohmy.MCP_SERVER]
    assert servers[ohmy.MCP_SERVER] == {"type": "http", "url": "http://127.0.0.1:6464/mcp",
                                        "headers": {"Authorization": "Bearer tok"}}
    assert env[ohmy.READY_ENV] == "1"  # a session that can't reach the app fails before its first turn


def test_omp_takes_its_own_thinking_suffix_off_the_model(tmp_path, monkeypatch):
    """omp's config default carries a thinking suffix ("provider/model:max"), and the launcher's effort comes as
    "#high": either becomes --thinking, and the model stays the bare selector (so a helper's name has no suffix)."""
    m = _manager(tmp_path, monkeypatch, "omp")
    for model, name, effort in (("zai/glm-5.3:max", "zai/glm-5.3", "max"),
                                ("zai/glm-5.3#low", "zai/glm-5.3", "low"),
                                ("zai/glm-5.3:max#low", "zai/glm-5.3", "low"),
                                ("opencode-go/deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash", "")):
        cmd, _, _ = m._command(agents.Session("omp-1", "omp", "o", "demo", "omp-1", {"model": model}), "t", tmp_path)
        assert cmd[cmd.index("--model") + 1] == name, model
        assert ("--thinking" in cmd) == bool(effort)
        if effort:
            assert cmd[cmd.index("--thinking") + 1] == effort


def test_omp_models_come_from_its_json_catalog():
    line = json.dumps({"models": [
        {"provider": "zai", "kind": "chat", "selector": "zai/glm-5.3", "name": "GLM 5.3"},
        {"provider": "zai", "kind": "chat", "selector": "zai/glm-5.3-flash", "name": "GLM 5.3 Flash"},
        {"provider": "web", "kind": "search", "selector": "web/duckduckgo", "name": "DuckDuckGo"},
        {"provider": "opencode-go", "kind": "chat", "selector": "opencode-go/deepseek-v4.1-flash",
         "name": "DeepSeek V4.1 Flash"},
        {"provider": "zai", "kind": "chat", "selector": "zai/glm-5.4", "name": "GLM 5.4"}]})
    # one model per provider per round: `omp models` lists a provider's whole catalog in turn, and the launcher keeps
    # only the first 200, which were all one provider's (nothing else could be picked)
    assert ohmy.OMP.read_models([line]) == [
        {"id": "zai/glm-5.3", "name": "GLM 5.3", "note": "zai"},
        {"id": "opencode-go/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "note": "opencode-go"},
        {"id": "zai/glm-5.3-flash", "name": "GLM 5.3 Flash", "note": "zai"},
        {"id": "zai/glm-5.4", "name": "GLM 5.4", "note": "zai"}]
    assert ohmy.OMP.read_models(["not json"]) == [] and ohmy.OMP.read_models([]) == []


def test_omp_default_model_is_the_one_in_its_own_settings(tmp_path, monkeypatch):
    agent_home = tmp_path / "agent"
    agent_home.mkdir()
    (agent_home / "config.yml").write_text(
        "theme:\n  dark: titanium\nmodelRoles:\n  smol: zai/glm-5.3-flash:auto\n"
        "  default: opencode-go/deepseek-v4.1-flash:max\nmodelTags: {}\n", encoding="utf-8")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_home))
    assert ohmy.agent_dir() == agent_home
    assert ohmy.OMP.config_model() == "opencode-go/deepseek-v4.1-flash:max"
    (agent_home / "config.yml").write_text("modelRoles:\n  smol: zai/glm-5.3-flash\n", encoding="utf-8")
    assert ohmy.OMP.config_model() is None
    # a one-line flow mapping and a block opened by a comment are the same setting
    (agent_home / "config.yml").write_text("modelRoles: {smol: zai/glm-5.3, default: zai/glm-5.4:high}\n")
    assert ohmy.OMP.config_model() == "zai/glm-5.4:high"
    (agent_home / "config.yml").write_text("modelRoles:  # who does what\n  default: 'zai/glm-5.4'  # quoted\n")
    assert ohmy.OMP.config_model() == "zai/glm-5.4"
    # omp reads config.yml, or config.yaml when there is no config.yml: a stale config.yaml never wins
    (agent_home / "config.yml").write_text("modelRoles:\n  smol: zai/glm-5.3\n", encoding="utf-8")
    (agent_home / "config.yaml").write_text("modelRoles:\n  default: stale/model\n", encoding="utf-8")
    assert ohmy.OMP.config_model() is None
    (agent_home / "config.yml").unlink()
    assert ohmy.OMP.config_model() == "stale/model"


def test_omp_settings_folder_is_the_one_omp_resolves(tmp_path, monkeypatch):
    """omp joins PI_CONFIG_DIR to the home folder (even an absolute one), takes a named profile before
    PI_CODING_AGENT_DIR, and treats an empty or `default` OMP_PROFILE as the default profile."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ohmy.Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("PI_CODING_AGENT_DIR", "PI_CONFIG_DIR", "OMP_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    assert ohmy.agent_dir() == tmp_path / ".omp" / "agent"
    monkeypatch.setenv("PI_CONFIG_DIR", ".omp2")
    assert ohmy.agent_dir() == tmp_path / ".omp2" / "agent"
    monkeypatch.setenv("PI_CONFIG_DIR", str(tmp_path / "abs"))
    # omp glues PI_CONFIG_DIR onto the home folder as text, so an absolute one nests instead of replacing it
    nested = Path(str(tmp_path) + os.sep + str(tmp_path / "abs")) / "agent"
    assert ohmy.agent_dir() == nested and ohmy.agent_dir() != (tmp_path / "abs" / "agent")
    monkeypatch.delenv("PI_CONFIG_DIR")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "own"))
    for profile in ("", "default"):
        monkeypatch.setenv("OMP_PROFILE", profile)
        assert ohmy.agent_dir() == tmp_path / "own"  # the default profile still uses PI_CODING_AGENT_DIR
    monkeypatch.setenv("OMP_PROFILE", "work")
    assert ohmy.agent_dir() == tmp_path / ".omp" / "profiles" / "work" / "agent"  # a profile ignores it


@pytest.mark.parametrize("channel", ["stderr", "stdout"])
def test_an_omp_nobody_signed_in_to_stops_and_says_so(tmp_path, monkeypatch, channel):
    """omp says a lack of sign-in on both channels, and both must stop the helper at its first failure instead of
    retrying it five times: `No default model selected. Use /login, ...` on stderr when nobody signed in, and a
    failed request's `401 Incorrect API key provided: ...` as the `errorMessage` of a stdout `message_end`."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(manager, "_preflight_tools", lambda *args, **kwargs: True)
    _, token = manager.net.local_agent("omp-1")
    monkeypatch.setattr(manager.net, "selection_counts", lambda *args, **kwargs: {"available": 1})
    if channel == "stderr":
        code = "import sys; print('No default model selected. Use /login, set an API key environment variable'," \
               " file=sys.stderr); raise SystemExit(1)"
    else:
        event = json.dumps({"type": "message_end", "message": {
            "role": "assistant", "content": [], "stopReason": "error", "errorStatus": 401,
            "errorMessage": "401 Incorrect API key provided: sk-x"}})
        code = f"import sys; print({event!r}); raise SystemExit(1)"
    monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))
    s = agents.Session("omp-1", "omp", "Oh My Pi #1", "game", "omp-1", {})
    assert manager._run_cli(s, token, tmp_path) is False
    assert s.signed_out and "isn't signed in" in s.error and "omp login" in s.error


def test_opencode_2_starts_and_its_quota_error_says_so(tmp_path, monkeypatch):
    """opencode 2.0 takes `--log-level error` only, earlier versions `ERROR` only, and either stops the other at once
    (an `InvalidValue` stack trace, shown with "run opencode auth login" though the user was signed in). The command
    passes no level; opencode 2's error lines are lowercase; its quota error stops the helpers with what it is."""
    m = _manager(tmp_path, monkeypatch, "opencode")
    cmd, _, _ = m._command(agents.Session("opencode-1", "opencode", "o", "demo", "opencode-1", {}), "t", tmp_path)
    assert "--log-level" not in cmd and "--print-logs" in cmd
    assert kinds.get("opencode").clean_stderr('level=error message=failed error="quota exceeded"') == "quota exceeded"
    assert not agents._SIGNIN.search("quota exceeded") and agents._SIGNIN.search("ProviderAuthError: no key")


def test_a_used_up_plan_waits_instead_of_stopping():
    """A night's run lost most of its helpers to their plans' usage limits: those reset, so the helper waits (a short
    rate limit is still a throttle, and a prepaid account out of credit still stops)."""
    for said in ("Claude AI usage limit reached|1792140000", "You've hit your usage limit. Upgrade or try again later",
                 "RESOURCE_EXHAUSTED: quota exhausted", "provider.quota: quota exceeded",
                 "You've hit your session limit · resets 5am (Australia/Sydney)", "You've hit your weekly limit",
                 "You've hit your 5-hour limit"):
        assert agents.LIMIT.search(said) and not agents.no_credit(said), said
    for said in ("Rate limit exceeded", "429 Too Many Requests", "You've hit your rate limit"):
        assert not agents.LIMIT.search(said) and agents.THROTTLED.search(said), said
    assert agents.no_credit("Insufficient Balance") and not agents.LIMIT.search("Insufficient Balance")


def test_a_concurrency_limit_is_a_throttle_not_a_plan_limit():
    """Z.ai's Start Plan takes one request at a time: a second zcode helper, or the ZCode app in use, gets
    "concurrency limit exceeded". It clears in a minute, so it waits THROTTLE_WAIT, not the plan's 15 minutes."""
    said = "concurrency limit exceeded"
    assert agents.THROTTLED.search(said) and not agents.LIMIT.search(said)
    assert agents.THROTTLED.search("concurrency-limit exceeded") and not agents.LIMIT.search("concurrency-limit exceeded")


def _manager(tmp_path, monkeypatch, kind):
    (tmp_path / "bin").mkdir(exist_ok=True)
    exe = tmp_path / "bin" / {"claude": "claude", "codex": "codex", "antigravity": "agy", "opencode": "opencode",
                              "zcode": "zcode", "omp": "omp"}[kind]
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(agents, "_which", lambda cmd: str(exe))
    m = agents.Manager.__new__(agents.Manager)
    m.options_now = lambda kind, options: options
    m.mcp_url, m.home = "http://127.0.0.1:6464/mcp", tmp_path
    m.prompt = lambda s: "line one\nline two with \"quotes\" & <ampersands>"
    m.prepare_session = lambda *a, **kw: None  # This test supplies its own prompt/claim script.
    return m


def test_prompts_go_in_on_stdin_never_the_command_line(tmp_path, monkeypatch):
    """On Windows, npm installs agents as .cmd scripts: cmd.exe cuts a multi-line argument at its first line break
    and caps the command line at 8191 characters. zcode has no stdin prompt: its prompt is a file, checked in
    test_zcode_prompt_reaches_it_by_file_never_as_argv."""
    for kind in ("claude", "codex", "antigravity", "opencode", "omp"):
        m = _manager(tmp_path, monkeypatch, kind)
        s = agents.Session(f"{kind}-1", kind, kind, "demo", f"{kind}-1", {"model": "some-model"})
        work = tmp_path / kind
        work.mkdir()
        cmd, env, stdin = m._command(s, "tok", work)
        assert not any("line one" in a for a in cmd), (kind, cmd)
        assert "line one" in stdin and "some-model" in cmd
        assert env[agents.HELPER_ENV] == str(tmp_path)  # what it starts can be found and ended when the app quits
    # Antigravity: one stream-json user message, the server in the workspace's own MCP config
    m = _manager(tmp_path, monkeypatch, "antigravity")
    s = agents.Session("antigravity-2", "antigravity", "a", "demo", "antigravity-2", {})
    (tmp_path / "agy2").mkdir()
    cmd, _, stdin = m._command(s, "tok", tmp_path / "agy2")
    assert json.loads(stdin) == {"event": "user", "message": {"content": "line one\nline two with \"quotes\" & <ampersands>"}}
    assert cmd[1:5] == ["--output-format", "stream-json", "--input-format", "stream-json"]
    cfg = json.loads((tmp_path / "agy2" / ".agents" / "mcp_config.json").read_text())
    assert cfg["mcpServers"]["decomp"] == {"serverUrl": "http://127.0.0.1:6464/mcp",
                                           "headers": {"Authorization": "Bearer tok"}}


def test_the_windows_stdin_gate_survives_an_empty_prompt_but_not_a_prompt_file(tmp_path, monkeypatch):
    """main always wrote (Windows' \\x01 gate +) the prompt, even when it was empty; winhelper.py waits for that gate
    before starting the CLI. Only an agent that took its prompt by file writes nothing on stdin."""
    class Stdin:
        def __init__(self):
            self.calls: list[str] = []
            self.closed = False

        def write(self, text):
            self.calls.append(text)

        def close(self):
            self.closed = True

    class Proc:
        def __init__(self):
            self.stdin = Stdin()

    def written(prompt):
        proc = Proc()
        agents._feed_stdin(proc, prompt)
        return "".join(proc.stdin.calls).encode("utf-8"), proc.stdin.closed

    monkeypatch.setattr(agents.os, "name", "posix")
    assert written("") == (b"", True)
    assert written("line one\n") == (b"line one\n", True)
    assert written(None) == (b"", True)  # a prompt-file agent: no prompt bytes, just close
    monkeypatch.setattr(agents.os, "name", "nt")
    assert written("") == (b"\x01", True)
    assert written("line one\n") == (b"\x01line one\n", True)
    assert written(None) == (b"\x01", True)  # the Windows gate only; zcode's prompt is in its file

    # an empty prompt for a normal agent is a str (write the gate), not the None sentinel (no stdin)
    monkeypatch.setattr(agents.os, "name", "posix")
    m = _manager(tmp_path, monkeypatch, "claude")
    m.prompt = lambda s: ""
    work = tmp_path / "claude-empty"
    work.mkdir()
    _, _, stdin = m._command(agents.Session("claude-1", "claude", "c", "demo", "claude-1", {}), "tok", work)
    assert stdin == ""


def test_opencode_free_models_keep_their_tools_but_never_get_to_use_them(tmp_path, monkeypatch):
    """OpenCode Zen's free models refuse requests whose tools were taken out ("deny"); "ask" keeps them listed and
    `opencode run` rejects every ask. Models of other providers get "deny"."""
    monkeypatch.setattr(kinds.get("opencode"), "config_model", lambda: None)
    for model, rule in (("opencode/big-pickle", "ask"), ("", "ask"), ("deepseek/deepseek-chat", "deny")):
        m = _manager(tmp_path, monkeypatch, "opencode")
        s = agents.Session("opencode-1", "opencode", "o", "demo", "opencode-1", {"model": model})
        work = tmp_path / f"oc-{rule}-{model.replace('/', '_')}"
        work.mkdir()
        m._command(s, "tok", work)
        config = json.loads((work / "opencode.json").read_text())
        assert config["permission"] == {"edit": rule, "bash": rule, "webfetch": rule}
        # a rejected ask ended the run: a free model's first `rg` ended its session, every session, 20 s in
        assert config.get("experimental") == ({"continue_loop_on_deny": True} if rule == "ask" else None)
    # its own connection, with its own token, and nothing else: opencode 2's shared service ran the user's own
    # `decomp` server without the session's token, so every helper worked as the user's one chat agent
    for version, standalone in (("opencode v2.0.28", True), ("0.15.4", False)):  # opencode 2's shared service
        monkeypatch.setitem(kinds.known_versions, "opencode", (0, version))
        cmd, env, _ = m._command(s, "tok", work)
        assert ("--standalone" in cmd) is standalone
        # opencode 2 works in $PWD, whatever folder it starts in: the app's own PWD put helpers in the user's home
        assert env["PWD"] == str(work)
        config = json.loads((work / "opencode.json").read_text())
        mcp = config["mcp"]["servers"] if standalone else config["mcp"]
        assert list(mcp) == ["decompdecomp"] and mcp["decompdecomp"]["headers"] == {"Authorization": "Bearer tok"}
    # opencode 2's `execute` ran JavaScript whose fetch() reached the internet: only "deny" removes it, and the
    # app's tools then have to be offered outside it
    monkeypatch.setitem(kinds.known_versions, "opencode", (0, "opencode v2.0.32"))
    m._command(s, "tok", work)
    config = json.loads((work / "opencode.json").read_text())
    assert {"execute", "subagent", "external_directory"} <= {p["action"] for p in config["permissions"]
                                                            if p["effect"] == "deny"}
    assert config["mcp"]["servers"]["decompdecomp"]["codemode"] is False


@pytest.mark.parametrize("version", ["1.19.5", "opencode v2.0.32"])
def test_opencode_helpers_use_independent_databases_without_losing_signin(tmp_path, monkeypatch, version):
    """A writer in one helper or the user's OpenCode must not lock another helper out before its MCP handshake."""
    m = _manager(tmp_path, monkeypatch, "opencode")
    monkeypatch.setitem(kinds.known_versions, "opencode", (0, version))
    shared = tmp_path / "user-opencode.db"
    monkeypatch.setenv("OPENCODE_DB", str(shared))
    for key in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key))
    paths = []
    for name in ("first helper", "second helper", "first helper"):
        work = tmp_path / name
        work.mkdir(exist_ok=True)
        s = agents.Session(name, "opencode", name, "demo", name, {"model": "fake/fake"})
        _, env, _ = m._command(s, "tok", work)
        paths.append(env["OPENCODE_DB"])
        assert os.path.isabs(paths[-1]) and os.path.dirname(paths[-1]) == str(work.resolve())
        for key in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            assert env[key] == os.environ[key]  # auth/config discovery is unchanged
    assert paths[0] == paths[2] and paths[0] != paths[1] and str(shared) not in paths
    assert os.environ["OPENCODE_DB"] == str(shared)  # only the child environment changes
    # Exercise real SQLite contention at the paths passed to the CLI, without starting a model or a live project.
    with sqlite3.connect(shared, timeout=0) as user, sqlite3.connect(paths[0], timeout=0) as first:
        user.execute("BEGIN IMMEDIATE")
        first.execute("BEGIN IMMEDIATE")
        with sqlite3.connect(paths[1], timeout=0) as second:
            second.execute("CREATE TABLE startup (ready INTEGER)")
            second.execute("INSERT INTO startup VALUES (1)")
            assert second.execute("SELECT ready FROM startup").fetchone() == (1,)


def test_opencode_2_helpers_get_a_plugin_that_waits_for_the_apps_tools(tmp_path, monkeypatch):
    """#24: opencode 2 starts the first model request before its MCP tools are registered, so the helper's folder gets
    a plugin that waits for the app's server once; older opencode keeps the config it had."""
    m = _manager(tmp_path, monkeypatch, "opencode")
    s = agents.Session("opencode-1", "opencode", "o", "demo", "opencode-1", {"model": "fake/fake"})
    work = tmp_path / "oc-2"
    work.mkdir()
    monkeypatch.setitem(kinds.known_versions, "opencode", (0, "opencode v2.0.32"))
    for _ in range(2):  # every session start writes the config and the plugin again
        m._command(s, "tok", work)
    config = json.loads((work / "opencode.json").read_text())
    assert list(config["mcp"]["servers"]) == [opencode.MCP_SERVER]
    assert config["plugins"] == [f"./{opencode.PLUGIN_DIR}"]  # once, however often a session starts
    plugin = (work / opencode.PLUGIN_DIR / "index.js").read_text()
    assert f"const SERVER = {json.dumps(opencode.MCP_SERVER)}" in plugin
    assert f"const DEADLINE_MS = {opencode.MCP_WAIT_SECONDS * 1000}" in plugin
    old = tmp_path / "oc-old"
    old.mkdir()
    monkeypatch.setitem(kinds.known_versions, "opencode", (0, "0.15.4"))
    m._command(s, "tok", old)
    assert "plugins" not in json.loads((old / "opencode.json").read_text())
    assert not (old / opencode.PLUGIN_DIR).exists()


def test_codex_helpers_cannot_search_the_web(tmp_path, monkeypatch):
    """Helpers that searched the web pulled a public decomp's code instead of working the function out."""
    m = _manager(tmp_path, monkeypatch, "codex")
    cmd, _, _ = m._command(agents.Session("codex-1", "codex", "c", "demo", "codex-1", {}), "t", tmp_path)
    assert 'web_search="disabled"' in cmd and cmd[cmd.index("-s") + 1] == "read-only"


def test_gemini_cli_helpers_carry_on_as_antigravity():
    assert kinds.kind_id("gemini") == "antigravity" and kinds.kind_id("agy") == "antigravity"














def test_assembly_for_agents_is_compact():
    asm = ("glabel func_80013C60\n/* 4460 80013C60 27BDFF88 */  addiu      $sp, $sp, -0x78\n"
           "/* 4464 80013C64 AFB10054 */  sw         $s1, 0x54($sp)\n.L80013CC4:\n"
           "/* 44C8 80013CC8 00000000 */  nop\n")
    assert scoring.compact_asm(asm) == ("glabel func_80013C60\n  addiu $sp, $sp, -0x78\n  sw $s1, 0x54($sp)\n"
                                        ".L80013CC4:\n  nop")
    assert scoring.compact_asm("\tpush {r4, lr}\n") == "\tpush {r4, lr}"  # other listings stay as they are


def test_diffs_sum_up_and_shorten_long_runs():
    rows = [(" ", "addiu $sp, $sp, -8", "addiu $sp, $sp, -8"), ("r", "move $s0, $a0", "move $s1, $a0")]
    rows += [("<", f"lw $v0, {i}($s0)", "") for i in range(300)]
    rows += [(" ", "jr $ra", "jr $ra")]
    text = scoring.render_rows(rows)
    assert text.splitlines()[0] == "300 missing, 1 regalloc"
    assert "... 297 more missing (target only) ..." in text and "lw $v0, 299($s0)" in text
    assert len(text.splitlines()) < 15


def test_a_helpers_identity_says_its_model_so_records_never_mix():
    """Helpers were opencode-1, opencode-2... whatever model they ran: re-added with another model, a helper took over
    the old one's matches. The model is part of the identity now; the same model gets its name (and record) back."""
    name = agents.helper_name
    assert name("opencode", "deepseek/deepseek-v4.1-flash", set()) == "opencode-deepseek-v4.1-flash-1"
    assert name("opencode", "deepseek/deepseek-v4.1-flash", {"opencode-deepseek-v4.1-flash-1"}) == "opencode-deepseek-v4.1-flash-2"
    assert name("opencode", "muse-spark-1.3", {"opencode-deepseek-v4.1-flash-1"}) == "opencode-muse-spark-1.3-1"
    assert name("antigravity", None, set()) == "antigravity-1"
    long = name("antigravity", "Some Provider/A Very Long Model Name: Preview Edition 2026", set())
    assert len(long) <= 40 and all(c.isalnum() or c in "-_." for c in long)


def test_claude_offers_each_version_as_well_as_the_latest():
    """The launcher offered only "Opus", "Sonnet", "Haiku" (always the newest): the owner wanted e.g. Opus 4.6."""
    offered = {m["id"]: m["name"] for m in kinds.get("claude").models("claude")}
    assert offered["opus"] == "Opus" and offered["claude-opus-4-6"] == "Opus 4.6"
    assert offered["claude-sonnet-5-5"] == "Sonnet 5.5" and offered["claude-fable-5"] == "Fable 5"
    assert agents.helper_name("claude", "claude-opus-4-6", set()) == "claude-opus-4-6-1"
    assert agents.helper_name("claude", "opus", set()) == "claude-opus-1"  # the alias keeps its old record


def test_every_helper_with_an_effort_offers_the_levels_its_cli_supports(tmp_path):
    """The launcher offered max for opencode but not xhigh, xhigh for Codex but not max, and neither for the API
    helper (a local llama.cpp model's template wants xhigh): those offer all four now. zcode's GLM models offer
    low/high/max only, so offering xhigh there would make every session fail on an unsupported effort."""
    for kind in kinds.AGENTS:
        if kind.takes_effort:
            expected = {"low", "high", "max"} if kind.id in ("kimi", "zcode") else {"medium", "high", "xhigh", "max"}
            assert expected <= set(kind.describe()["efforts"]), kind.id
    cmd, _ = kinds.get("codex").session_command("codex", tmp_path, URL, "token", "gpt-5.5#max")
    assert 'model_reasoning_effort="max"' in cmd


def test_claude_helpers_think_as_hard_as_the_launcher_says(tmp_path):
    """The launcher sends an effort with the model's name ("opus#xhigh", as for opencode); Claude Code takes it as
    --effort, and an effort-less helper gets none (the model's default)."""
    claude = kinds.get("claude")
    assert claude.describe()["takes_effort"] and "xhigh" in claude.describe()["efforts"]
    cmd, _ = claude.session_command("claude", tmp_path, URL, "token", "opus#xhigh")
    assert cmd[-4:] == ["--model", "opus", "--effort", "xhigh"]
    cmd, _ = claude.session_command("claude", tmp_path, URL, "token", "opus")
    assert cmd[-2:] == ["--model", "opus"] and "--effort" not in cmd
    assert agents.helper_name("claude", "opus#xhigh", set()) == "claude-opus-xhigh-1"  # a record of its own


# ---------------------------------------------------------------------------------- helpers as records

URL = "http://127.0.0.1:1/mcp"


def _game(tmp_path, functions: int = 1) -> RomNetwork:
    """A project with a few open functions, enough for helpers to run their loop on."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for k in range(functions):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                       (pid, f"f{k}", 16, 4, "open", ""))
    return net


def _no_agent_settings(monkeypatch) -> None:
    """No agent's own settings read: the default model of none."""
    for kind in kinds.AGENTS:
        monkeypatch.setattr(kind, "config_model", lambda: None)


class Quick(threading.Event):
    """A helper's stop event whose waits end at once, noting how long they'd have been."""

    def __init__(self):
        super().__init__()
        self.waits: list[float] = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.is_set()


class ClockQuick(Quick):
    """Simulate elapsed wall time for deadline-aware waits without sleeping."""
    def __init__(self,monkeypatch,now=None):
        super().__init__()
        self.now=time.time() if now is None else now
        monkeypatch.setattr(time,'time',lambda:self.now)

    def wait(self,timeout=None):
        result=super().wait(timeout)
        if not result:self.now+=timeout or 0
        return result


@pytest.fixture
def quiet(monkeypatch):
    """Helpers that start without their real loop, and no agent's own settings read."""
    _no_agent_settings(monkeypatch)
    monkeypatch.setattr(agents.Manager, "_prepare", lambda self, slug: None)
    monkeypatch.setattr(agents.Manager, "_run", lambda self, s: None)


def test_helpers_are_records_that_outlive_the_app(tmp_path, quiet):
    """After a restart the Agents view showed opencode-20 with only "agents running": helpers lived in the app's memory
    and their claims in the database, so a dead helper's claim showed as a "connected agent" with no stop button, and
    its name stayed taken. Now a helper is a record: after a restart it shows as it was, its claim is released at
    once, it carries on if the app stopped it by closing, and a new helper of the same model takes its name back."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("claude", "game", 1, {"model": "opus"})
    (other,) = m.start("codex", "game", 1, {})
    m.stop(other)  # you stopped this one
    helper, _ = net.local_agent("claude-opus-1")
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES((SELECT id FROM functions WHERE name='f0'),?,?,?)", (helper.id, 0, 1e12))
    later = agents.Manager(net, tmp_path, URL)  # the app closed without a word (it crashed), and starts again
    s = later.sessions[sid]
    assert (s.agent_name, s.state, s.stopped_by) == ("claude-opus-1", "stopped", agents.BY_QUIT)
    assert net.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0
    assert later.resume() == [sid]  # what the app stopped carries on, what you stopped doesn't
    assert later.sessions[sid].state == "starting" and later.sessions[other].state == "stopped"
    later.stop(sid)
    assert later.sessions[sid].why == "stopped by you"
    fresh = later.start("claude", "game", 1, {"model": "opus"})[0]
    assert fresh != sid  # Add creates a new identity; Start again resumes a record.
    assert net.store.one("SELECT COUNT(*) AS n FROM helpers")["n"] == 3
    later.remove(other)
    assert other not in later.sessions and net.store.one("SELECT COUNT(*) AS n FROM helpers")["n"] == 2


CLI_MODEL_FLAGS = {"antigravity": "--model", "claude": "--model", "codex": "-m", "cursor": "--model", "kimi": "-m",
                   "opencode": "-m", "zcode": "--model"}
CLI_EXECUTABLES = {"antigravity": "agy", "claude": "claude", "codex": "codex", "cursor": "cursor-agent",
                   "kimi": "kimi.js", "opencode": "opencode", "zcode": "zcode"}


def _stand_in_cli(tmp_path, monkeypatch, kind) -> None:
    """A program standing in for an agent's CLI, found where the app looks: a session's command can then be read
    without starting a real CLI (which starts the MCP servers in its own settings)."""
    exe = tmp_path / "bin" / CLI_EXECUTABLES[kind]
    exe.parent.mkdir(exist_ok=True)
    if kind == "kimi":  # its launcher wants the npm build (a .js entry) with Node beside it; neither runs here
        exe.write_text("#!/usr/bin/env node\n")
        for node in ("node", "node.exe"):
            (exe.parent / node).write_text("#!/bin/sh\n")
            (exe.parent / node).chmod(0o755)
    else:
        exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(agents, "_which", lambda cmd: str(exe))


def _command_model(m, sid: str, kind: str, tmp_path) -> str:
    """The model the helper's next session command line names, "" when it names none."""
    m.prompt = lambda s: "line one"
    work = tmp_path / f"{kind}-{sid}-command"
    work.mkdir(exist_ok=True)
    cmd, _, _ = m._command(m.sessions[sid], "tok", work)
    flag = CLI_MODEL_FLAGS[kind]
    return cmd[cmd.index(flag) + 1] if flag in cmd else ""


def _own_configs(tmp_path, monkeypatch) -> None:
    """The agents' own settings read from this test's home, never the user's (and no Cursor keychain lookup)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")


@pytest.mark.parametrize("kind", sorted(CLI_MODEL_FLAGS))
def test_a_helper_started_on_the_clis_default_keeps_that_model_when_the_default_changes(kind, tmp_path, monkeypatch,
                                                                                         quiet):
    """#204: helpers started on the launcher's "Default · <model>" entry were named after that model but recorded no
    model, so changing the CLI's default later switched a resumed helper to another model. The default it started on
    is resolved and recorded now, and its command line still names it after the default has changed."""
    _stand_in_cli(tmp_path, monkeypatch, kind)
    _own_configs(tmp_path, monkeypatch)
    agent = kinds.get(kind)
    first_model = "test/gemini-3.8-flash" if kind == "opencode" else "gemini-3.8-flash"
    second_model = "test/argon-4-medium" if kind == "opencode" else "argon-4-medium"
    monkeypatch.setattr(agent, "config_model", lambda: first_model)
    # The list its CLI last printed (`agy models` for Antigravity caches display names beside their ids); a start
    # reads it cached and never runs the CLI: `agy models` starts the MCP servers in the user's settings.
    monkeypatch.setitem(kinds._models, kind, (time.time(), [
        {"id": first_model, "name": "Gemini 3.8 Flash (High)", "note": "test"},
        {"id": second_model, "name": "Argon 4 (Medium)", "note": "test"}]))
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start(kind, "game", 1, {})
    assert m.sessions[sid].model == first_model
    row = net.store.one("SELECT model, options FROM helpers WHERE id=?", int(sid))
    assert row["model"] == first_model
    assert json.loads(row["options"])["model"] == first_model
    m.stop()  # the app quits: the helper carries on when it starts again
    monkeypatch.setattr(agent, "config_model", lambda: second_model)  # chosen in the CLI meanwhile
    later = agents.Manager(net, tmp_path, URL)
    assert later.resume() == [sid]
    assert _command_model(later, sid, kind, tmp_path) == first_model
    (new,) = later.start(kind, "game", 1, {})  # "Default" for a new helper is the default now
    assert _command_model(later, new, kind, tmp_path) == second_model


@pytest.mark.parametrize("kind", sorted(CLI_MODEL_FLAGS))
def test_a_chosen_model_survives_resume_and_start_again(kind, tmp_path, monkeypatch, quiet):
    """A helper started on a model the user chose keeps it when the app quits and it carries on, and when it is
    started again by hand: every later session's command names the chosen model, whatever the CLI's default is."""
    _stand_in_cli(tmp_path, monkeypatch, kind)
    _own_configs(tmp_path, monkeypatch)
    agent = kinds.get(kind)
    monkeypatch.setattr(agent, "config_model", lambda: "cli-default")
    if kind == "antigravity":
        monkeypatch.setitem(kinds._models, kind, (time.time(), [
            {"id": "chosen-model", "name": "Chosen Model", "note": "test"}]))
    selected = "provider/chosen-model" if kind == "opencode" else "chosen-model"
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start(kind, "game", 1, {"model": selected})
    m.stop()
    monkeypatch.setattr(agent, "config_model", lambda: "argon-4-medium")
    later = agents.Manager(net, tmp_path, URL)
    assert later.resume() == [sid]
    assert _command_model(later, sid, kind, tmp_path) == selected
    later.stop(sid)  # stopped by you
    monkeypatch.setattr(agent, "config_model", lambda: "another-default")
    later.restart(sid)  # Start again
    assert _command_model(later, sid, kind, tmp_path) == selected


@pytest.mark.parametrize("rows, pinned", [
    ([{"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "note": "gemini"}],
     "gemini-3.8-flash-high"),
    ([{"id": "another-model", "name": "Another Model", "note": "gemini"}], ""),
    ([], ""),  # the list wasn't cached yet: nothing to map, so no --model (as before)
])
def test_antigravity_maps_the_display_name_its_settings_hold_to_the_slug_its_model_flag_takes(rows, pinned, tmp_path,
                                                                                              monkeypatch, quiet):
    """#204: Antigravity's settings keep a display name ("Gemini 3.8 Flash (High)"), while `agy --model` takes the
    slug `agy models` lists. Starting on Default records and passes the slug; when the cached list hasn't the name,
    it can't be mapped, so no --model is passed (as before). The helper's name is sensible in both cases."""
    _stand_in_cli(tmp_path, monkeypatch, "antigravity")
    _own_configs(tmp_path, monkeypatch)
    ran = tmp_path / "bin" / "agy-ran"  # `agy models` starts the user's MCP servers: a start must never run it
    exe = tmp_path / "bin" / CLI_EXECUTABLES["antigravity"]
    exe.write_text('#!/bin/sh\ntouch "$(dirname "$0")/agy-ran"\n')
    exe.chmod(0o755)
    agent = kinds.get("antigravity")
    monkeypatch.setattr(agent, "config_model", lambda: "Gemini 3.8 Flash (High)")
    monkeypatch.setitem(kinds._models, "antigravity", (time.time(), rows))
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("antigravity", "game", 1, {})
    assert not ran.exists()
    assert m.sessions[sid].model == pinned
    assert m.sessions[sid].agent_name == "antigravity-gemini-3.8-flash-high-1"
    row = net.store.one("SELECT model, options FROM helpers WHERE id=?", int(sid))
    assert row["model"] == pinned
    assert (json.loads(row["options"]).get("model") or "") == pinned
    assert _command_model(m, sid, "antigravity", tmp_path) == pinned
    # changing the CLI's default meanwhile never switches this helper: it keeps what it started on (or, unmapped,
    # keeps starting on the CLI's default, as it did before)
    m.stop()
    monkeypatch.setattr(agent, "config_model", lambda: "Argon 4 (Medium)")
    later = agents.Manager(net, tmp_path, URL)
    assert later.resume() == [sid]
    assert _command_model(later, sid, "antigravity", tmp_path) == pinned


def test_remove_paused_stops_and_deletes_only_paused_helpers_on_the_project(tmp_path, quiet):
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    paused_ids = [
        m.start("claude", "game", 1, {"model": "paused-a"})[0],
        m.start("claude", "game", 1, {"model": "paused-b"})[0],
    ]
    (working_id,) = m.start("claude", "game", 1, {"model": "working"})
    (elsewhere_id,) = m.start("claude", "other", 1, {"model": "paused-elsewhere"})
    for sid in paused_ids + [elsewhere_id]:
        m.sessions[sid].state = "paused"
        m._save(m.sessions[sid])
    m.sessions[working_id].state = "working"
    m._save(m.sessions[working_id])

    assert m.remove_paused("game") == 2

    assert all(sid not in m.sessions for sid in paused_ids)
    assert working_id in m.sessions and m.sessions[working_id].state == "working"
    assert elsewhere_id in m.sessions and m.sessions[elsewhere_id].state == "paused"
    rows = net.store.all("SELECT id, state FROM helpers ORDER BY id")
    assert {str(row["id"]): row["state"] for row in rows} == {
        working_id: "working", elsewhere_id: "paused",
    }


def test_an_api_helper_starts_again_with_its_endpoints_key(tmp_path, quiet):
    """An API helper's key is never in its record: started again by hand, it gets it from its saved endpoint, and one
    whose endpoint is gone says so."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("api", "game", 1, {"model": "m", "endpoint": "e1", "api_key": "sk-1"})
    m.stop(sid)
    later = agents.Manager(net, tmp_path, URL)
    assert "api_key" not in later.sessions[sid].options
    later.options_now = lambda kind, options: options | {"api_key": "sk-now"} if options.get("endpoint") == "e1" else None
    later.restart(sid)
    assert later.sessions[sid].options["api_key"] == "sk-now" and later.sessions[sid].state == "starting"
    later.stop(sid)
    later.options_now = lambda kind, options: None
    with pytest.raises(ValueError, match="endpoint was removed"):
        later.restart(sid)


def test_the_prompt_gives_the_compile_budget_the_guide_gives(tmp_path, monkeypatch):
    """The prompt said "no better after 4 compiles in a row" while the guide said "~20"."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    monkeypatch.setattr(agents.agent_startup, "project_facts", lambda net, slug: {"kind": "rom", "title": "Game", "platform": "n64",
                                                      "compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    s = agents.Session("1", "claude", "claude-1", "game", "claude-1", {})
    assert network.stall_budget_text() in m._prompt(s) and "8 compiled claims" in m._prompt(s)


def test_a_project_started_from_scratch_isnt_called_a_public_decomp(tmp_path, monkeypatch):
    """Told a new DS project was "a public matching-decompilation project", a model spent its first 7 minutes
    looking for the project's conventions in config/ and build.ninja; on an N64 game one went to fetch
    the finished public decomp. Every prompt says code from outside the project is out."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = agents.Session("1", "claude", "claude-1", "game", "claude-1", {})
    for origin, says in (("scratch", "no upstream project"), (None, "a public matching-decompilation project")):
        monkeypatch.setattr(agents.agent_startup, "project_facts", lambda net, slug, o=origin: {"kind": "fork", "origin": o, "title": "Game",
                                                                     "platform": "nds", "platform_name": "Nintendo DS"})
        assert says in m._prompt(s) and "Never fetch or copy code from outside this project" in m._prompt(s)
    monkeypatch.setattr(agents.agent_startup, "project_facts", lambda net, slug: {"kind": "rom", "title": "Game", "platform": "n64",
                                                      "compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    assert "Never fetch or copy code from outside this project" in m._prompt(s)


def test_stopping_a_helper_frees_its_function_at_once(tmp_path, quiet):
    """A stopped helper's claim used to wait for its thread to notice (an API helper waits on its model meanwhile):
    removed from the list, it showed up as a "connected agent" without a Stop button."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("api", "game", 1, {"model": "deepseek-chat", "provider_name": "DeepSeek"})
    helper, _ = net.local_agent(m.sessions[sid].agent_name)
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES((SELECT id FROM functions WHERE name='f0'),?,?,?)", (helper.id, 0, 1e12))
    m.stop(sid)
    assert net.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0
    assert net.store.one("SELECT stopped_by FROM helpers WHERE id=?", sid)["stopped_by"] == agents.BY_YOU
    # its run is over: what it released in it no longer keeps it off those functions next time
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE agent_id=? AND kind='freed'", helper.id)["n"] >= 1


def test_removing_a_room_helper_releases_its_function_at_the_host(tmp_path, quiet, monkeypatch):
    """A helper's local claim is only a mirror in a room: the host must hear when it is removed, too."""
    monkeypatch.setattr(agents, "detect", lambda quick=False: [])
    monkeypatch.setattr(agents.Manager, "_strays", lambda self: set())
    guest_root, host_root = tmp_path / "guest", tmp_path / "host"
    guest_root.mkdir()
    host_root.mkdir()
    net, host = _game(guest_root), _game(host_root)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("api", "game", 1, {"model": "deepseek-chat", "provider_name": "DeepSeek"})
    helper, _ = net.local_agent(m.sessions[sid].agent_name)
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES((SELECT id FROM functions WHERE name='f0'),?,?,?)", (helper.id, 0, 1e12))
    host_agent, _ = host.local_agent(f"alex.{helper.name}")
    host.claim_row(host_agent, "game", "f0")

    class RoomLink:
        def __init__(self):
            self.releases = []

        def call(self, route, agent, /, **data):
            assert route == "/collab/release"
            self.releases.append((route, agent.name, data["function"]))
            host.release(host_agent, data["function"], data["notes"], "game")

    room = net.links["game"] = RoomLink()
    m.remove(sid)

    assert room.releases == [("/collab/release", helper.name, "f0")]
    claims = net.store.one("SELECT COUNT(*) AS n FROM claims")
    helpers = net.store.one("SELECT COUNT(*) AS n FROM helpers")
    assert claims is not None and claims["n"] == 0
    assert helpers is not None and helpers["n"] == 0
    host_claims = host.store.one("SELECT COUNT(*) AS n FROM claims")
    released = host.store.one("SELECT kind FROM events WHERE agent_id=? AND detail='f0' ORDER BY id DESC LIMIT 1",
                              host_agent.id)
    assert host_claims is not None and host_claims["n"] == 0
    assert released is not None and released["kind"] == "release"


def test_a_failing_helper_starts_again_then_pauses_with_the_error(tmp_path, monkeypatch):
    """Helpers stopped after an error and stayed stopped ("agent stopped, see the message", in a tab nobody looked
    at). Now one starts again by itself, waiting longer after each failure; five fast failures in a row pause it, with
    the error to read."""
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = m._record("game", "opencode", "opencode-1", "opencode-1", {})
    s.stop = Quick()

    def fails(s, token, workdir):
        s.error = "ProviderModelNotFoundError: Model not found: nosuch/model."
        return False
    monkeypatch.setattr(m, "_run_cli", fails)
    m._run(s)
    assert s.state == "paused" and "Model not found" in s.why and s.runs == agents.FAST_FAILURES_TO_PAUSE
    assert s.restarts == agents.FAST_FAILURES_TO_PAUSE - 1
    pauses = [w for w in s.stop.waits if w]
    assert len(pauses) == agents.FAST_FAILURES_TO_PAUSE - 1 and pauses == sorted(pauses)  # longer each time
    assert pauses[0] <= agents.BACKOFF_FIRST
    rows = net.store.all("SELECT outcome FROM helper_sessions WHERE helper_id=?", s.id)
    assert [r["outcome"] for r in rows] == ["failed"] * agents.FAST_FAILURES_TO_PAUSE
    assert net.store.one("SELECT state FROM helpers WHERE id=?", s.id)["state"] == "paused"


def test_an_expired_sign_in_is_recognised_retried_then_asks_for_a_new_sign_in(tmp_path, monkeypatch):
    """Claude Code exited with code 1 at once, "Failed to authenticate: OAuth session expired and could
    not be refreshed", and the app took it for an ordinary failure (retries after 10, 19, 39, 76 s, outcome
    "failed", nothing saying why) until the user happened to sign in again. Now it's recognised, tried again a few
    times, and the helper then pauses asking for a new sign-in, without counting as fast failures."""
    monkeypatch.setattr(agents.Manager, "_preflight_tools", lambda *a, **kw: None)
    monkeypatch.setattr(agents.Manager, "prepare_session", lambda *a, **kw: None)
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = m._record("game", "claude", "claude-1", "claude-1", {})
    s.stop = Quick()
    said = "Failed to authenticate: OAuth session expired and could not be refreshed"
    events = [{"type": "assistant", "error": "authentication_failed", "message": {
                   "model": "<synthetic>", "content": [{"type": "text", "text": said}]}},
              {"type": "result", "is_error": True, "result": said, "total_cost_usd": 0}]
    script = "import json, sys\nsys.stdin.read()\n" + "".join(
        f"print(json.dumps({e!r}), flush=True)\n" for e in events) + "sys.exit(1)\n"
    monkeypatch.setattr(m, "_command", lambda s, token, workdir: ([sys.executable, "-c", script], dict(os.environ), ""))
    m._run(s)
    assert s.state == "paused" and "needs you to sign in again" in s.why and said in s.why
    assert s.todo == kinds.get("claude").signin
    assert s.runs == agents.AUTH_RETRIES  # not FAST_FAILURES_TO_PAUSE: it's the sign-in, not the helper
    waits = [w for w in s.stop.waits if w]
    assert len(waits) == agents.AUTH_RETRIES - 1 and waits[0] >= agents.AUTH_WAIT - 1
    rows = net.store.all("SELECT outcome FROM helper_sessions WHERE helper_id=?", s.id)
    assert [r["outcome"] for r in rows] == ["signin"] * agents.AUTH_RETRIES
    assert any("sign-in failed" in f["text"] for f in s.feed)


def test_a_usage_limit_waits_until_it_resets_then_carries_on(tmp_path, monkeypatch):
    """A night's run lost most of its helpers to their plans' usage limits. A helper waits until the time the agent
    says the limit resets, then carries on by itself; its last words show meanwhile."""
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = m._record("game", "claude", "claude-1", "claude-1", {})
    s.stop = ClockQuick(monkeypatch)
    resets = time.time() + 3 * 3600
    said = "You've hit your limit · resets 3pm"
    runs = []

    def run(s, token, workdir):
        runs.append(s.state)
        if len(runs) == 1:
            s.push("text", said)
            s.limited, s.limit_until = True, resets
            return False
        m.stop(s.id)
        return True
    monkeypatch.setattr(m, "_run_cli", run)
    m._run(s)
    assert len(runs) == 2 and s.restarts == 1 and s.state == "stopped" and s.last_message == said
    assert abs(sum(w for w in s.stop.waits if w) - (3 * 3600 + agents.LIMIT_RESET_MARGIN)) < 5
    assert any("usage limit is reached" in f["text"] for f in s.feed)
    assert [r["outcome"] for r in net.store.all("SELECT outcome FROM helper_sessions")] == ["limit", "stopped"]


def test_when_a_usage_limit_resets_is_read_from_what_the_agent_said():
    from datetime import datetime, timedelta, timezone

    noon = datetime(2026, 10, 18, 13, 0).astimezone()
    now = noon.timestamp()
    reset = agents.limit_reset
    assert reset(f"Claude AI usage limit reached|{int(now) + 3600}", now) == int(now) + 3600
    assert reset("You've hit your usage limit. Upgrade to Pro or try again in 2 hours 13 minutes.", now) == now + 7980
    assert reset("RESOURCE_EXHAUSTED: Quota exceeded. Please retry in 46.6s.", now) == pytest.approx(now + 46.6)
    assert reset("5-hour limit reached ∙ resets 3pm", now) == (noon + timedelta(hours=2)).timestamp()
    assert reset("You've hit your usage limit. Try again at 8:05 PM.", now) == (noon + timedelta(hours=7, minutes=5)).timestamp()
    assert reset("resets 10am", now) == (noon + timedelta(hours=21)).timestamp()  # tomorrow
    assert datetime.fromtimestamp(reset("You've hit your limit · resets 3pm (UTC)", now), timezone.utc).hour == 15
    from zoneinfo import ZoneInfo
    sydney = datetime.fromtimestamp(reset("You've hit your session limit · resets 5am (Australia/Sydney)", now),
                                    ZoneInfo("Australia/Sydney"))
    assert (sydney.hour, sydney.minute) == (5, 0)
    # an ISO time in UTC, two and a half hours after `now` wherever the tests run: 15:30Z was in the past on a computer
    # whose 13:00 is later than 15:30 UTC (America/New_York: 17:00 UTC), and a reset in the past is none
    later = (noon + timedelta(hours=2, minutes=30)).astimezone(timezone.utc)
    assert reset(f"quota resets at {later:%Y-%m-%dT%H:%M:%SZ}", now) == later.timestamp()
    for vague in ("quota exhausted", "resets 3", f"usage limit reached|{int(now) + 365 * 86400}"):
        assert reset(vague, now) is None, vague


def test_quitting_ends_everything_a_helper_started(tmp_path):
    """An `opencode serve --service` outlived its parent (its parent became PID 1): quitting ends a helper's process
    group, and what it started that left the group."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    pidfile = tmp_path / "service.pid"
    code = ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
            f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ, **{agents.HELPER_ENV: str(tmp_path)}),
                            start_new_session=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    service = int(pidfile.read_text())
    s = agents.Session("1", "opencode", "opencode-1", "game", "opencode-1", {}, state="working")
    s.proc = proc
    m.sessions[s.id] = s
    m.shutdown()
    proc.wait(10)

    def alive(pid: int) -> bool:  # (a process that ended but nobody collected yet counts as ended)
        if sys.platform == "win32":
            from .test_platform import _alive

            return _alive(pid)
        if sys.platform == "darwin":
            status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
            return status.returncode == 0 and not status.stdout.strip().startswith("Z")
        try:
            return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False
    for _ in range(50):
        if not alive(service):
            break
        time.sleep(0.1)
    assert not alive(service) and s.state == "stopped" and s.stopped_by == agents.BY_QUIT


def test_startup_without_proc_filesystem(tmp_path, monkeypatch):
    """Windows has no /proc: starting with no previous helper groups must still work."""
    monkeypatch.setattr(agents, "PROCFS", tmp_path / "proc")
    m = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, URL)
    assert m._strays() == set()


def test_finding_the_installed_agents_never_holds_up_a_page(tmp_path, monkeypatch):
    """The first /api/state took 3.7 s: it ran `agy models`, `opencode models` and six `--version`s. That runs in the
    background from the app's start now; until it's done, the page gets where the agents are, without their models."""
    gate = threading.Event()

    def detect(quick=False):
        if quick:
            return [{"id": "claude", "models": []}]
        gate.wait(5)
        return [{"id": "claude", "models": ["opus"]}]
    monkeypatch.setattr(agents, "detect", detect)
    m = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, URL)
    began = time.time()
    assert m.detected() == [{"id": "claude", "models": []}] and m.detecting()
    assert time.time() - began < 1
    gate.set()
    m.refresh_detected(wait=True)
    assert m.detected() == [{"id": "claude", "models": ["opus"]}] and not m.detecting()


def test_the_tests_never_run_an_agent_to_find_it(monkeypatch):
    """`agy models` starts the MCP servers in Antigravity's settings (the old bridge among them started the user's own
    app): with DECOMP_DETECT_AGENTS=off nothing is run, only looked for."""
    assert os.environ.get(agents.DETECT_ENV) == "off"

    def never(*args, **kwargs):
        raise AssertionError(f"ran {args[0]}")
    monkeypatch.setattr(agents.subprocess, "run", never)
    assert {row["id"] for row in agents.detect()} == {a.id for a in kinds.AGENTS}


def test_the_api_helper_folds_each_finished_function_into_a_line(tmp_path, monkeypatch):
    """Ridge Racer V's API helper used 240K tokens per match: it trimmed old tool output wherever the conversation grew,
    so every request changed near its start and the provider's prompt cache never served it. Now what led up to a
    finished function folds into one line, nothing before it changes, the turn that finished it stays whole, and each
    session's tokens (cached ones apart), turns and tool calls are on its row."""
    import httpx

    _no_agent_settings(monkeypatch)
    net = _game(tmp_path, functions=2)
    m = agents.Manager(net, tmp_path, URL)
    m.prompt = lambda s: "task"
    m.prepare_session = lambda *a, **kw: None  # This test supplies its own prompt/claim script.
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "per_session": 2,
                                                       "context_tokens": 32768, "prompts":{"instructions":"Custom API experiment instructions"}})
    s.stop = Quick()

    def call(net, agent, slug, name, args):
        if name == "claim_function":
            return f"## CLAIMED: game/{net.claim_row(agent, slug)['name']}\n" + "assembly " * 300
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched' WHERE name=?", (args["function"],))
            db.execute("DELETE FROM claims WHERE agent_id=?", (agent.id,))
        return f"✅ MATCH! {args['function']} is byte-identical"
    monkeypatch.setattr(agents.tools, "call", call)
    steps = iter(["claim_function", "compile_and_diff:f0", "claim_function", "compile_and_diff:f1"])
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["messages"])
        name, _, fn = next(steps).partition(":")
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{len(sent)}", "type": "function", "function": {
            "name": name, "arguments": json.dumps({"function": fn, "source": "int f;"} if fn else {})}}]}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {
            "prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 800}}})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    m._run(s)
    assert sent[0][0] == {'role':'system','content':'Custom API experiment instructions'}
    assert any(e['kind']=='instructions' and e['text']=='Custom API experiment instructions' for e in m.transcript('game',s.id)['events'])
    assert len(sent) == 4 and s.state == "stopped" and "nothing left" in s.why
    third = sent[2]  # after f0 matched: its claim folded into a line, the match itself whole
    assert third[:2] == sent[0][:2] and "folded" in third[3]["content"] and "matched f0" in third[3]["content"]
    assert "byte-identical" in third[-1]["content"] and not any("assembly" in str(x.get("content")) for x in third)
    assert sent[3][:len(third)] == third  # what was sent stays as it was
    row = net.store.one("SELECT * FROM helper_sessions WHERE helper_id=?", s.id)
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"]) == (800, 3200, 200)
    assert (row["turns"], row["tool_calls"], row["outcome"]) == (4, 4, "ok")


@pytest.mark.parametrize("answer, said", [
    ({"choices": [{"message": {"role": "assistant", "content": "int f(void) { return 0; }"}}]}, "without calling a tool"),
    ({"error": "Unexpected endpoint or method"}, "often ends in /v1"),
])
def test_an_api_helper_that_never_starts_says_why(tmp_path, monkeypatch, answer, said):
    """A model fine-tuned to write C (in LM Studio) answered in words: the helper started again and again with an
    empty error. LM Studio's address without /v1 failed with KeyError: 'choices'."""
    import httpx

    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    m.prompt = lambda s: "task"
    m.prepare_session = lambda *a, **kw: None  # This test supplies its own prompt/claim script.
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "context_tokens": 32768, "prompts":{"instructions":"Custom API experiment instructions"}})
    s.stop = Quick()
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=answer)), **kw))
    m._run(s)
    assert said in s.error and said in s.why


def test_the_api_helper_sends_a_thinking_models_reasoning_back(tmp_path, monkeypatch):
    """DeepSeek in thinking mode refuses a request whose tool calls come without the reasoning that made them ("The
    reasoning_content in the thinking mode must be passed back to the API"): two DeepSeek helpers waited out that
    error 106 times in a day. It goes back with them; a reply without one gets none."""
    import httpx

    _no_agent_settings(monkeypatch)
    net = _game(tmp_path, functions=1)
    m = agents.Manager(net, tmp_path, URL)
    m.prompt = lambda s: "task"
    m.prepare_session = lambda *a, **kw: None  # This test supplies its own prompt/claim script.
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "per_session": 1,
                                                       "context_tokens": 32768})
    s.stop = Quick()
    monkeypatch.setattr(agents.tools, "call", lambda net, agent, slug, name, args: "## CLAIMED: game/f0")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["messages"])
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{len(sent)}", "type": "function",
                                                                     "function": {"name": "claim_function", "arguments": "{}"}}]}
        if len(sent) == 1:
            msg["reasoning_content"] = "The smallest function first."
        elif len(sent) == 3:
            msg = {"role": "assistant", "content": "done"}
        return httpx.Response(200, json={"choices": [{"message": msg}]})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    m._run(s)
    turns = [x for x in sent[2] if x["role"] == "assistant"]
    assert turns[0]["reasoning_content"] == "The smallest function first." and "reasoning_content" not in turns[1]



def test_the_api_helper_asks_again_after_a_dropped_request_keeping_its_conversation(tmp_path, monkeypatch):
    """#154: behind a laggy gateway, one 502 or dropped connection ended the session, and the next one started the
    function over, again and again. They're asked again in place, after a wait, with the same conversation."""
    import httpx

    _no_agent_settings(monkeypatch)
    net = _game(tmp_path, functions=1)
    m = agents.Manager(net, tmp_path, URL)
    m.prompt = lambda s: "task"
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "per_session": 1,
                                                       "context_tokens": 32768})
    s.stop = Quick()
    monkeypatch.setattr(agents.tools, "call", lambda net, agent, slug, name, args: "## CLAIMED: game/f0")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["messages"])
        if len(sent) == 2:
            return httpx.Response(502, text="Bad Gateway")
        if len(sent) == 3:
            raise httpx.ReadTimeout("the gateway took too long")
        if len(sent) > 4:
            s.stop.set()
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{len(sent)}", "type": "function", "function": {"name": "claim_function", "arguments": "{}"}}]}}]})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    m._run(s)
    assert sent[1] == sent[2] == sent[3] and len(sent[1]) > len(sent[0])
    assert s.stop.waits[:2] == [10, 20] and s.runs == 1

def test_each_models_cost_per_match_cheapest_first(tmp_path, quiet):
    """The Agents view says what each model cost on a game, to tell which models to run more of: cheapest per match
    first, models that matched nothing last."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    with net.store.tx() as db:
        for model, usd, tokens, matches in (("claude-opus-5.5", 3.0, 0, 10), ("deepseek-v4.1-flash", 0.5, 0, 10),
                                            ("muse-1.3", 1.0, 0, 0), ("local-qwen", 0.0, 900_000, 9)):
            db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, usd, tokens, "
                       "matches) VALUES(1, 'game', 'api', ?, 'a', 0, ?, ?, ?)", (model, usd, tokens, matches))
    # a model counted only in tokens isn't the cheapest: it comes after the priced ones
    assert [r["model"] for r in m.costs("game")] == ["deepseek-v4.1-flash", "claude-opus-5.5", "local-qwen", "muse-1.3"]
    assert m.costs("other") == []
    assert all(r["advice"] == "" for r in m.costs("game"))  # long ago, and no model spinning


def test_a_model_spinning_on_a_game_gets_advice_to_weigh_not_an_order(tmp_path, quiet):
    """An Xbox game in one night: one model's helpers compiled 5,741 times for 10 matches ($138), another's 1,184
    times for 26 ($199). Nothing told the user the first was spinning."""
    import time

    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    now = time.time()
    with net.store.tx() as db:
        for model, usd, compiles, matches, at in (("claude-haiku", 138.1, 5741, 10, now - 3600),
                                                  ("claude-opus", 199.3, 1184, 26, now - 3600),
                                                  ("claude-haiku", 50.0, 9000, 400, now - 3 * 86400)):  # long ago
            db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, usd, compiles, "
                       "matches) VALUES(1, 'game', 'claude', ?, 'a', ?, ?, ?, ?)", (model, at, usd, compiles, matches))
    rows = {r["model"]: r for r in m.costs("game")}
    said = rows["claude-haiku"]["advice"]
    assert rows["claude-haiku"]["advice_model"] == "claude-haiku"  # the GUI dismisses this model's advice by name
    assert said.startswith("In the last day claude-haiku's helpers here made 5,741 compiles for 10 matches, $13.81 a match")
    assert "claude-opus's needed about 46 compiles a match, $7.67 a match" in said
    assert "may be past what claude-haiku does well: claude-opus could get further, for less" in said
    assert "You might give claude-haiku fewer helpers here." in said
    assert rows["claude-opus"]["advice"] == "" and rows["claude-opus"]["advice_model"] == ""
    # one model alone: no comparison, a stronger one in general; a model that does fine alone: nothing
    alone = agents.spin_advice([{"kind": "claude", "model": "claude-haiku", "usd": 30.0, "compiles": 900, "matches": 0}])
    assert alone[("claude", "claude-haiku")].endswith("for no matches. What's left of this game may be past what "
                                                      "claude-haiku does well: a stronger model could get further.")
    assert agents.spin_advice([{"kind": "api", "model": "m", "usd": 0, "compiles": 900, "matches": 30}]) == {}
    assert agents.spin_advice([{"kind": "api", "model": "m", "usd": 0, "compiles": 150, "matches": 0}]) == {}  # too few


def test_polishing_sessions_dont_make_a_model_look_like_its_spinning(tmp_path, quiet):
    """A model run only to polish an Xbox game was told its 1,282 compiles of a day had made no match: polishing
    compiles matched code and matches nothing new. Sessions keep their helper's task, those from before it was kept
    too, and only decompiling ones count for the advice."""
    import time

    net = _game(tmp_path)
    now = time.time()
    with net.store.tx() as db:
        db.execute(agents.SCHEMA[0])  # helpers
        db.execute("DROP TABLE IF EXISTS helper_sessions")
        db.execute(agents.SCHEMA[1].replace(", task TEXT NOT NULL DEFAULT ''", ""))  # before sessions kept their task
        db.execute("INSERT INTO helpers(id, project, kind, model, name, label, options, state) VALUES "
                   "(1, 'game', 'claude', 'sonnet', 'claude-sonnet-1', 's', '{\"model\": \"sonnet\", \"task\": \"readable\"}', "
                   "'stopped'), (2, 'game', 'claude', 'opus', 'claude-opus-1', 'o', '{\"model\": \"opus\"}', 'stopped'), "
                   "(4, 'game', 'claude', 'haiku', 'claude-haiku-1', 'h', '', 'stopped')")  # options no JSON: none
        for helper, model, agent, usd, compiles, matches in (
                (1, "sonnet", "claude-sonnet-1", 20.0, 1282, 0), (2, "opus", "claude-opus-1", 104.4, 650, 10),
                (4, "haiku", "claude-haiku-1", 1.0, 10, 0)):
            db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, usd, compiles, "
                       "matches) VALUES(?, 'game', 'claude', ?, ?, ?, ?, ?, ?)", (helper, model, agent, now - 3600, usd,
                                                                                  compiles, matches))
    m = agents.Manager(net, tmp_path, URL)
    assert {r["model"]: r["task"] for r in net.store.all("SELECT model, task FROM helper_sessions")} == \
        {"sonnet": "readable", "opus": "", "haiku": ""}
    rows = {r["model"]: r for r in m.costs("game")}
    assert rows["sonnet"]["advice"] == "" and rows["sonnet"]["compiles"] == 1282  # still in what it spent
    # a decompiling helper of the same model, spinning: that one counts
    with net.store.tx() as db:
        db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, compiles, matches) "
                   "VALUES(3, 'game', 'claude', 'sonnet', 'b', ?, 900, 0)", (now - 60,))
    said = {r["model"]: r for r in m.costs("game")}["sonnet"]["advice"]
    assert said.startswith("In the last day sonnet's helpers here made 900 compiles for no matches")


def test_a_session_says_the_task_its_helper_was_started_with(tmp_path, quiet):
    """The advice reads the task each session wrote when it began: a polishing helper's sessions say readable, a
    decompiling helper's say nothing. Every session inserted by hand, a _session_begin that wrote "" passed."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    for name, options, task in (("claude-opus-1", {"model": "opus", "task": "readable"}, "readable"),
                                ("claude-opus-2", {"model": "opus"}, "")):
        s = m._record("game", "claude", name, "opus", options)
        row = m._session_begin(s)
        assert net.store.one("SELECT task FROM helper_sessions WHERE id=?", row)["task"] == task
    assert [r["task"] for r in net.store.all("SELECT task FROM helper_sessions ORDER BY id")] == ["readable", ""]


def test_a_reused_helper_id_doesnt_give_an_old_session_another_helpers_task(tmp_path, quiet):
    """helpers.id has no AUTOINCREMENT, so a removed helper's id goes to the next helper: matching an old session by
    id alone made a sonnet session stand as a new polishing helper's, hiding the sonnet helper's spinning. A session
    matches its helper by id, project and name."""
    net = _game(tmp_path)
    now = time.time()
    with net.store.tx() as db:
        db.execute(agents.SCHEMA[0])  # helpers
        db.execute("DROP TABLE IF EXISTS helper_sessions")
        db.execute(agents.SCHEMA[1].replace(", task TEXT NOT NULL DEFAULT ''", ""))  # before sessions kept their task
        db.execute("INSERT INTO helpers(id, project, kind, model, name, label, options, state) VALUES "
                   "(1, 'game', 'claude', 'haiku', 'claude-haiku-1', 'h', '{\"model\": \"haiku\"}', 'stopped'), "
                   "(2, 'game', 'claude', 'sonnet', 'claude-sonnet-1', 's', '{\"model\": \"sonnet\"}', 'stopped')")
        db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, compiles, matches) "
                   "VALUES(2, 'game', 'claude', 'sonnet', 'claude-sonnet-1', ?, 900, 0)", (now - 3600,))
        db.execute("DELETE FROM helpers WHERE id=2")  # its record is removed
        rid = db.execute("INSERT INTO helpers(project, kind, model, name, label, options, state) VALUES "
                         "('game', 'claude', 'opus', 'claude-opus-1', 'o', '{\"model\": \"opus\", \"task\": "
                         "\"readable\"}', 'stopped')").lastrowid
        assert rid == 2  # SQLite gives the removed helper's id to the new polishing helper
    m = agents.Manager(net, tmp_path, URL)
    assert net.store.one("SELECT task FROM helper_sessions WHERE model='sonnet'")["task"] == ""
    said = {r["model"]: r for r in m.costs("game")}["sonnet"]["advice"]
    assert said.startswith("In the last day sonnet's helpers here made 900 compiles for no matches")


def test_a_session_whose_helper_is_gone_has_no_task_to_take(tmp_path, quiet):
    """A session from before tasks were kept whose helper's record was removed has none to take: it stays blank, so
    it counts as decompiling rather than borrowing another helper's task."""
    net = _game(tmp_path)
    now = time.time()
    with net.store.tx() as db:
        db.execute(agents.SCHEMA[0])  # helpers
        db.execute("DROP TABLE IF EXISTS helper_sessions")
        db.execute(agents.SCHEMA[1].replace(", task TEXT NOT NULL DEFAULT ''", ""))  # before sessions kept their task
        db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, compiles, matches) "
                   "VALUES(7, 'game', 'claude', 'sonnet', 'claude-sonnet-1', ?, 900, 0)", (now - 3600,))
    agents.Manager(net, tmp_path, URL)
    assert net.store.one("SELECT task FROM helper_sessions")["task"] == ""


def test_codex_and_api_helpers_take_an_effort_too(tmp_path, monkeypatch):
    """Codex gets it as its model_reasoning_effort setting (from the model's name, as Claude Code and opencode);
    an API helper, whose model is its endpoint's, as the `effort` option, sent as OpenAI's reasoning_effort."""
    codex = kinds.get("codex")
    monkeypatch.setattr("decompnet.agents.codex.codex_config", lambda: {})
    cmd, _ = codex.session_command("codex", tmp_path, URL, "token", "gpt-5#xhigh")
    assert cmd[-5:] == ["-c", 'model_reasoning_effort="xhigh"', "-m", "gpt-5", "-"]
    cmd, _ = codex.session_command("codex", tmp_path, URL, "token", 'gpt-5#"high"')  # its own words only
    assert not any("model_reasoning_effort" in c for c in cmd)
    api = kinds.get("api")
    assert api.describe()["takes_effort"] and api.describe()["efforts"] == ["minimal", "low", "medium", "high", "xhigh", "max"]


def test_an_api_helper_asks_its_model_for_the_effort_it_was_given(tmp_path, monkeypatch):
    import httpx

    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    m.prompt = lambda s: "task"
    m.prepare_session = lambda *a, **kw: None  # This test supplies its own prompt/claim script.
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    for effort in ("high", None):
        options = {"base_url": "http://mock/v1", "model": "m", "context_tokens": 32768} | ({"effort": effort} if effort else {})
        s = m._record("game", "api", f"api-m-{effort}", "box #1", options)
        s.stop = Quick()
        m._run(s)
        assert sent[-1].get("reasoning_effort") == effort  # none at all without one: the model's default


def test_worker_selection_is_editable_and_restored_without_changing_other_workers(tmp_path, quiet):
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (a,) = m.start("claude", "game", options={"model": "opus", "selection": {"strategy": "hardest"}})
    (b,) = m.start("codex", "game", options={"selection": {"strategy": "untried"}})
    m.set_selection(a, {"strategy": "closest", "match_below": 85})
    assert m.sessions[a].snapshot()["selection"] == {"strategy": "closest", "match_below": 85.0}
    assert net.selection("game", m.sessions[b].agent_name)["strategy"] == "untried"
    later = agents.Manager(net, tmp_path, URL)
    assert later.sessions[a].options["selection"] == {"strategy": "closest", "match_below": 85.0}
    assert later.sessions[b].options["selection"]["strategy"] == "untried"


def test_empty_filtered_queue_pauses_before_spending_on_a_session(tmp_path, monkeypatch):
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = m._record("game", "claude", "claude-1", "Claude", {"selection": {"match_below": 0}})
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=1")
    m._run(s)
    assert s.state == "paused" and "below 0%" in s.why
    assert s.runs == 0


def test_session_size_changes_are_validated_persisted_and_exposed(tmp_path):
    net = _game(tmp_path)
    manager = agents.Manager.__new__(agents.Manager)
    manager.net, manager._lock = net, threading.RLock()
    session = agents.Session('1', 'codex', 'model', 'game', 'worker', {'per_session': 8})
    manager.sessions = {session.id: session}
    with net.store.tx() as db:
        for statement in agents.SCHEMA:
            db.execute(statement)
        db.execute('INSERT INTO helpers(id,project,kind,model,name,label,options,state) VALUES(?,?,?,?,?,?,?,?)',
                   (session.id, 'game', 'codex', 'model', 'worker', 'worker', '{}', 'working'))
    manager.set_selection(session.id, {'strategy': 'least_tried'}, 12)
    assert session.snapshot()['per_session'] == 12
    row = net.store.one('SELECT options FROM helpers WHERE id=?', session.id)
    assert json.loads(row['options'])['per_session'] == 12
    for invalid in (0, 21, True, 2.5, '8'):
        with pytest.raises(ValueError, match='whole number'):
            manager.set_selection(session.id, {}, invalid)
    assert session.options['per_session'] == 12
    assert net.selection('game', session.agent_name)['strategy'] == 'least_tried'


def test_model_costs_separate_cached_and_uncached_tokens(tmp_path, quiet):
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    with net.store.tx() as db:
        db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,input_tokens,"
                   "cache_read_tokens,cache_write_tokens,output_tokens,tokens) VALUES(1,'game','api','local','a',0,100,900,20,50,1070)")
    row = m.costs('game')[0]
    assert [row[k] for k in ('input_tokens','cache_read_tokens','cache_write_tokens','output_tokens','tokens')] == [100,900,20,50,1070]


def test_removed_helper_identity_and_statistics_are_not_reused(tmp_path, quiet):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    old = manager.start('codex', 'game', options={'model': 'gpt-6-luna'})[0]
    original = manager.sessions[old]
    token = net.register_agent(original.agent_name)
    agent = net.authenticate(token)
    net.store.conn.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at) VALUES(?,?,?,?,?,0)",
                         (old, 'game', 'codex', 'gpt-6-luna', original.agent_name))
    manager.remove(old)
    new = manager.start('codex', 'game', options={'model': 'gpt-6-luna'})[0]
    assert new != old
    assert manager.sessions[new].agent_name != agent.name
    assert manager.sessions[new].tokens == 0


def test_helper_rename_persists_without_changing_identity(tmp_path, quiet):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid = manager.start('codex', 'game')[0]
    original = manager.sessions[sid].agent_name
    assert manager.rename(sid, '  Library hunter  ') == 'Library hunter'
    assert manager.sessions[sid].agent_name == original
    restored = agents.Manager(net, tmp_path, URL)
    assert restored.sessions[sid].label == 'Library hunter'
    for invalid in ['', 'x' * 81, 'bad\nname', None]:
        with pytest.raises(ValueError):
            manager.rename(sid, invalid)
    with pytest.raises(ValueError, match='no longer exists'):
        manager.rename('missing', 'Name')


def test_codex_api_equivalent_uses_separate_token_rates():
    from decompnet.agents.codex import CODEX
    usage = {'input': 1_000_000, 'cache_read': 2_000_000, 'output': 100_000, 'tokens': 3_100_000}
    assert CODEX.estimate_cost('gpt-6-luna#high', usage) == pytest.approx(.17)
    assert CODEX.estimate_cost('unknown', usage) is None
    assert CODEX.estimate_cost('gpt-6-luna', {'tokens': 100}) is None
    assert CODEX.estimate_cost('gpt-6-luna', {}) is None


def test_helper_cost_estimate_survives_restart_without_double_counting(tmp_path, quiet):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid = manager.start('codex', 'game', options={'model': 'gpt-6-luna'})[0]
    s = manager.sessions[sid]
    agent = net.authenticate(net.register_agent(s.agent_name))
    row = manager._session_begin(s)
    manager.account(s, {'input': 1_000_000, 'cache_read': 2_000_000, 'output': 100_000})
    assert manager.snapshot()[0]['estimated_cost'] == pytest.approx(.17)
    manager._session_end(s, row, agent, 0, 'done')
    assert manager.snapshot()[0]['estimated_cost'] == pytest.approx(.17)
    empty = manager._session_begin(s)
    manager._session_end(s, empty, agent, 0, 'failed before usage')
    restored = agents.Manager(net, tmp_path, URL)
    assert restored.snapshot()[0]['estimated_cost'] == pytest.approx(.17)
    # A legacy record with uncategorized usage must not look cheaper than it was.
    restored.sessions[sid].tokens += 50
    assert restored.snapshot()[0]['estimated_cost'] is None


def test_reset_metrics_keeps_history_and_counts_new_work_across_restart(tmp_path, quiet):
    net = _game(tmp_path, functions=2)
    manager = agents.Manager(net, tmp_path, URL)
    sid = manager.start('codex', 'game', options={'model': 'gpt-6-luna'})[0]
    s = manager.sessions[sid]
    agent = net.authenticate(net.register_agent(s.agent_name))
    session = manager._session_begin(s)
    manager.account(s, {'input': 1000000})
    def attempt(fn, score, stamp):
        with net.store.tx() as db:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,1,?)',
                       (fn, agent.id, score, stamp))
            if score == 0:
                db.execute("UPDATE functions SET status='matched', matched_agent_id=? WHERE id=?", (agent.id, fn))
    attempt(1, 100, 100)
    manager.reset_metrics(sid)
    row = manager.snapshot()[0]
    assert row['tokens'] == row['runs'] == row['metrics']['compiles'] == row['metrics']['improved'] == 0
    assert row['metrics']['last_improvement'] is None
    assert row['estimated_cost'] == 0
    assert row['lifetime_tokens'] == 1000000
    # Ties and worse attempts after reset do not become "improvements".
    attempt(1, 100, 101)
    attempt(1, 120, 102)
    attempt(1, 50, 103)
    attempt(2, 0, 104)
    manager.account(s, {'input': 1000000})
    manager._session_end(s, session, agent, 0, 'done')
    manager._metrics_cache = {}
    row = manager.snapshot()[0]
    assert row['tokens'] == 1000000 and row['lifetime_tokens'] == 2000000
    assert row['metrics']['compiles'] == 4
    assert row['metrics']['worked'] == 2
    assert row['metrics']['improved'] == 2 and row['metrics']['matched'] == 1
    assert row['metrics']['last_improvement'] == 104
    assert row['estimated_cost'] == pytest.approx(kinds.get('codex').estimate_cost('gpt-6-luna', {'input':1000000, 'tokens':1000000}))
    restored = agents.Manager(net, tmp_path, URL)
    again = restored.snapshot()[0]
    assert again['tokens'] == row['tokens'] and again['metrics'] == row['metrics']
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n'] == 5
    after = row['metrics_after']
    assert [f['name'] for f in net.agent_results('game', s.agent_name, 'matched', after=after)['functions']] == ['f1']
    assert {f['name'] for f in net.agent_results('game', s.agent_name, 'improved', after=after)['functions']} == {'f0','f1'}
    assert {f['name'] for f in net.agent_results('game', s.agent_name, 'worked', after=after)['functions']} == {'f0','f1'}
    manager.reset_metrics(sid)
    assert manager.snapshot()[0]['metrics']['worked'] == 0
    assert manager.snapshot()[0]['tokens'] == 0
    with pytest.raises(ValueError, match='no longer exists'):
        manager.reset_metrics('missing')


@pytest.mark.parametrize('policy',[{}, {'match_below':100}])
def test_paused_unchanged_queue_waits_without_starting_paid_session(tmp_path,monkeypatch,policy):
    from decompnet import claim_stalls
    _no_agent_settings(monkeypatch)
    net=_game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){}',best_score=10")
    m=agents.Manager(net,tmp_path,URL)
    s=m._record('game','claude','claude-1','Claude',{'selection':policy})
    worker,_=net.local_agent(s.agent_name)
    for fn in net.store.all('SELECT * FROM functions'):
        claim_stalls.released(net,fn,exhausted=True,agent=worker)
    waits=[]
    def wait(session,why,*args):
        waits.append(why);session.stop.set()
    monkeypatch.setattr(m,'_wait',wait)
    monkeypatch.setattr(m,'_run_cli',lambda *args:pytest.fail('No provider call while all candidates are paused'))
    m._run(s)
    assert waits and 'paused' in waits[0] and s.runs==0


def test_invalid_kimi_authorization_grant_uses_bounded_signin_retries(tmp_path,monkeypatch):
    monkeypatch.setattr(agents.Manager, "_preflight_tools", lambda *a, **kw: None)
    monkeypatch.setattr(agents.Manager, "prepare_session", lambda *a, **kw: None)
    _no_agent_settings(monkeypatch)
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    session=manager._record('game','kimi','kimi-1','kimi-1',{})
    session.stop=Quick()
    message='The provided authorization grant is invalid'
    script=f'import sys; print({message!r},file=sys.stderr,flush=True); sys.exit(1)'
    monkeypatch.setattr(manager,'_command',lambda *a:([sys.executable,'-c',script],dict(os.environ),''))
    manager._run(session)
    assert session.state=='paused' and 'sign in again' in session.why
    assert session.runs==agents.AUTH_RETRIES
    assert message in session.why


def test_termination_reason_is_saved_in_transcript():
    s = agents.Session('x','claude','worker','game','worker',{})
    recorded = []
    s.record = lambda kind,text: recorded.append((kind,text))
    s.push('termination','App requested termination: user stopped this helper')
    assert recorded == [('termination','App requested termination: user stopped this helper')]


def test_zcode_stream_json_is_read():
    """zcode's --output-format stream-json: reasoning and text in deltas, a tool call, a failed tool result,
    turn.failed, and the final result whose inputTokens include the cached input and cache writes."""
    state: dict = {}
    lines = [
        {"type": "session.created", "sessionId": "sess_1", "payload": {"mode": "yolo"}},
        {"type": "model.streaming", "payload": {"kind": "reasoning_delta", "delta": "The user wants"}},
        {"type": "model.streaming", "payload": {"kind": "reasoning_delta", "delta": " to claim."}},
        {"type": "model.streaming", "payload": {"kind": "reasoning_end"}},
        {"type": "model.streaming", "payload": {"kind": "text_delta", "delta": "Claiming "}},
        {"type": "model.streaming", "payload": {"kind": "text_delta", "delta": "the next one."}},
        {"type": "model.streaming", "payload": {"kind": "text_end"}},
        {"type": "model.streaming", "payload": {"kind": "tool_call", "toolName": "mcp__decomp__claim_function",
                                                "input": {"count": 4}}},
        {"type": "tool.updated", "payload": {"kind": "result", "result": {"success": False,
                                                                           "error": "tool exploded"}}},
        {"type": "turn.failed", "payload": {"error": {"message": "usage limit reached"}}},
        {"type": "result", "sessionId": "sess_1", "usage": {"inputTokens": 100, "outputTokens": 10,
                                                            "cacheReadTokens": 40, "cacheWriteTokens": 10,
                                                            "modelRequestCount": 2}},
    ]
    out = [item for line in lines for item in kinds.get("zcode").parse_line(json.dumps(line), state)]
    assert out == [("think", "The user wants to claim."), ("text", "Claiming the next one."),
                   ("tool", "claim_function(4)"), ("error", "tool exploded"), ("error", "usage limit reached"),
                   ("usage", json.dumps({"input": 50, "output": 10, "cache_read": 40, "cache_write": 10,
                                         "turns": 2}))]
    assert state["zcode_session"] == "sess_1"
    assert kinds.get("zcode").parse_line(json.dumps({"type": "session.updated", "payload": {}})) == []
    assert kinds.get("zcode").clean_stderr("Error: Model access is not configured. Run /login.") == \
        "Model access is not configured. Run /login."
    assert kinds.get("zcode").fatal("Unsupported reasoning effort: xhigh")



def test_zcode_prompt_reaches_it_by_file_never_as_argv(tmp_path, monkeypatch):
    """zcode has no stdin prompt: its prompt is a Markdown file the session attaches, so the text is never an
    argument (and zcode cannot parse it as a slash command). Its settings file names only the app's MCP server."""
    m = _manager(tmp_path, monkeypatch, "zcode")
    s = agents.Session("zcode-1", "zcode", "z", "demo", "zcode-1",
                       {"model": "account:zai-start-plan/GLM-5.3#high"})
    work = tmp_path / "zcode"
    work.mkdir()
    cmd, env, stdin = m._command(s, "tok", work)
    prompt = m.prompt(s)
    assert stdin is None  # nothing for stdin: the prompt is in the attached file
    assert (work / zcode.PROMPT_FILE).read_text(encoding="utf-8") == prompt
    assert not any(prompt in arg or "line one" in arg for arg in cmd)
    assert cmd[cmd.index("--attach") + 1] == str(work / zcode.PROMPT_FILE)
    assert cmd[cmd.index("-p") + 1] == zcode.PROMPT_TEXT
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--mode") + 1] == "yolo"
    denied = set(cmd[cmd.index("--disallowed-tools") + 1].split())
    assert {"Read", "Write", "Edit", "Bash", "WebFetch", "WebSearch"} <= denied
    assert cmd[cmd.index("--model") + 1] == "account:zai-start-plan/GLM-5.3"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert not any("tok" in arg for arg in cmd)
    settings = json.loads((work / zcode.SETTINGS_FILE).read_text(encoding="utf-8"))
    assert list(settings["mcp"]["servers"]) == [zcode.MCP_SERVER]
    assert settings["mcp"]["servers"][zcode.MCP_SERVER] == {
        "type": "http", "url": "http://127.0.0.1:6464/mcp",
        "headers": {"Authorization": "Bearer tok"}}
    assert settings["plugins"]["enabled"] is False and settings["skills"]["enabled"] is False
    assert env["ZCODE_CLI_SETTINGS_FILE"] == str(work / zcode.SETTINGS_FILE)



def test_zcode_refuses_a_prompt_it_cannot_attach(tmp_path, monkeypatch):
    """Past zcode's 25,000-token / 256 KiB attachment caps it stops inlining the prompt and gives the model a
    path placeholder or a partial view; Read is disallowed, so the helper would work without its instructions.
    Just under the cap the prompt is written and attached; over it, the session start fails before writing."""
    under = "x" * (zcode.ATTACHMENT_TOKENS * zcode.TOKEN_CHARS - zcode.TOKEN_CHARS)
    over = "x" * (zcode.ATTACHMENT_TOKENS * zcode.TOKEN_CHARS + zcode.TOKEN_CHARS)
    assert zcode.estimated_tokens(under) == zcode.ATTACHMENT_TOKENS - 1
    assert zcode.estimated_tokens(over) == zcode.ATTACHMENT_TOKENS + 1
    under_work = tmp_path / "zcode-under"
    under_work.mkdir()
    path = zcode.ZCODE.prompt_file(under_work, under)
    assert path == under_work / zcode.PROMPT_FILE and path.read_text(encoding="utf-8") == under
    cmd, _ = zcode.ZCODE.session_command("zcode", under_work, "http://127.0.0.1:1/mcp", "tok", "")
    assert cmd[cmd.index("--attach") + 1] == str(path)
    over_work = tmp_path / "zcode-over"
    over_work.mkdir()
    with pytest.raises(ValueError, match="too long for zcode"):
        zcode.ZCODE.prompt_file(over_work, over)
    assert not (over_work / zcode.PROMPT_FILE).exists()
    # the session start itself raises Fatal with the same sentence, before a process is launched
    m = _manager(tmp_path, monkeypatch, "zcode")
    m.prompt = lambda session: over
    command_work = tmp_path / "zcode-command"
    command_work.mkdir()
    with pytest.raises(agents.Fatal, match="too long for zcode"):
        m._command(agents.Session("zcode-1", "zcode", "z", "demo", "zcode-1", {}), "tok", command_work)
    assert not any(command_work.iterdir())  # no prompt, no settings: the session start wrote nothing



def test_zcode_refuses_a_prompt_over_its_byte_cap(tmp_path, monkeypatch):
    """The 256 KiB cap is separate from the token cap: zcode would read only a 2000-line partial view."""
    monkeypatch.setattr(zcode, "ATTACHMENT_TOKENS", 10**9)  # isolate the byte guard
    work = tmp_path / "zcode-bytes"
    work.mkdir()
    with pytest.raises(ValueError, match="too long for zcode"):
        zcode.ZCODE.prompt_file(work, "x" * (zcode.ATTACHMENT_BYTES + 1))
    assert not (work / zcode.PROMPT_FILE).exists()


def test_rejected_codex_model_pauses_siblings_before_a_provider_session(tmp_path, monkeypatch):
    from decompnet.app import session_followups
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    first = manager._record('game', 'codex', 'codex-one', 'first', {'model':'unavailable'})
    second = manager._record('game', 'codex', 'codex-two', 'second', {'model':'unavailable'})
    reason = 'The unavailable model is not supported when using Codex with a ChatGPT account. model_not_found'
    assert kinds.get('codex').fatal(reason)
    assert not kinds.get('codex').fatal('Model metadata not found. Defaulting to fallback metadata.')
    assert not kinds.get('codex').fatal('connection reset; retry later')
    def rejected(*args):
        raise agents.Fatal(reason)
    monkeypatch.setattr(manager, '_run_cli', rejected)
    manager._run(first)
    assert first.state == 'paused' and first.runs == 1 and first.configuration_error
    monkeypatch.setattr(manager, '_run_cli', lambda *a: pytest.fail('Rejected configuration must not launch'))
    manager._run(second)
    assert second.state == 'paused' and second.runs == 0
    assert second.snapshot()['configuration_error'] and 'Choose another model' in second.todo
    changed = manager._record('game', 'codex', 'codex-three', 'third', {'model':'different'})
    assert session_followups.configuration_error(manager, changed) is None


def test_polishing_start_and_restart_skip_matching_preparation(tmp_path, quiet, monkeypatch):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    calls = []
    monkeypatch.setattr(manager, '_prepare', lambda slug: calls.append(slug))
    sid = manager.start('codex', 'game', options={'task': 'readable'})[0]
    assert manager.sessions[sid].state == 'starting'
    assert calls == []
    manager.sessions[sid].state = 'stopped'
    manager.restart(sid)
    assert calls == []
    manager.sessions[sid].state = 'stopped'
    manager.sessions[sid].stopped_by = agents.BY_QUIT
    assert manager.resume() == [sid]
    assert calls == []
    manager.start('codex', 'game')
    assert calls == ['game']


def test_polishing_does_not_wait_for_matching_jobs(tmp_path, quiet, monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import focused_agents
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid = manager.start('codex', 'game', options={'task': 'readable'})[0]
    class MatchingWait:
        def is_set(self):
            return False
        def wait(self, timeout):
            pytest.fail('Polishing waited for unrelated matching preparation')
    manager._prep['game'] = MatchingWait()
    monkeypatch.setattr(net.twins, 'jobs', {'game': SimpleNamespace(ordered=MatchingWait())})
    class Ready(Exception):
        pass
    def ready(*args):
        raise Ready()
    monkeypatch.setattr(focused_agents, 'ready', ready)
    with pytest.raises(Ready):
        manager._run_work(manager.sessions[sid])


def test_restart_retains_conversation_and_claim_handoff(tmp_path, quiet):
    from decompnet.app import session_recovery
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid, = manager.start('codex', 'game', 1, {})
    session = manager.sessions[sid]
    conversation = '12345678-1234-1234-1234-123456789abc'
    assert session_recovery.conversation(session, conversation)
    assert not session_recovery.conversation(session, 'invalid cli output')
    worker, _ = net.local_agent(session.agent_name)
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES((SELECT id FROM functions WHERE name='f0'),?,?,?)", (worker.id,0,1e12))
    manager._save(session)
    restored = agents.Manager(net, tmp_path, URL).sessions[sid]
    assert restored.options['resume_conversation'] == conversation
    assert restored.options['resume_functions'] == ['f0']
    assert 'reacquire' in session_recovery.handoff(restored)
    assert not net.store.one('SELECT 1 FROM claims')
    session_recovery.completed(restored, net)
    assert 'resume_conversation' not in restored.options and 'resume_functions' not in restored.options


def test_api_conversation_checkpoint_is_scoped_and_cleared_after_completion(tmp_path):
    from decompnet.app import session_recovery
    net = _game(tmp_path)
    session = agents.Session('1','api','test','game','api-1',{})
    messages = [{'role':'system','content':'rules'}, {'role':'user','content':'work'},
                {'role':'assistant','content':'next experiment'}]
    session_recovery.api_messages(net, session, ['local','model'], messages)
    assert session_recovery.api_messages(net, session, ['local','model']) == messages
    assert session_recovery.api_messages(net, session, ['other','model']) is None
    session_recovery.completed(session, net)
    assert session_recovery.api_messages(net, session, ['local','model']) is None


def test_ordinary_codex_command_persists_and_resumes_exact_conversation(tmp_path, quiet, monkeypatch):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    monkeypatch.setattr(agents, '_which', lambda command:command)
    monkeypatch.setattr(manager, 'prompt', lambda session:'work instructions')
    session = agents.Session('1','codex','test','game','codex-1',{})
    work = tmp_path/'worker'
    work.mkdir()
    initial, _, _ = manager._command(session,'secret',work)
    assert '--ephemeral' not in initial
    sid = '12345678-1234-1234-1234-123456789abc'
    session.options.update(resume_conversation=sid, resume_functions=['f0'])
    resumed, _, prompt = manager._command(session,'new-secret',work)
    assert resumed[-3:] == ['resume',sid,'-']
    assert 'f0' in prompt and 'reacquire' in prompt and 'Resume this conversation' in prompt


def test_retry_paused_releases_worker_registry_before_scanning(tmp_path, quiet, monkeypatch):
    from decompnet.app import retry_paused
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    session = manager._record('game', 'codex', 'retry-worker', 'Worker', {})
    def retry(*args, **kwargs):
        acquired = threading.Event()
        def read_registry():
            with manager._lock:
                acquired.set()
        thread = threading.Thread(target=read_registry)
        thread.start()
        assert acquired.wait(2), 'Retry held the registry while scanning candidates'
        thread.join()
        return {'resumed': 0}
    monkeypatch.setattr(retry_paused, 'retry', retry)
    assert manager.retry_paused(session.id) == {'resumed': 0}


@pytest.mark.parametrize('fails',[False,True])
def test_retry_paused_keeps_running_worker_credentials(tmp_path,quiet,monkeypatch,fails):
    from decompnet.app import retry_paused
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    session=manager._record('game','codex','retry-token-worker','Worker',{})
    original,token=net.local_agent(session.agent_name)
    before=net.store.one('SELECT token_hash FROM agents WHERE id=?',original.id)['token_hash']
    def retry(network,slug,worker,limit,**kwargs):
        assert worker==original and slug=='game' and limit==3
        assert network.authenticate(token)==original
        if fails:raise ValueError('Synthetic scan failure')
        return {'resumed':1}
    monkeypatch.setattr(retry_paused,'retry',retry)
    if fails:
        with pytest.raises(ValueError,match='Synthetic scan failure'):manager.retry_paused(session.id,3)
    else:
        assert manager.retry_paused(session.id,3)=={'resumed':1}
    assert net.authenticate(token)==original
    assert net.store.one('SELECT token_hash FROM agents WHERE id=?',original.id)['token_hash']==before


def test_retry_paused_without_existing_runner_does_not_issue_credentials(tmp_path,quiet,monkeypatch):
    from decompnet.app import retry_paused
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    session=manager._record('game','codex','retry-credit-only','Worker',{})
    monkeypatch.setattr(net,'local_agent',lambda *a:pytest.fail('Retry must not issue runner credentials'))
    seen=[]
    monkeypatch.setattr(retry_paused,'retry',lambda n,p,a,*args,**kwargs:seen.append(a) or {'resumed':0})
    assert manager.retry_paused(session.id)=={'resumed':0}
    row=net.store.one('SELECT id,token_hash FROM agents WHERE name=?',session.agent_name)
    assert seen[0].id==row['id'] and row['token_hash'].startswith('attribution:')


def test_external_match_rotates_only_displaced_matching_worker(tmp_path,quiet,monkeypatch):
    net=_game(tmp_path,functions=2)
    manager=agents.Manager(net,tmp_path,URL)
    owner,_=net.local_agent('displaced')
    worker=agents.Session('1','codex','Worker','game','displaced',{'resume_functions':['f0'],'resume_conversation':'old'},state='working')
    other=agents.Session('2','codex','Other','game','other',{},state='working')
    polish=agents.Session('3','codex','Polish','game','displaced',{'task':'readable'},state='working')
    worker.proc=object();ended=[]
    monkeypatch.setattr(agents,'_terminate',lambda proc:ended.append(proc))
    manager.sessions={s.id:s for s in (worker,other,polish)}
    pid=net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    with net.store.tx() as db:db.execute("UPDATE functions SET status='matched' WHERE name='f0'")
    manager.matched_elsewhere(owner.id,'f0',pid)
    assert worker.rotated and not worker.stop.is_set()
    assert not other.rotated and not polish.rotated
    assert ended==[worker.proc]
    assert not worker.options.get('resume_functions') and not worker.options.get('resume_conversation')
    assert 'Claim a different' in worker.continuation_note
    assert net.claim_row(owner,'game',None)['name']=='f1'


def test_external_match_does_not_rotate_worker_already_on_another_function(tmp_path,quiet):
    net=_game(tmp_path,functions=2)
    manager=agents.Manager(net,tmp_path,URL)
    owner,_=net.local_agent('displaced')
    s=agents.Session('1','codex','Worker','game','displaced',{},state='working')
    manager.sessions={s.id:s}
    pid=net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    net.claim_row(owner,'game','f1')
    manager.matched_elsewhere(owner.id,'f0',pid)
    assert not s.rotated


def test_api_external_match_discards_obsolete_tools_but_accounts_response(tmp_path,monkeypatch):
    import httpx

    _no_agent_settings(monkeypatch)
    net=_game(tmp_path,functions=2)
    manager=agents.Manager(net,tmp_path,URL)
    manager.prompt=lambda s:'task'
    manager.prepare_session=lambda *a,**kw:None
    s=manager._record('game','api','api-m-1','API',{'base_url':'http://mock/v1','model':'m','context_tokens':32768})
    s.state='working'
    owner,_=net.local_agent(s.agent_name)
    net.claim_row(owner,'game','f0')
    pid=net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    calls=[]
    def handler(request):
        calls.append(request)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched' WHERE name='f0'")
            db.execute('DELETE FROM claims WHERE agent_id=?',(owner.id,))
        manager.matched_elsewhere(owner.id,'f0',pid)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[
            {'id':'old','type':'function','function':{'name':'compile_and_diff','arguments':'{}'}}]}}],
            'usage':{'prompt_tokens':100,'completion_tokens':5}})
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(agents.tools,'call',lambda *args:pytest.fail('Obsolete tool must not run'))
    assert kinds.get('api').run(manager,s,owner)
    assert s.rotated and len(calls)==1
    assert s.usage['tokens']==105


@pytest.mark.parametrize('path',['room','unit'])
def test_remote_and_imported_unit_matches_handoff_displaced_worker(tmp_path,quiet,monkeypatch,path):
    net=_game(tmp_path,functions=2)
    manager=agents.Manager(net,tmp_path,URL)
    owner,_=net.local_agent('displaced')
    s=agents.Session('1','codex','Worker','game','displaced',{},state='working')
    manager.sessions={s.id:s}
    net.claim_row(owner,'game','f0')
    monkeypatch.setattr(net,'on_match',lambda *args:None)
    if path=='room':net.apply_remote_match_named('game','f0','remote-solver')
    else:
        proj=net._project('game');row=net._function('f0','game')
        with net.store.tx() as db:net.forks._matched(db,proj,row,None,None)
    assert net._function('f0','game')['status']=='matched'
    assert not net.store.one('SELECT 1 FROM claims WHERE agent_id=?',owner.id)
    assert s.rotated and net.claim_row(owner,'game',None)['name']=='f1'


def test_reset_metrics_does_not_snapshot_other_agents_or_scan_project_results(tmp_path, quiet, monkeypatch):
    from decompnet.app import agent_metrics
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid = manager.start('codex','game',options={'model':'gpt-6-luna'})[0]
    monkeypatch.setattr(manager,'snapshot',lambda *a,**kw:pytest.fail('reset must not build all agent snapshots'))
    monkeypatch.setattr(agent_metrics,'results',lambda *a:pytest.fail('reset must not scan project attempts for card results'))
    reset = manager.reset_metrics(sid)
    assert reset['metrics_since'] > 0 and reset['metrics_after'] == 0
    assert manager.sessions[sid].options['_metrics']['at'] == reset['metrics_since']


def test_failed_metric_reset_preserves_previous_boundary(tmp_path, quiet, monkeypatch):
    net = _game(tmp_path)
    manager = agents.Manager(net,tmp_path,URL)
    sid = manager.start('codex','game')[0]
    previous = {'at':1,'attempt':0,'tokens':0}
    manager.sessions[sid].options['_metrics'] = previous
    def fail_save(session):raise OSError('save failed')
    monkeypatch.setattr(manager,'_save',fail_save)
    with pytest.raises(OSError,match='save failed'):manager.reset_metrics(sid)
    assert manager.sessions[sid].options['_metrics'] is previous


@pytest.mark.parametrize('version', ['1.19.5', 'opencode v2.0.28'])
@pytest.mark.parametrize('variant', ['', 'high', 'max'])
def test_opencode_reasoning_variant_uses_installed_cli_syntax(tmp_path, monkeypatch, version, variant):
    monkeypatch.setitem(kinds.known_versions, 'opencode', (0, version))
    manager=_manager(tmp_path,monkeypatch,'opencode')
    model='opencode/synthetic-model'+('#'+variant if variant else '')
    session=agents.Session('synthetic','opencode','o','demo','synthetic',{'model':model})
    cmd,env,_=manager._command(session,'private-token',tmp_path)
    legacy=version.startswith('1.')
    assert cmd[cmd.index('-m')+1]==('opencode/synthetic-model' if legacy else model)
    if legacy and variant:
        assert cmd[cmd.index('--variant')+1]==variant
    else:assert '--variant' not in cmd
    assert ('--standalone' in cmd) is (not legacy)
    assert env['OPENCODE_DB']==str((tmp_path/'opencode.db').resolve())
    assert session.options['model']==model
    assert 'private-token' not in str(cmd)
    config=json.loads((tmp_path/'opencode.json').read_text())
    servers=config['mcp'] if legacy else config['mcp']['servers']
    assert servers['decompdecomp']['headers']['Authorization']=='Bearer private-token'


@pytest.mark.parametrize('channel', ['stdout', 'stderr'])
def test_opencode_privacy_refusal_is_fatal_before_retry(tmp_path, monkeypatch, channel):
    manager=agents.Manager(RomNetwork(tmp_path/'app.db',tmp_path),tmp_path,'http://127.0.0.1:9/mcp')
    monkeypatch.setattr(manager,'_preflight_tools',lambda *args,**kwargs:True)
    monkeypatch.setattr(manager.net,'selection_counts',lambda *args,**kwargs:{'available':1})
    _,token=manager.net.local_agent('synthetic-opencode')
    message='This Go model trains on request data. Turn on "Allow paid endpoints that train on request data" to use it.'
    output=json.dumps({'type':'error','error':{'name':'APIError','data':{'message':message}}}) if channel=='stdout' else message
    code=f'import sys; print({output!r},file=sys.{channel}); raise SystemExit(1)'
    monkeypatch.setattr(manager,'_command',lambda *args:([sys.executable,'-c',code],dict(os.environ),''))
    session=agents.Session('synthetic-opencode','opencode','synthetic','game','synthetic-opencode',{'model':'opencode/synthetic'})
    with pytest.raises(agents.Fatal,match='trains on request data'):
        manager._run_cli(session,token,tmp_path)
    assert session.options['model']=='opencode/synthetic'


def test_opencode_privacy_refusal_pauses_same_configuration_siblings(tmp_path, monkeypatch):
    from decompnet.app import session_followups
    _no_agent_settings(monkeypatch)
    manager=agents.Manager(_game(tmp_path),tmp_path,URL)
    first=manager._record('game','opencode','one','one',{'model':'opencode/synthetic'})
    second=manager._record('game','opencode','two','two',{'model':'opencode/synthetic'})
    reason='APIError: This Go model trains on request data. Review provider privacy settings.'
    def rejected(*args):raise agents.Fatal(reason)
    monkeypatch.setattr(manager,'_run_cli',rejected)
    manager._run(first)
    assert first.state=='paused' and first.runs==1 and first.configuration_error
    monkeypatch.setattr(manager,'_run_cli',lambda *args:pytest.fail('A known policy refusal must not launch another request'))
    manager._run(second)
    assert second.state=='paused' and second.runs==0 and second.configuration_error
    assert 'Choose another model' in second.todo
    changed=manager._record('game','opencode','three','three',{'model':'opencode/different'})
    assert session_followups.configuration_error(manager,changed) is None


def test_opencode_temporary_failures_do_not_become_privacy_refusals():
    agent=kinds.get('opencode')
    for message in ('APIError: 503 no capacity available, try again later',
                    'Connection reset while sending request data',
                    'A training request timed out', 'Rate limit exceeded'):
        assert not agent.fatal(message)


def test_changed_model_has_new_identity_and_old_history_survives_restart(tmp_path, monkeypatch, quiet):
    _stand_in_cli(tmp_path, monkeypatch, 'opencode')
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (old,) = m.start('opencode', 'game', 1, {'model':'opencode/example-free', 'per_session':3})
    old_name = m.sessions[old].agent_name
    m.sessions[old].push('text', 'Synthetic retained exchange')
    (other,) = m.start('opencode', 'game', 1, {'model':'opencode/another-free'})
    (new,) = m.change_model(old, 'game', 'opencode', 'opencode-go/example')
    assert new != old
    assert m.sessions[new].agent_name != old_name
    assert m.sessions[old].model == 'opencode/example-free'
    assert m.sessions[new].model == 'opencode-go/example'
    assert m.sessions[new].options['per_session'] == 3
    assert m.sessions[old].state == 'stopped'
    assert m.sessions[other].state == 'starting'
    assert m.sessions[old].stopped_by == agents.BY_YOU
    later = agents.Manager(net, tmp_path, URL)
    assert later.sessions[old].options['model_replacement'] == new
    assert later.sessions[old].agent_name == old_name
    assert later.sessions[new].model == 'opencode-go/example'
    with pytest.raises(ValueError, match='already replaced'):
        later.change_model(old, 'game', 'opencode', 'opencode-go/example')


def test_opencode_stderr_stream_rejection_ends_a_process_that_keeps_stdout_open(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    manager = agents.Manager(RomNetwork(tmp_path/'app.db', tmp_path), tmp_path, URL)
    monkeypatch.setattr(manager, '_preflight_tools', lambda *a, **kw: True)
    monkeypatch.setattr(manager.net, 'selection_counts', lambda *a, **kw: {'available':1})
    _, token = manager.net.local_agent('synthetic-stream')
    line = 'level=error message="stream error" providerID=opencode modelID=example-free small=false error="Free usage limit reached private-secret"'
    code = f'import sys,time; print({line!r},file=sys.stderr,flush=True); time.sleep(30)'
    monkeypatch.setattr(manager, '_command', lambda *a: ([sys.executable, '-c', code], dict(os.environ), ''))
    session = agents.Session('synthetic-stream','opencode','synthetic','game','synthetic-stream', {'model':'opencode/example-free'})
    saved=[]
    session.record=lambda kind,text:saved.append((kind,text))
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(manager._run_cli,session,token,tmp_path)
        try:
            with pytest.raises(agents.Fatal,match='free allowance'):
                future.result(timeout=5)
        finally:
            if session.proc is not None:agents._terminate(session.proc)
    # Only this free model is unavailable. No account-wide cooldown, so Go helpers keep working.
    assert not session.limited and not session.signed_out
    peer=manager._record('game','opencode','peer','peer',{'model':'opencode-go/example'})
    assert session_followups.cooldown(manager,peer) is None
    assert not session.stop.is_set(), 'provider failure is not a user stop'
    assert session.proc is None
    errors=[text for kind,text in saved if kind=='error']
    assert errors and 'free allowance' in str(errors)
    assert 'private-secret' not in str(saved)


def test_opencode_terminal_stderr_requires_the_selected_primary_stream():
    agent=kinds.get('opencode')
    prefix='level=error message="stream error" providerID=opencode modelID=example-free small=false'
    line=prefix+' error="Free usage limit reached"'
    for invalid in [line.replace('level=error','level=info'), line.replace('small=false','small=true'),
                    line.replace('providerID=opencode','providerID=opencode-go'),
                    line.replace('modelID=example-free','modelID=another-free'),
                    line.replace('Free usage limit reached','HTTP 502 Bad Gateway; retrying'),
                    line.replace('Free usage limit reached','rate limit reached; retrying'),
                    'Free usage limit reached', prefix]:
        assert not agent.stderr_failure(invalid, 'opencode/example-free')
    diagnostic=agent.stderr_failure(line, 'opencode/example-free#high')
    assert kinds.FREE_ALLOWANCE in diagnostic
    assert not agents.LIMIT.search(diagnostic), 'a free-tier refusal is not the account usage limit'
    assert agent.fatal(diagnostic), 'the helper pauses on a model decision, not a timed cooldown'
    assert not agent.stderr_failure(line, 'opencode-go/example-free')
    assert not agent.stderr_failure(line)
    assert not kinds.get('claude').stderr_failure(line, 'opencode/example-free')


def test_free_allowance_stderr_never_reaches_the_account_limit_check():
    # opencode also logs the refusal for its small title requests, whose stream is not the selected
    # model's primary one. Even those lines used to match LIMIT and publish an account cooldown.
    line = ('level=error message="stream error" providerID=opencode modelID=glm-5.3-flash-free '
            'small=true agent=title error="AI_RetryError: Free usage limit reached. Free models work '
            'in OpenCode (https://opencode.ai); subscribe to OpenCode Go to keep going"')
    assert agents.LIMIT.search(line), 'the provider wording did look like an account usage limit'
    assert agents.FREE_MODEL_ALLOWANCE.search(line)
    assert not agents.LIMIT.search(agents.FREE_MODEL_ALLOWANCE.sub('', line))


@pytest.mark.parametrize('message,outcome', [
    ('Model example-free has been deprecated.', 'fatal'),
    ('Upstream request failed: Model is unavailable.', 'capacity'),
])
def test_opencode_provider_failure_is_not_a_local_mcp_handshake(tmp_path, monkeypatch, message, outcome):
    manager=agents.Manager(RomNetwork(tmp_path/'app.db',tmp_path),tmp_path,URL)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a,**kw:True)
    monkeypatch.setattr(manager.net,'selection_counts',lambda *a,**kw:{'available':1})
    _,token=manager.net.local_agent('synthetic-provider')
    event=json.dumps({'type':'error','error':{'name':'APIError','data':{'message':message}}})
    code=f'import sys; print({event!r},flush=True); raise SystemExit(1)'
    monkeypatch.setattr(manager,'_command',lambda *a:([sys.executable,'-c',code],dict(os.environ),''))
    session=agents.Session('synthetic-provider','opencode','synthetic','game','synthetic-provider',{'model':'opencode/example'})
    if outcome=='fatal':
        with pytest.raises(agents.Fatal,match='deprecated'):
            manager._run_cli(session,token,tmp_path)
    else:
        assert manager._run_cli(session,token,tmp_path) is False
        assert session.throttled and not session.auth_failed and not session.limited
    assert session.tool_handshake_failures==0


@pytest.mark.parametrize('progressing', [False, True])
def test_persistent_provider_overload_leaves_open_stream_for_normal_backoff(tmp_path, monkeypatch, progressing):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import provider_retries
    # Unit checks cover the production 120 s interval. The fake CLI remains
    # bounded while exercising actual stderr/stdout reading and termination.
    monkeypatch.setattr(provider_retries, 'QUIET_SECONDS', 0)
    progress_calls = []
    original_progress = provider_retries.Retries.progress
    def progress(retries):
        progress_calls.append(1)
        original_progress(retries)
    monkeypatch.setattr(provider_retries.Retries, 'progress', progress)
    manager = agents.Manager(RomNetwork(tmp_path/'app.db', tmp_path), tmp_path, URL)
    monkeypatch.setattr(manager, '_preflight_tools', lambda *a, **kw: True)
    monkeypatch.setattr(manager.net, 'selection_counts', lambda *a, **kw: {'available':1})
    _, token = manager.net.local_agent('synthetic-overload')
    line = ('level=ERROR message="stream error" providerID=opencode-go modelID=example small=false '
            'agent=build mode=primary error.error="AI_APICallError: The inference service is overloaded. Please try again later. private-secret"')
    event = json.dumps({'type':'tool_use','part':{'tool':'project_info','input':{}}})
    code = 'import sys,time\n'
    if progressing:
        # Two errors can recover. The runner must observe the tool progress.
        code += f'print({line!r},file=sys.stderr,flush=True)\n'*2
        code += f'print({event!r},flush=True)\n'
    else:
        code += f'print({line!r},file=sys.stderr,flush=True)\n'*3
        code += 'time.sleep(30)\n'
    def command(*args):
        # Simulate the authenticated project_info acknowledgement separately
        # from the fake stdout event. A provider failure before this must still
        # take provider backoff rather than a local-handshake retry.
        if progressing:manager.net._pending_tool_handshakes.clear()
        return [sys.executable,'-c',code],dict(os.environ),''
    monkeypatch.setattr(manager, '_command', command)
    session = agents.Session('synthetic-overload','opencode','synthetic','game','synthetic-overload',
                             {'model':'opencode-go/example'})
    saved = []
    session.record = lambda kind,text: saved.append((kind,text))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(manager._run_cli,session,token,tmp_path)
        try:assert future.result(timeout=5) is progressing
        finally:
            if session.proc is not None:agents._terminate(session.proc)
    assert bool(progress_calls) is progressing
    assert session.throttled is not progressing
    assert not session.limited and not session.signed_out and not session.stop.is_set()
    assert session.tool_handshake_failures == 0
    assert session.proc is None
    assert 'private-secret' not in str(saved)
    if not progressing:
        assert 'No capacity available' in session.error
        assert any(kind=='error' and 'repeated inference service overload' in text for kind,text in saved)


@pytest.mark.parametrize('channel', ['stdout', 'stderr'])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_opencode_unavailable_route_pauses_before_handshake_retry(tmp_path, monkeypatch, channel, exit_code):
    manager=agents.Manager(RomNetwork(tmp_path/'app.db',tmp_path),tmp_path,URL)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a,**kw:True)
    monkeypatch.setattr(manager.net,'selection_counts',lambda *a,**kw:{'available':1})
    _,token=manager.net.local_agent('synthetic-unavailable')
    message='Model unavailable: opencode/removed-example'
    if channel == 'stdout':
        text=json.dumps({'type':'error','error':{'name':'provider.no-route','data':{'message':message}}})
    else:
        text='SessionRunnerModel.ModelUnavailableError: '+message
    output='sys.stdout' if channel == 'stdout' else 'sys.stderr'
    code=f'import sys; print({text!r},file={output},flush=True); raise SystemExit({exit_code})'
    monkeypatch.setattr(manager,'_command',lambda *a:([sys.executable,'-c',code],dict(os.environ),''))
    session=agents.Session('synthetic-unavailable','opencode','synthetic','game','synthetic-unavailable',
                           {'model':'opencode/removed-example'})
    with pytest.raises(agents.Fatal,match='Model unavailable: opencode/removed-example'):
        manager._run_cli(session,token,tmp_path)
    assert session.tool_handshake_failures==0
    assert not session.throttled and not session.limited and not session.auth_failed
    assert not any('handshake was not completed' in item.get('text','') for item in session.feed)
