"""The match-function skill's setup.py: a private session (worktree, venv, claim, renewal) made and undone in one step.

A stand-in for the app answers the three calls the skill's judge.py makes (function, claim, release), so nothing here
touches a real app or claims a real function."""
import importlib.util
import json
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApp:
    """The app's window API (function status, connection create/revoke) and its MCP gateway, on two local ports."""

    def __init__(self, home):
        self.claims, self.revokes, self.status, self.creates, self.busy = [], [], "open", [], 0
        self.calls, self.answers = [], ["ok"]  # the other tools' calls, and what they answer (the last repeats)
        self.tools = [
            {"name": "add_note", "description": "Add a note.", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "get_function", "description": "Read a function.\nMore.", "inputSchema": {
                "type": "object", "properties": {"function": {"type": "string"}}, "required": ["function"]}},
            {"name": "report_repair", "description": "Report what the repair was.", "inputSchema": {
                "type": "object", "properties": {"function": {"type": "string", "description": "which"},
                                                 "category": {"type": "string", "enum": ["register", "other"]}}}}]
        outer = self

        class Window(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, code=200):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply({"status": outer.status, "name": "func_8001"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/api/manual-mcp"
                if body["action"] == "create":
                    outer.creates.append(body)
                    self.reply({"id": 7, "name": "manual-abc", "slug": body["slug"], "token": "bearer-s3cret"})
                else:
                    outer.revokes.append(body)
                    self.reply({"ok": True})

        class Mcp(Window):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/mcp" and self.headers["Authorization"] == "Bearer bearer-s3cret"
                if "id" not in body:
                    return self.reply({}, 202)
                if body["method"] == "initialize":
                    return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"serverInfo": {"name": "decomp"}}})
                if body["method"] == "tools/list":
                    return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": outer.tools}})
                args = body["params"]["arguments"]
                if body["params"]["name"] != "claim_function":
                    outer.calls.append((body["params"]["name"], args))
                    text = outer.answers.pop(0) if len(outer.answers) > 1 else outer.answers[0]
                    return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}})
                outer.claims.append(args)
                if outer.busy:
                    outer.busy -= 1
                    text = "claim_function is still running; repeat exactly the same call"
                else:
                    text = f"## CLAIMED: demo/{args.get('function') or 'func_8001'}\nclaim_token: abc123\n"
                self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}})

        self.servers = [HTTPServer(("127.0.0.1", 0), Window), HTTPServer(("127.0.0.1", 0), Mcp)]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        (home / ".decomp").mkdir(parents=True)
        (home / ".decomp" / "server.json").write_text(json.dumps({"port": self.servers[0].server_port, "secret": "s3cret"}))
        (home / ".decomp" / "mcp-gateway.port").write_text(str(self.servers[1].server_port))

    def close(self):
        for server in self.servers:
            server.shutdown()


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    app = FakeApp(home)
    git = lambda *a, cwd=tmp_path: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "--bare", "-b", "main", "origin.git")
    git("clone", "-q", "origin.git", "primary")
    (tmp_path / "primary" / "f.txt").write_text("x")
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=tmp_path / "primary")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one", cwd=tmp_path / "primary")
    git("push", "-q", "origin", "HEAD:main", cwd=tmp_path / "primary")
    setup = load("setup")
    monkeypatch.setattr(setup, "TMP", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    yield setup, app, home, tmp_path / "primary"
    app.close()


def start(setup, home, primary, *extra):
    return setup.main(["demo", "--home", str(home), "--repo", str(primary), "--no-sync", "--interval", "1", *extra])


def wait_for(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        time.sleep(0.2)
    return condition()


def test_a_session_claims_once_over_mcp_and_is_removed_by_stop(world, capsys):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t1") == 0
    root = setup.session_dir("claude-t1")
    assert (root / "repo" / "f.txt").exists()  # a worktree off origin/main
    text = (root / "connection.json").read_text()
    connection = json.loads(text)
    assert connection["function"] == "func_8001" and connection["token"] == "bearer-s3cret"
    assert connection["started"] > 0  # the hand-off clock's start
    assert connection["gateway_url"].endswith(f":{app.servers[1].server_port}/mcp")
    assert oct((root / "connection.json").stat().st_mode & 0o777) == "0o600"
    assert app.creates == [{"action": "create", "slug": "demo"}]  # the window API only makes the connection
    assert app.claims == [{"strategy": "nearest", "max_insns": 800}]  # the default pick: nearest, at most 800 instructions, asked once over MCP
    out = capsys.readouterr().out
    assert "UV_PROJECT_ENVIRONMENT=" + str(root / "venv") in out and "bearer-s3cret" not in out
    pid = int((root / "renew.pid").read_text())
    assert wait_for(lambda: len(app.claims) >= 2)
    assert app.claims[1] == {"function": "func_8001", "fallback": False}  # renewed by name, never swapped
    assert app.claims.count({"strategy": "nearest", "max_insns": 800}) == 1
    assert setup.alive(pid)
    assert setup.main(["stop", "claude-t1"]) == 0
    assert app.revokes == [{"action": "revoke", "id": 7}]  # frees the connection's claim, with or without a compile
    assert not root.exists()
    assert wait_for(lambda: not setup.alive(pid), 10)
    listing = subprocess.run(["git", "-C", str(primary), "worktree", "list"], capture_output=True, text=True).stdout
    assert str(root) not in listing


def test_random_and_size_options_shape_the_one_automatic_claim(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t9", "--random", "--max-size", "0") == 0
    try:
        assert app.claims[0] == {"strategy": "random"}  # a plain random claim stays available, any size
    finally:
        setup.main(["stop", "claude-t9"])
    assert start(setup, home, primary, "--holder", "claude-t10", "--max-size", "300") == 0
    try:
        assert {"strategy": "nearest", "max_insns": 300} in app.claims
    finally:
        setup.main(["stop", "claude-t10"])


def test_a_named_function_is_claimed_by_name_without_a_replacement(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t2", "func_9000") == 0
    try:
        assert app.claims[0] == {"function": "func_9000", "fallback": False}
    finally:
        setup.main(["stop", "claude-t2"])


def test_a_still_running_answer_repeats_the_identical_call(world, monkeypatch):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    app.busy = 2
    assert start(setup, home, primary, "--holder", "claude-t6") == 0
    try:
        assert app.claims[:3] == [{"strategy": "nearest", "max_insns": 800}] * 3
    finally:
        setup.main(["stop", "claude-t6"])


def test_the_renewal_ends_by_itself_once_the_function_matches(world):
    setup, app, home, primary = world
    app.status = "matched"
    assert start(setup, home, primary, "--holder", "claude-t3") == 0
    root = setup.session_dir("claude-t3")
    assert wait_for(lambda: not (root / "renew.pid").exists())
    assert len(app.claims) == 1  # matched: nothing left to renew
    setup.main(["stop", "claude-t3"])


def test_a_failed_claim_revokes_the_connection_and_leaves_nothing_behind(world, monkeypatch):
    setup, app, home, primary = world

    def refuse(gateway, function="", *_):
        raise setup.McpError("claim_function: someone holds it")
    monkeypatch.setattr(setup, "claim", refuse)
    with pytest.raises(SystemExit, match="someone holds it"):
        start(setup, home, primary, "--holder", "claude-t4")
    assert not setup.session_dir("claude-t4").exists()
    assert app.revokes == [{"action": "revoke", "id": 7}]
    assert str(setup.session_dir("claude-t4")) not in subprocess.run(
        ["git", "-C", str(primary), "worktree", "list"], capture_output=True, text=True).stdout


def test_a_missing_gateway_stops_before_anything_is_built(world):
    setup, app, home, primary = world
    (home / ".decomp" / "mcp-gateway.port").unlink()
    with pytest.raises(SystemExit, match="MCP gateway isn't running"):
        start(setup, home, primary, "--holder", "claude-t7")
    assert not setup.session_dir("claude-t7").exists() and app.creates == []


def test_holder_names_are_checked_and_never_reused(world):
    setup, app, home, primary = world
    with pytest.raises(SystemExit):
        setup.holder_name("bad name!")
    assert setup.holder_name().startswith("claude-")
    setup.session_dir("claude-t5").mkdir()
    with pytest.raises(SystemExit, match="exists"):
        start(setup, home, primary, "--holder", "claude-t5")


def test_an_event_stream_reply_is_read_like_a_json_one(world, monkeypatch):
    setup = world[0]

    class Reply:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"## CLAIMED: g/f_1"}]}}\n\n'

    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: Reply())
    assert setup.claim(setup.Gateway("http://x/mcp", "t"), "f_1") == "f_1"


