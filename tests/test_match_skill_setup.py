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


SLOW = ("{0} is still running (30 s so far): decompdecomp is still working on it. Nothing was lost and it will not run "
        "twice: repeat exactly the same {0} call (same arguments) to wait for its result.")


class FakeApp:
    """The app's window API (function status, connection create/revoke) and its MCP gateway, on two local ports."""

    def __init__(self, home):
        self.claims, self.revokes, self.status, self.creates, self.busy = [], [], "open", [], 0
        self.busy_is_error = False
        self.drops = 0  # the next N tools/call requests get HTTP 503 (an overloaded app)
        self.owner = ""  # when set, claim_function refuses: someone else holds the function
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
                if outer.drops:
                    outer.drops -= 1
                    return self.reply({"error": "overloaded"}, 503)
                if body["params"]["name"] != "claim_function":
                    outer.calls.append((body["params"]["name"], args))
                    text = outer.answers.pop(0) if len(outer.answers) > 1 else outer.answers[0]
                    return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}})
                outer.claims.append(args)
                if outer.owner:
                    text = f"claim_function: {args.get('function')} is claimed by {outer.owner} for another 7 min"
                    return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {
                        "isError": True, "content": [{"type": "text", "text": text}]}})
                if outer.busy:
                    outer.busy -= 1
                    text = "claim_function is still running; repeat exactly the same call"
                    if outer.busy_is_error:  # the real gateway answers "still running" as an error result
                        return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {
                            "isError": True, "content": [{"type": "text", "text": text}]}})
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


def test_a_still_running_answer_marked_as_an_error_result_is_repeated_too(world, monkeypatch):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    app.busy, app.busy_is_error = 2, True
    assert start(setup, home, primary, "--holder", "claude-t6e") == 0
    try:
        assert app.claims[:3] == [{"strategy": "nearest", "max_insns": 800}] * 3
    finally:
        setup.main(["stop", "claude-t6e"])


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


def lost_world(world, holder, capsys):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", holder, "func_8001") == 0
    capsys.readouterr()
    return setup, app, setup.session_dir(holder)


def test_the_renewal_records_a_lost_claim_and_every_command_warns_loudly(world, capsys, tmp_path):
    setup, app, root = lost_world(world, "claude-l1", capsys)
    claimlost = sys.modules["claimlost"]
    try:
        app.owner = "claude-other"
        assert wait_for(lambda: (root / "LOST").exists())
        marker = json.loads((root / "LOST").read_text())
        assert marker["holder"] == "claude-other" and marker["function"] == "func_8001" and not marker["matched"]
        assert "claude-other" in (root / "renew.log").read_text()
        setup.main(["env", "claude-l1"])  # a command prints the warning (stderr) before its own output
        err = capsys.readouterr().err
        assert "CLAIM LOST" in err and "claude-other" in err and "reclaim claude-l1" in err
        cand = tmp_path / "c.c"
        cand.write_text("int f(void){return 0;}")
        app.calls.clear()
        with pytest.raises(SystemExit, match="someone else holds"):
            setup.main(["try", "claude-l1", str(cand)])
        assert not [c for c in app.calls if c[0] == "compile_and_diff"]  # refused before anything is submitted
        # the helpers print the same warning from the folder their venv lives in
        capsys.readouterr()
        assert claimlost.warn(root) and "CLAIM LOST" in capsys.readouterr().err
        setup.main(["release", "claude-l1"])
        assert not (root / "LOST").exists() and claimlost.warning(root) == ""
    finally:
        setup.main(["stop", "claude-l1"])


