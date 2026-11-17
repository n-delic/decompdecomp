"""`decomp agents`: the running app's helpers listed, started and restarted from the command line, asked as the
window asks (server.json's secret, as `decomp stop` and `decomp import` do)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decompnet.app import cli, window

SESSIONS = [{"id": "s1", "state": "paused", "agent": "claude-haiku-1", "model": "haiku", "why": "an error"},
            {"id": "s2", "state": "stopped", "agent": "codex-1", "model": "", "why": "stopped by you"},
            {"id": "s3", "state": "working", "agent": "claude-haiku-2", "model": "haiku", "why": ""}]


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    monkeypatch.setenv("DECOMP_HOME", str(home))
    seen = {"requests": [], "fail": None}

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
            return self.answer(200, {"sessions": SESSIONS})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen["requests"].append(("POST", self.path, body, self.headers.get("User-Agent")))
            if seen["fail"]:
                return self.answer(400, {"error": seen["fail"]})
            return self.answer(200, {"started": ["n1"]} if self.path == "/api/agents/start" else {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (home / ".decomp" / "server.json").write_text(json.dumps({"port": server.server_address[1], "secret": "s3cret", "pid": 1}))
    yield seen
    server.shutdown()


def test_starts_a_helper_with_its_model_as_the_window_would(app, capsys):
    assert cli.main(["agents", "start", "a-game", "--agent", "claude", "--model", "haiku"]) == 0
    method, path, body, agent = app["requests"][-1]
    assert (method, path) == ("POST", "/api/agents/start")
    assert body == {"kind": "claude", "slug": "a-game", "count": 1, "options": {"model": "haiku"}}
    assert agent == f"{window.TOKEN_AGENT}s3cret"
    assert "started 1 claude helper on a-game: n1" in capsys.readouterr().out


def test_lists_and_restarts_stopped_and_paused_helpers(app, capsys):
    assert cli.main(["agents", "list", "a-game"]) == 0
    assert "s1\tpaused" in capsys.readouterr().out
    assert app["requests"][-1][1] == "/api/live?slug=a-game&only=sessions"
    assert cli.main(["agents", "restart", "a-game"]) == 0
    assert [r[2] for r in app["requests"] if r[1] == "/api/agents/restart"] == [{"id": "s1"}, {"id": "s2"}]
    app["requests"].clear()
    assert cli.main(["agents", "restart", "a-game", "--paused"]) == 0
    assert [r[2] for r in app["requests"] if r[1] == "/api/agents/restart"] == [{"id": "s1"}]


def test_says_what_the_app_refused_and_when_none_runs(app, capsys, tmp_path, monkeypatch):
    app["fail"] = "choose a model this agent offers"
    assert cli.main(["agents", "start", "a-game", "--model", "nope"]) == 1
    assert "refused: choose a model this agent offers" in capsys.readouterr().err
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path / "elsewhere"))  # (no server.json: no wait)
    assert cli.main(["agents", "list", "a-game"]) == 1
    assert "isn't running" in capsys.readouterr().err
