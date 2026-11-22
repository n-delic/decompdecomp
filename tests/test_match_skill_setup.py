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
    def __init__(self, home):
        self.claims, self.releases, self.status = [], [], "open"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply({"status": outer.status, "name": "func_8001"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/claim":
                    outer.claims.append(body)
                    self.reply({"name": body["name"] or "func_8001"})
                else:
                    outer.releases.append(body)
                    self.reply({"released": True})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        (home / ".decomp").mkdir(parents=True)
        (home / ".decomp" / "server.json").write_text(json.dumps({"port": self.server.server_port, "secret": "s3cret"}))

    def close(self):
        self.server.shutdown()


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


def test_a_session_claims_once_and_is_removed_by_stop(world, capsys):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t1") == 0
    root = setup.session_dir("claude-t1")
    assert (root / "repo" / "f.txt").exists()  # a worktree off origin/main
    connection = json.loads((root / "connection.json").read_text())
    assert connection["function"] == "func_8001" and "s3cret" not in (root / "connection.json").read_text()
    assert oct((root / "connection.json").stat().st_mode & 0o777) == "0o600"
    assert [c["name"] for c in app.claims].count("") == 1  # the random claim, asked for once
    assert "UV_PROJECT_ENVIRONMENT=" + str(root / "venv") in capsys.readouterr().out
    pid = int((root / "renew.pid").read_text())
    deadline = time.time() + 15
    while len(app.claims) < 2 and time.time() < deadline:
        time.sleep(0.2)
    assert app.claims[1]["name"] == "func_8001" and app.claims[1]["holder"] == "claude-t1"  # renewed by name
    assert [c["name"] for c in app.claims].count("") == 1
    assert setup.alive(pid)
    assert setup.main(["stop", "claude-t1"]) == 0
    assert app.releases[-1]["name"] == "func_8001" and app.releases[-1]["holder"] == "claude-t1"
    assert not root.exists()
    deadline = time.time() + 10
    while setup.alive(pid) and time.time() < deadline:
        time.sleep(0.2)
    assert not setup.alive(pid)
    listing = subprocess.run(["git", "-C", str(primary), "worktree", "list"], capture_output=True, text=True).stdout
    assert str(root) not in listing


def test_a_named_function_is_claimed_by_name_not_at_random(world):
    setup, app, home, primary = world
    assert start(setup, home, primary, "--holder", "claude-t2", "func_9000") == 0
    try:
        assert app.claims[0]["name"] == "func_9000"
    finally:
        setup.main(["stop", "claude-t2"])


def test_the_renewal_ends_by_itself_once_the_function_matches(world):
    setup, app, home, primary = world
    app.status = "matched"
    assert start(setup, home, primary, "--holder", "claude-t3") == 0
    root = setup.session_dir("claude-t3")
    deadline = time.time() + 15
    while (root / "renew.pid").exists() and time.time() < deadline:
        time.sleep(0.2)
    assert not (root / "renew.pid").exists()
    assert len(app.claims) == 1  # matched: nothing left to renew
    setup.main(["stop", "claude-t3"])


def test_a_failed_claim_leaves_nothing_behind(world, monkeypatch):
    setup, app, home, primary = world
    judge = sys.modules["judge"]
    monkeypatch.setattr(judge.App, "claim", lambda self, slug, name="": {"error": "someone holds it"})
    with pytest.raises(SystemExit, match="no claim"):
        start(setup, home, primary, "--holder", "claude-t4")
    assert not setup.session_dir("claude-t4").exists()
    assert str(setup.session_dir("claude-t4")) not in subprocess.run(
        ["git", "-C", str(primary), "worktree", "list"], capture_output=True, text=True).stdout


def test_holder_names_are_checked_and_never_reused(world):
    setup, app, home, primary = world
    with pytest.raises(SystemExit):
        setup.holder_name("bad name!")
    assert setup.holder_name().startswith("claude-")
    setup.session_dir("claude-t5").mkdir()
    with pytest.raises(SystemExit, match="exists"):
        start(setup, home, primary, "--holder", "claude-t5")