def test_a_failed_renewal_is_retried_at_once_and_lost_only_when_the_claim_must_have_lapsed(world, capsys):
    setup, app, root = lost_world(world, "claude-l2", capsys)
    claimlost = sys.modules["claimlost"]
    try:
        info = json.loads((root / "connection.json").read_text())
        gateway = setup.Gateway(info["gateway_url"], info["token"])
        flaky = setup.Gateway(info["gateway_url"], info["token"])
        calls = []

        class Slow:
            def function(self, slug, name):
                calls.append(name)
                if len(calls) < 3:
                    raise setup.McpError("timed out")
                return {"status": "open"}

        assert setup.renew_once(root, info, Slow(), gateway, time.time(), pause=0) == "renewed"
        assert len(calls) == 3  # two timeouts, then the renewal went through within the same cycle
        assert not (root / "LOST").exists()

        class Down:
            def function(self, slug, name):
                raise OSError("connection refused")

        assert setup.renew_once(root, info, Down(), flaky, time.time(), pause=0) == "failed"
        assert not (root / "LOST").exists()  # a miss of a few seconds is not a loss
        assert setup.renew_once(root, info, Down(), flaky, time.time() - 700, pause=0) == "failed"
        assert "OSError" not in (root / "LOST").read_text() and claimlost.read(root)["holder"] == ""
        assert "could not be renewed" in claimlost.warning(root)
        # a transient loss does not stop `try`: only a function someone else holds does
        assert not claimlost.taken(claimlost.read(root))
        assert setup.renew_once(root, info, Slow(), gateway, time.time(), pause=0) == "renewed"
        assert not (root / "LOST").exists()  # the claim came back: the warning goes
    finally:
        setup.main(["stop", "claude-l2"])


def test_a_matched_function_is_recorded_and_a_released_one_is_never_claimed_to_renew_it(world, capsys):
    setup, app, root = lost_world(world, "claude-l3", capsys)
    try:
        info = json.loads((root / "connection.json").read_text())
        app.status = "matched"
        before = len(app.claims)
        gateway = setup.Gateway(info["gateway_url"], info["token"])
        assert setup.renew_once(root, info, setup.judge.App(info["home"]), gateway, time.time(), pause=0) == "matched"
        assert len(app.claims) == before and json.loads((root / "LOST").read_text())["matched"] is True
        assert "MATCHED" in setup.sys.modules["claimlost"].warning(root)
    finally:
        setup.main(["stop", "claude-l3"])


def test_check_asks_the_app_now_and_exits_nonzero_when_the_claim_is_not_the_sessions(world, capsys):
    setup, app, root = lost_world(world, "claude-l4", capsys)
    try:
        app.answers = [json.dumps({"claims": [{"function": "func_8001", "expires_at": time.time() + 500}]})]
        assert setup.main(["check", "claude-l4"]) == 0
        assert "claimed by this session" in capsys.readouterr().out and not (root / "LOST").exists()
        app.answers = [json.dumps({"claims": []})]
        assert setup.main(["status", "claude-l4"]) == 1
        out = capsys.readouterr()
        assert "CLAIM LOST" in out.err + out.out and (root / "LOST").exists()
        app.answers = [json.dumps({"claims": [{"function": "func_8001"}]})]
        assert setup.main(["check", "claude-l4"]) == 0 and not (root / "LOST").exists()  # reclaimed: cleared
    finally:
        setup.main(["stop", "claude-l4"])


def test_the_long_helpers_warn_when_the_session_marker_exists(world, capsys, monkeypatch):
    setup, app, root = lost_world(world, "claude-l5", capsys)
    try:
        sys.modules["claimlost"].write(root, "func_8001", "x", holder="claude-other")
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(root / "venv"))
        for name in ("fast_judge", "prove_chain", "grid"):
            text = (SCRIPTS / f"{name}.py").read_text()
            assert "claimlost.warn()" in text
        capsys.readouterr()
        assert sys.modules["claimlost"].warn() and "claude-other" in capsys.readouterr().err
    finally:
        setup.main(["stop", "claude-l5"])