def test_the_venv_is_synced_inside_the_worktree_into_the_private_environment_only(world, monkeypatch, capsys):
    """The worktree is off the latest origin/main, so an older pin in a stale venv failed tests until `uv sync` by hand:
    setup syncs it, `sync HOLDER` repeats that, and the primary checkout's .venv is never the target."""
    setup, app, home, primary = world
    calls = []

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] != ["uv", "sync"]:
            return real_run(cmd, **kwargs)  # git and ps run for real
        calls.append((cmd, kwargs["cwd"], kwargs["env"]["UV_PROJECT_ENVIRONMENT"]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert setup.main(["demo", "--home", str(home), "--repo", str(primary), "--interval", "1", "--holder", "claude-t2"]) == 0
    root = setup.session_dir("claude-t2")
    uv = [c for c in calls if c[0][:2] == ["uv", "sync"]]
    assert uv == [(["uv", "sync"], (root / "repo").resolve(), str((root / "venv").resolve()))]
    assert uv[0][1] != primary and not str(uv[0][2]).startswith(str(primary))
    calls.clear()
    assert setup.main(["sync", "claude-t2"]) == 0
    assert [c[:2] for c in calls] == [(["uv", "sync"], (root / "repo").resolve())]
    assert "synced" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        setup.main(["sync", "no-such-holder"])
    assert setup.main(["stop", "claude-t2"]) == 0


def test_try_submits_a_candidate_as_the_session_through_compile_and_diff(world, capsys, monkeypatch, tmp_path):
    setup, app, home, primary = world
    seen = []
    original = setup.Gateway.call

    def call(self, tool, arguments):
        if tool == "compile_and_diff":
            seen.append((self.token, tool, arguments))
            return "score 12\n1 register\n"
        return original(self, tool, arguments)

    monkeypatch.setattr(setup.Gateway, "call", call)
    assert start(setup, home, primary, "--holder", "claude-t9") == 0
    capsys.readouterr()
    cand = tmp_path / "cand.c"
    cand.write_text("int f(void) { return 1; }")
    assert setup.main(["try", "claude-t9", str(cand)]) == 0
    assert seen == [("bearer-s3cret", "compile_and_diff", {"function": "func_8001", "source": cand.read_text()})]
    out = capsys.readouterr().out
    assert "score 12" in out and "bearer-s3cret" not in out
    assert setup.main(["try", "claude-t9", str(cand), "func_9"]) == 0 and seen[-1][2]["function"] == "func_9"
    with pytest.raises(SystemExit, match="no session"):
        setup.main(["try", "nobody", str(cand)])
    setup.main(["stop", "claude-t9"])


def test_try_reports_a_refusal_from_the_app(world, tmp_path, monkeypatch):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t8") == 0

    def refuse(self, tool, arguments):
        if tool == "compile_and_diff":
            raise setup.McpError("compile_and_diff: you do not hold func_8001")
        return "## CLAIMED: demo/func_8001\n"

    cand = tmp_path / "c.c"
    cand.write_text("x")
    monkeypatch.setattr(setup.Gateway, "call", refuse)
    with pytest.raises(SystemExit, match="not submitted: .*do not hold"):
        setup.main(["try", "claude-t8", str(cand)])
    setup.main(["stop", "claude-t8"])


def test_an_older_app_without_nearest_gets_closest_then_random(world):
    setup = world[0]

    class OldGateway:
        def __init__(self, known):
            self.known, self.calls = known, []

        def call(self, tool, arguments):
            self.calls.append(arguments)
            if arguments.get("strategy") not in self.known:
                raise setup.McpError("claim_function: strategy must be one of easiest, closest, random")
            if "max_insns" in arguments and "max_insns" not in self.known:
                raise setup.McpError("claim_function: unexpected keyword argument 'max_insns'")
            return "## CLAIMED: demo/func_8001"

    old = OldGateway({"closest", "random"})
    assert setup.claim(old, "", "nearest", 800) == "func_8001"
    assert old.calls == [{"strategy": "nearest", "max_insns": 800}, {"strategy": "closest", "max_insns": 800},
                         {"strategy": "closest"}]
    oldest = OldGateway({"random"})
    assert setup.claim(oldest, "", "nearest", 800) == "func_8001" and oldest.calls[-1] == {"strategy": "random"}

    class Busy(OldGateway):
        def call(self, tool, arguments):
            raise setup.McpError("claim_function: nothing left to claim")

    with pytest.raises(setup.McpError, match="nothing left"):
        setup.claim(Busy(set()), "", "nearest", 800)  # a real refusal is not retried with another order


def test_judge_claim_falls_back_to_closest_on_an_older_app():
    judge = load("judge")
    app = judge.App.__new__(judge.App)
    sent = []

    def call(path, body):
        sent.append(body["strategy"])
        return {"name": "f"} if body["strategy"] == "closest" else {"error": "strategy must be one of easiest, closest"}
    app.call = call
    assert app.claim("demo", strategy="nearest", max_insns=800) == {"name": "f"} and sent == ["nearest", "closest"]


def test_a_released_function_is_never_renewed_again_and_a_reclaimed_one_is(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-r1", "func_9000") == 0
    root = setup.session_dir("claude-r1")
    try:
        assert wait_for(lambda: {"function": "func_9000", "fallback": False} in app.claims[1:])
        assert setup.main(["release", "claude-r1"]) == 0
        info = json.loads((root / "connection.json").read_text())
        assert info["function"] == "" and info["released"] == ["func_9000"] and info["token"] == "bearer-s3cret"
        time.sleep(0.5)  # a renewal already under way finishes; none starts after it
        seen = len(app.claims)
        time.sleep(3.5)  # several 1-second cycles
        assert len(app.claims) == seen  # nothing claimed: the loop has no function to renew
        assert (root / "renew.pid").exists()  # and it keeps running for the next one
        with pytest.raises(SystemExit, match="released by this session"):
            setup.main(["reclaim", "claude-r1", "func_9000"])
        assert setup.main(["reclaim", "claude-r1", "func_9100"]) == 0
        assert app.claims[seen] == {"function": "func_9100", "fallback": False}
        assert json.loads((root / "connection.json").read_text())["function"] == "func_9100"
        mark = len(app.claims)
        assert wait_for(lambda: len(app.claims) >= mark + 2)
        assert {c.get("function") for c in app.claims[mark:]} == {"func_9100"}  # renewed: the new one only
    finally:
        setup.main(["stop", "claude-r1"])


def test_reclaim_with_no_name_claims_the_nearest_and_refuses_the_released_one(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-r2") == 0  # the fake app answers func_8001 to a pick
    try:
        setup.main(["release", "claude-r2"])
        with pytest.raises(SystemExit, match="released by this session"):
            setup.main(["reclaim", "claude-r2"])  # the app handed back the function it let go
        assert app.claims.count({"strategy": "nearest", "max_insns": 800}) == 2
        assert json.loads((setup.session_dir("claude-r2") / "connection.json").read_text())["function"] == ""
    finally:
        setup.main(["stop", "claude-r2"])


def test_mcp_lists_describes_and_calls_tools_over_the_sessions_connection(world, capsys):
    setup, app, home, primary = world
    mcp = load("mcp")
    mcp.setup.TMP = setup.TMP
    assert start(setup, home, primary, "--holder", "claude-m1") == 0
    try:
        capsys.readouterr()
        assert mcp.run(["claude-m1", "--list"]) == 0
        listing = capsys.readouterr().out
        assert "get_function" in listing and "Read a function." in listing and "More." not in listing
        assert mcp.run(["claude-m1", "report_repair", "--help"]) == 0
        helped = capsys.readouterr().out
        assert "one of: register, other" in helped and "bearer-s3cret" not in helped
        app.answers = ["the function text"]
        assert mcp.run(["claude-m1", "get_function", '{"function": "func_8001"}']) == 0
        assert "the function text" in capsys.readouterr().out
        assert app.calls == [("get_function", {"function": "func_8001"})]
        with pytest.raises(SystemExit, match="category='bogus' is not valid; use one of: register, other"):
            mcp.run(["claude-m1", "report_repair", '{"category": "bogus"}'])
        with pytest.raises(SystemExit, match="no tool 'nope'"):
            mcp.run(["claude-m1", "nope"])
        assert len(app.calls) == 1  # a refused call never reached the app
    finally:
        setup.main(["stop", "claude-m1"])


def test_mcp_repeats_a_still_running_answer_but_never_an_accepted_one(world, monkeypatch, capsys):
    setup, app, home, primary = world
    mcp = load("mcp")
    mcp.setup.TMP = setup.TMP
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    monkeypatch.setattr(mcp.setup.time, "sleep", lambda _: None)
    assert start(setup, home, primary, "--holder", "claude-m2") == 0
    try:
        capsys.readouterr()
        app.answers = ["add_note is still running; repeat exactly the same call", "add_note is still running; repeat exactly "
                       "the same call", "noted"]
        assert mcp.run(["claude-m2", "add_note", '{"function": "f"}']) == 0
        assert [c[0] for c in app.calls] == ["add_note"] * 3 and "noted" in capsys.readouterr().out
        app.calls.clear()
        app.answers = ["accepted: still running in the app, do not repeat this call"]
        assert mcp.run(["claude-m2", "add_note"]) == 0
        assert len(app.calls) == 1 and "accepted" in capsys.readouterr().out
    finally:
        setup.main(["stop", "claude-m2"])


def test_env_prints_evalable_lines_without_the_token_and_setup_ends_with_them(world, capsys):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-e1") == 0
    try:
        started = capsys.readouterr().out
        assert setup.main(["env", "claude-e1"]) == 0
        line = capsys.readouterr().out.strip()
        assert line in started and "bearer-s3cret" not in line
        words = shlex.split(line)
        assert words[0] == "export"
        env = dict(word.split("=", 1) for word in words[1:])
        root = setup.session_dir("claude-e1")
        assert env == {"UV_PROJECT_ENVIRONMENT": str(root / "venv"), "DECOMP_HOME": str(home),
                       "DECOMP_PORT": str(app.servers[0].server_port)}
    finally:
        setup.main(["stop", "claude-e1"])
    with pytest.raises(SystemExit, match="no session"):
        setup.main(["env", "claude-e1"])
