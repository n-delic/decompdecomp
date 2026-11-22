"""The match-function skill's setup.py: a private session (worktree, venv, claim, renewal) made and undone in one step.

A stand-in for the app answers the three calls the skill's judge.py makes (function, claim, release), so nothing here
touches a real app or claims a real function."""
import importlib.util
import json
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
                args = body["params"]["arguments"]
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
    assert connection["gateway_url"].endswith(f":{app.servers[1].server_port}/mcp")
    assert oct((root / "connection.json").stat().st_mode & 0o777) == "0o600"
    assert app.creates == [{"action": "create", "slug": "demo"}]  # the window API only makes the connection
    assert app.claims == [{"strategy": "random"}]  # the random claim, asked for once, over MCP
    out = capsys.readouterr().out
    assert "UV_PROJECT_ENVIRONMENT=" + str(root / "venv") in out and "bearer-s3cret" not in out
    pid = int((root / "renew.pid").read_text())
    assert wait_for(lambda: len(app.claims) >= 2)
    assert app.claims[1] == {"function": "func_8001", "fallback": False}  # renewed by name, never swapped
    assert app.claims.count({"strategy": "random"}) == 1
    assert setup.alive(pid)
    assert setup.main(["stop", "claude-t1"]) == 0
    assert app.revokes == [{"action": "revoke", "id": 7}]  # frees the connection's claim, with or without a compile
    assert not root.exists()
    assert wait_for(lambda: not setup.alive(pid), 10)
    listing = subprocess.run(["git", "-C", str(primary), "worktree", "list"], capture_output=True, text=True).stdout
    assert str(root) not in listing


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
        assert app.claims[:3] == [{"strategy": "random"}] * 3
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

    def refuse(gateway, function=""):
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