def test_an_unchanged_claim_reply_counts_as_still_held_not_as_a_failed_renewal():
    """The app answers a renewal that changes nothing with "You still hold the unchanged claim", not "## CLAIMED"; the
    renewal took it for a failure and wrote a LOST warning for a function the session still held."""
    setup = load("setup")

    class Gateway:
        def __init__(self, text):
            self.text = text

        def call(self, tool, arguments):
            return self.text

    assert setup.claim(Gateway("You still hold the unchanged claim: demo/func_8001. No new functions were claimed.\nx"), "func_8001") == "func_8001"
    assert setup.claim(Gateway("## CLAIMED: demo/func_8002\nclaim_token: a\n"), "func_8002") == "func_8002"
    with pytest.raises(setup.McpError, match="didn't claim anything"):
        setup.claim(Gateway("nothing"), "func_8001")
    with pytest.raises(setup.McpError, match="didn't claim anything"):  # a claim by strategy has no function to keep
        setup.claim(Gateway("You still hold the unchanged claim: demo/func_8001. x"), "", "nearest")




def test_a_claim_that_stays_still_running_for_long_is_waited_for_not_given_up_after_60_repeats(world, monkeypatch, capsys):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    app.busy = 130
    assert start(setup, home, primary, "--holder", "claude-w1") == 0
    try:
        assert len(app.claims) == 131 and set(map(str, app.claims)) == {str({"strategy": "nearest", "max_insns": 800})}
        assert "claim_function: still running in the app" in capsys.readouterr().err  # progress is printed
    finally:
        setup.main(["stop", "claude-w1"])


def test_a_claim_the_app_never_finishes_in_time_keeps_the_built_session_and_a_rerun_reuses_it(world, monkeypatch):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    monkeypatch.setattr(setup, "CALL_WAIT_SECONDS", 0)
    app.busy = 10**6
    with pytest.raises(SystemExit, match="run the same command again"):
        start(setup, home, primary, "--holder", "claude-w2")
    root = setup.session_dir("claude-w2")
    assert (root / "repo" / "f.txt").exists() and (root / "connection.json").exists()  # nothing was torn down
    assert app.revokes == [] and len(app.creates) == 1
    app.busy = 0
    monkeypatch.setattr(setup, "CALL_WAIT_SECONDS", 60)
    assert start(setup, home, primary, "--holder", "claude-w2") == 0  # the rerun reuses worktree and connection
    try:
        assert len(app.creates) == 1 and app.revokes == []
        assert json.loads((root / "connection.json").read_text())["function"] == "func_8001"
        assert "claim_pending" not in json.loads((root / "connection.json").read_text())
        assert (root / "renew.pid").exists()
    finally:
        setup.main(["stop", "claude-w2"])


def test_a_dropped_connection_or_503_is_repeated_not_fatal(world, monkeypatch):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    assert start(setup, home, primary, "--holder", "claude-w3") == 0
    try:
        gateway, _ = setup.session_gateway(holder="claude-w3")
        app.drops, app.answers = 3, ["the function"]
        assert gateway.call("get_function", {"function": "f"}) == "the function"
        assert len(app.calls) == 1
        app.drops = 10**6
        with pytest.raises(setup.AppSlow):
            gateway.call("get_function", {"function": "f"}, wait=0)
    finally:
        app.drops = 0
        setup.main(["stop", "claude-w3"])


def test_try_never_sends_a_second_source_while_the_first_is_still_in_flight(world, monkeypatch, tmp_path, capsys):
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    assert start(setup, home, primary, "--holder", "claude-w4") == 0
    root = setup.session_dir("claude-w4")
    old, new = tmp_path / "old.c", tmp_path / "new.c"
    old.write_text("int old;")
    new.write_text("int new;")
    try:
        app.answers = [SLOW.format("compile_and_diff")]
        monkeypatch.setattr(setup, "CALL_WAIT_SECONDS", 0)
        with pytest.raises(SystemExit, match="still in flight"):
            setup.main(["try", "claude-w4", str(old)])
        assert (root / "try-pending.json").exists()
        monkeypatch.setattr(setup, "CALL_WAIT_SECONDS", 60)
        app.calls.clear()
        app.answers = ["old verdict", "new verdict"]
        assert setup.main(["try", "claude-w4", str(new)]) == 0
        # the first source is repeated and finished before the second is sent
        assert [a["source"] for _, a in app.calls] == ["int old;", "int new;"]
        out = capsys.readouterr().out
        assert out.index("old verdict") < out.index("new verdict")
        assert not (root / "try-pending.json").exists()
    finally:
        setup.main(["stop", "claude-w4"])


