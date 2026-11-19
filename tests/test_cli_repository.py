"""`decomp commit` and `decomp push`: a project's save repository committed and pushed by the running app, asked as
the window asks (server.json's secret, as `decomp agents` does), its jobs followed to the end."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decompnet.app import cli, window

STATUS = {"slug": "a-game", "root": "/saves/a-game", "origin": True, "checkpoint": True}


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    monkeypatch.setenv("DECOMP_HOME", str(home))
    seen = {"requests": [], "jobs": {}, "status": dict(STATUS)}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, code, data):
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                return self.answer(200, {"app": "decomp-wtf", "version": __import__("decompnet").__version__})
            seen["requests"].append(("GET", self.path, None, self.headers.get("User-Agent")))
            if self.path.startswith("/api/job?id="):
                return self.answer(200, seen["jobs"][self.path.split("=", 1)[1]])
            return self.answer(200, seen["status"])

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen["requests"].append(("POST", self.path, body, self.headers.get("User-Agent")))
            return self.answer(200, {"job": {"/api/repository-setup": "commit", "/api/repository-push": "push"}[self.path]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (home / ".decomp" / "server.json").write_text(json.dumps({"port": server.server_address[1], "secret": "s3cret", "pid": 1}))
    seen["jobs"]["commit"] = {"state": "done", "log": ["Saved progress commit 0123456789ab. Nothing was pushed."],
                              "result": seen["status"]}
    seen["jobs"]["push"] = {"state": "done", "log": ["Pushed 3 commits."],
                            "result": {"status": {"destination": "origin/main"}}}
    yield seen
    server.shutdown()


def posts(app):
    return [(path, body) for method, path, body, _ in app["requests"] if method == "POST"]


def test_commit_asks_for_the_commit_buttons_checkpoint_and_prints_its_log(app, capsys):
    assert cli.main(["commit", "a-game"]) == 0
    assert posts(app) == [("/api/repository-setup", {"slug": "a-game", "action": "checkpoint", "origin": ""})]
    assert all(agent == f"{window.TOKEN_AGENT}s3cret" for *_, agent in app["requests"])
    assert "Saved progress commit 0123456789ab" in capsys.readouterr().out


def test_commit_then_push_pushes_the_repository_the_commit_reported(app, capsys):
    assert cli.main(["commit", "a-game", "--push"]) == 0
    assert posts(app)[1] == ("/api/repository-push", {"slug": "a-game", "folder": "/saves/a-game"})
    out = capsys.readouterr().out
    assert "Pushed 3 commits." in out and "pushed to origin/main" in out


def test_push_alone_reads_the_repository_first(app, capsys):
    assert cli.main(["push", "a-game"]) == 0
    assert app["requests"][0][:2] == ("GET", "/api/repository-setup?slug=a-game")
    assert posts(app) == [("/api/repository-push", {"slug": "a-game", "folder": "/saves/a-game"})]


def test_a_failed_commit_says_why_and_pushes_nothing(app, capsys):
    app["jobs"]["commit"] = {"state": "error", "log": [], "error": "Set Git user.name and user.email before committing."}
    assert cli.main(["commit", "a-game", "--push"]) == 1
    assert "Commit failed: Set Git user.name" in capsys.readouterr().err
    assert [path for path, _ in posts(app)] == ["/api/repository-setup"]


def test_push_without_an_origin_says_where_to_add_one(app, capsys):
    app["status"]["origin"] = False
    assert cli.main(["push", "a-game"]) == 1
    assert "no origin to push to" in capsys.readouterr().err
    assert posts(app) == []


def test_says_when_no_app_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path))
    assert cli.main(["commit", "a-game"]) == 1
    assert "isn't running" in capsys.readouterr().err
