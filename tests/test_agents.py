"""Helper agents: each CLI's output read into the feed (Antigravity CLI, opencode errors), prompts sent on stdin,
helpers as records that survive a restart, starting again by themselves (and waiting out usage limits), quitting
ending everything they started, what each session spent, and the compact context agents get (assembly, diffs)."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from decompnet import agents as kinds
from decompnet import network, scoring
from decompnet.agents import antigravity, opencode
from decompnet.app import agents
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


def _manager(tmp_path, monkeypatch, kind):
    (tmp_path / "bin").mkdir(exist_ok=True)
    exe = tmp_path / "bin" / {"claude": "claude", "codex": "codex", "antigravity": "agy", "opencode": "opencode"}[kind]
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(agents, "_which", lambda cmd: str(exe))
    m = agents.Manager.__new__(agents.Manager)
    m.mcp_url, m.home = "http://127.0.0.1:6464/mcp", tmp_path
    m.prompt = lambda s: "line one\nline two with \"quotes\" & <ampersands>"
    return m


def test_prompts_go_in_on_stdin_never_the_command_line(tmp_path, monkeypatch):
    """On Windows, npm installs agents as .cmd scripts: cmd.exe cuts a multi-line argument at its first line break
    and caps the command line at 8191 characters."""
    for kind in ("claude", "codex", "antigravity", "opencode"):
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
        assert json.loads((work / "opencode.json").read_text())["permission"] == {"edit": rule, "bash": rule, "webfetch": rule}
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
    assert "gemini" not in {a.id for a in kinds.AGENTS} and kinds.get("antigravity").command == "agy"
    assert kinds.get("gemini") is kinds.get("antigravity")


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


def test_every_helper_with_an_effort_offers_medium_to_max(tmp_path):
    """The launcher offered max for opencode but not xhigh, xhigh for Codex but not max, and neither for the API
    helper (a local llama.cpp model's template wants xhigh): each one that takes an effort offers all four now."""
    for kind in kinds.AGENTS:
        if kind.takes_effort:
            assert {"medium", "high", "xhigh", "max"} <= set(kind.describe()["efforts"]), kind.id
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
    assert later.start("claude", "game", 1, {"model": "opus"}) == [sid]  # its name and record back: no climbing
    assert net.store.one("SELECT COUNT(*) AS n FROM helpers")["n"] == 2
    later.remove(other)
    assert other not in later.sessions and net.store.one("SELECT COUNT(*) AS n FROM helpers")["n"] == 1


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
    monkeypatch.setattr(net, "overview", lambda slug: {"kind": "rom", "title": "Game", "platform": "n64",
                                                      "compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    s = agents.Session("1", "claude", "claude-1", "game", "claude-1", {})
    assert network.stall_budget_text() in m._prompt(s) and "8 claims" in m._prompt(s)


def test_a_project_started_from_scratch_isnt_called_a_public_decomp(tmp_path, monkeypatch):
    """Told a new DS project was "a public matching-decompilation project", a model spent its first 7 minutes
    looking for the project's conventions in config/ and build.ninja; on an N64 game one went to fetch
    the finished public decomp. Every prompt says code from outside the project is out."""
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    s = agents.Session("1", "claude", "claude-1", "game", "claude-1", {})
    for origin, says in (("scratch", "no upstream project"), (None, "a public matching-decompilation project")):
        monkeypatch.setattr(net, "overview", lambda slug, o=origin: {"kind": "fork", "origin": o, "title": "Game",
                                                                     "platform": "nds", "platform_name": "Nintendo DS"})
        assert says in m._prompt(s) and "Never fetch or copy code from outside this project" in m._prompt(s)
    monkeypatch.setattr(net, "overview", lambda slug: {"kind": "rom", "title": "Game", "platform": "n64",
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
    s.stop = Quick()
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
    assert abs(max(w for w in s.stop.waits if w) - (3 * 3600 + agents.LIMIT_RESET_MARGIN)) < 5
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
    assert reset("quota resets at 2026-10-18T15:30:00Z", now) == datetime.fromisoformat("2026-10-18T15:30:00+00:00").timestamp()
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
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "per_session": 2,
                                                       "context_tokens": 32768})
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
    s = m._record("game", "api", "api-m-1", "box #1", {"base_url": "http://mock/v1", "model": "m", "context_tokens": 32768})
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
    assert said.startswith("In the last day claude-haiku's helpers here made 5,741 compiles for 10 matches, $13.81 a match")
    assert "claude-opus's needed about 46 compiles a match, $7.67 a match" in said
    assert "may be past what claude-haiku does well: claude-opus could get further, for less" in said
    assert "You might give claude-haiku fewer helpers here." in said
    assert rows["claude-opus"]["advice"] == ""
    # one model alone: no comparison, a stronger one in general; a model that does fine alone: nothing
    alone = agents.spin_advice([{"kind": "claude", "model": "claude-haiku", "usd": 30.0, "compiles": 900, "matches": 0}])
    assert alone[("claude", "claude-haiku")].endswith("for no matches. What's left of this game may be past what "
                                                      "claude-haiku does well: a stronger model could get further.")
    assert agents.spin_advice([{"kind": "api", "model": "m", "usd": 0, "compiles": 900, "matches": 30}]) == {}
    assert agents.spin_advice([{"kind": "api", "model": "m", "usd": 0, "compiles": 150, "matches": 0}]) == {}  # too few


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