def test_stop_ends_the_renewal_even_when_it_ignores_sigterm(world, tmp_path, monkeypatch):
    setup, app, home, primary = world
    monkeypatch.setattr(setup, "STOP_GRACE_SECONDS", 1)
    root = setup.session_dir("claude-w5")
    root.mkdir(parents=True)
    child = subprocess.Popen(
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
         "_renew", str(root), "1"], start_new_session=True)
    (root / "renew.pid").write_text(str(child.pid))
    time.sleep(0.5)
    assert setup.alive(child.pid)
    assert setup.main(["stop", "claude-w5"]) == 0
    assert child.poll() is not None and not setup.alive(child.pid)  # SIGKILLed, and gone
    assert not root.exists()


def test_stop_finds_a_renewal_whose_pid_file_is_gone(world, monkeypatch):
    setup, app, home, primary = world
    root = setup.session_dir("claude-w6")
    root.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "_renew", str(root), "1"],
                             start_new_session=True)
    time.sleep(0.5)
    try:
        assert setup.main(["stop", "claude-w6"]) == 0
        assert child.poll() is not None
    finally:
        child.kill()


def test_a_real_session_stop_leaves_no_renewal_behind(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-w7") == 0
    root = setup.session_dir("claude-w7")
    pid = int((root / "renew.pid").read_text())
    assert setup.alive(pid)
    assert setup.main(["stop", "claude-w7"]) == 0
    assert not setup.alive(pid)


def test_a_still_running_notice_flagged_as_an_error_is_repeated_not_fatal_and_phases_are_timed(world, monkeypatch, capsys):
    """The gateway sends "still running" with isError set: a claim the app took over 45 s to start used to raise there
    and tear the whole session down. The phases of the start are timed on stderr so a slow one is visible."""
    setup, app, home, primary = world
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    app.busy, app.busy_is_error = 3, True
    assert start(setup, home, primary, "--holder", "claude-e1") == 0
    try:
        assert len(app.claims) == 4 and app.revokes == []
        err = capsys.readouterr().err
        assert "claim_function: answered after" in err and "3 repeats" in err
        for name in ("worktree", "connection", "MCP handshake", "claim_function (gateway to app)"):
            assert f"[setup] {name}" in err
        assert "phases: worktree" in err and "claim_function (gateway to app)" in err.split("phases:")[1]
    finally:
        setup.main(["stop", "claude-e1"])


def test_a_restarting_app_is_waited_for_and_a_refused_claim_says_what_to_do():
    setup = load("setup")
    gateway_text = ("decompdecomp isn't running or is restarting (connection refused). Nothing was changed. "
                    "Wait a minute, then call claim_function again.")
    assert setup.is_restarting(gateway_text.lower()) and not setup.is_still_running(gateway_text.lower())
    assert "report_repair" in setup.claim_hint("Before claiming more work, call report_repair with function, case_id=3")
    assert "release_function" in setup.claim_hint("you already hold func_1; match it or release_function it first")
    assert "ask for another" in setup.claim_hint("func_2 is set aside (assembly): claim another")
    assert setup.claim_hint("something unforeseen") == ""


def test_a_refused_claim_at_start_prints_the_hint_and_leaves_nothing_behind(world, monkeypatch):
    setup, app, home, primary = world
    app.owner = "agent-x"
    with pytest.raises(SystemExit, match="What to do: another agent"):
        start(setup, home, primary, "--holder", "claude-e2", "func_9")
    assert not setup.session_dir("claude-e2").exists()
