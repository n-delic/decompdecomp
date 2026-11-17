"""`decomp import`: a game made a project by the running app, as dropping it on the home page does. The command asks
the app with server.json's secret (as `decomp stop` does) and follows the import's job."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decompnet.app import cli, window


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A stand-in for the running app of a scratch home: /health, /api/import and /api/job, the requests kept."""
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    monkeypatch.setenv("DECOMP_HOME", str(home))
    seen: dict = {"requests": [], "polls": 0, "fail": None}

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
            seen["requests"].append(("GET", self.path, dict(self.headers)))
            seen["polls"] += 1
            if seen["polls"] == 1:  # a busy app: this poll times out
                import time
                time.sleep(0.3)
                return self.answer(200, {"state": "running", "log": []})
            if seen["polls"] < 3:
                return self.answer(200, {"state": "running", "log": ["splitting the ROM"]})
            return self.answer(200, {"state": "done", "log": ["splitting the ROM", "1,234 functions"],
                                     "result": {"slug": "a-racing-game"}})

        def do_POST(self):
            seen["requests"].append(("POST", self.path, dict(self.headers)))
            if seen["fail"]:
                return self.answer(400, {"error": seen["fail"]})
            return self.answer(200, {"job": "j1"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (home / ".decomp" / "server.json").write_text(json.dumps({"port": server.server_address[1], "secret": "s3cret", "pid": 1}))
    monkeypatch.setattr(cli, "IMPORT_POLL_SECONDS", 0.01)
    real_call = cli._call
    monkeypatch.setattr(cli, "_call", lambda info, path, body=None, timeout=10: real_call(
        info, path, body, timeout=0.1 if path.startswith("/api/job") else timeout))
    yield seen
    server.shutdown()


def test_the_running_app_imports_the_game_from_where_it_is(app, tmp_path, capsys):
    game = tmp_path / "game.z64"
    game.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    assert cli.main(["import", str(game), "--yes", "--name", "My Game"]) == 0
    method, path, headers = app["requests"][0]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    assert (method, urllib.parse.urlsplit(path).path) == ("POST", "/api/import")
    assert query == {"path": [str(game.resolve())], "confirmed": ["1"], "name": ["My Game"]}
    assert headers["User-Agent"] == window.TOKEN_AGENT + "s3cret" and headers["X-Decomp"] == "1"
    out = capsys.readouterr().out
    assert out.count("splitting the ROM") == 1 and "1,234 functions" in out and "imported: a-racing-game" in out
    assert app["polls"] >= 3  # the slow answer was asked again, not taken as the app gone


def test_without_confirming_its_your_copy_nothing_is_sent(app, tmp_path, capsys):
    game = tmp_path / "game.z64"
    game.write_bytes(bytes(64))
    assert cli.main(["import", str(game)]) == 1  # no terminal to ask in
    assert app["requests"] == [] and "your own copy" in capsys.readouterr().err


def test_the_apps_refusal_is_shown(app, tmp_path, capsys):
    game = tmp_path / "game.bin"
    game.write_bytes(bytes(64))
    app["fail"] = "this isn't a game decompdecomp knows"
    assert cli.main(["import", str(game), "--yes"]) == 1
    assert "not imported: this isn't a game decompdecomp knows" in capsys.readouterr().err


def test_a_missing_file_or_no_running_app_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path / "empty"))
    assert cli.main(["import", str(tmp_path / "nothing.z64"), "--yes"]) == 2
    game = tmp_path / "game.z64"
    game.write_bytes(bytes(64))
    assert cli.main(["import", str(game), "--yes"]) == 1
    assert "isn't running" in capsys.readouterr().err
