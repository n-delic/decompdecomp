"""The window's read-only pages answered by the reader process (app/window_reads.py, app/tool_reader.py) exactly
as the app answers them, and only for the app's own window."""
import os
import stat
import sys
import time

import httpx
import pytest
from starlette.testclient import TestClient

from decompnet.app import mcp_gateway, tool_reader, window, window_reads
from tests.test_claims import _game

PORT, SECRET = 6497, "window-reads-secret"
WINDOW = {"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}{SECRET}"}
# Every allow-listed read, as the window asks for it.
READS = ["/api/stats?slug=game", "/api/knowledge?slug=game", "/api/attempt-history?slug=game&function=f1",
         "/api/projects?brief=1"]


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    """The app (its own routes, from server.build) and a reader process over the same databases."""
    from decompnet.app.server import build
    tmp_path = tmp_path_factory.mktemp("window-reads")
    net, _pid = _game(tmp_path, [("f1", 4, "open"), ("f2", 6, "matched")])
    agent, _ = net.local_agent("reads-helper")
    fn = net._function("f1", "game")
    net._record(fn, agent, 10, "int f1(void){return 1;}", 1)
    net._record(fn, agent, 4, "int f1(void){return 2;}", 1)
    net.knowledge.share(agent, "game", "Loops here count down; write them as do-while.")
    (tmp_path / ".decomp").mkdir(exist_ok=True)
    mcp, manager, security, _ = build(net, tmp_path / ".decomp", PORT, SECRET)
    net.autosave.close()
    app = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                     base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    routes = {"assignments": {}, "manual": {}, "app_only": [], "tasks": {}, "busy": [], "preparing": False}
    reports = []
    reader = tool_reader.Reader(net, tmp_path / "internal", {"secret": SECRET, "port": PORT})
    reader.start()
    reader.attach(lambda: routes | {"at": time.time()}, reports.append)
    deadline = time.time() + 90
    while reader.process is None and time.time() < deadline:
        time.sleep(0.1)
    if reader.process is None:
        pytest.fail(f"the reader didn't start: {(tmp_path / 'internal' / 'logs' / 'tool-reader.log').read_text()}")
    reader.changed()
    time.sleep(0.3)
    yield app, net, reader, routes, reports
    reader.stop()


def ask(reader, path, headers=None, host=f"127.0.0.1:{PORT}"):
    return httpx.get(f"{reader.window_url}{path}", headers={"Host": host} | WINDOW | (headers or {}), timeout=60)


@pytest.mark.parametrize("path", READS)
def test_answers_each_read_exactly_as_the_app(both, path):
    app, net, reader, *_ = both
    mine = app.get(path)
    assert mine.status_code == 200, mine.text
    theirs = ask(reader, path)
    assert theirs.status_code == 200 and theirs.headers.get(tool_reader.WINDOW_ANSWERED) == "answered", theirs.text
    assert theirs.content == mine.content
    assert theirs.headers["content-type"] == mine.headers["content-type"]


def test_the_reads_say_something(both):
    """(The comparison above isn't of two empty answers.)"""
    app, *_ = both
    assert app.get("/api/knowledge?slug=game").json()["items"][0]["text"].startswith("Loops here")
    assert len(app.get("/api/attempt-history?slug=game&function=f1").json()["attempts"]) == 2
    assert app.get("/api/projects?brief=1").json() == [{"slug": "game", "name": "Game"}]
    assert any(row["agent"] == "reads-helper" for row in app.get("/api/stats?slug=game").json()["agents"])


def test_sees_what_the_app_changes(both):
    app, net, reader, *_ = both
    before = ask(reader, "/api/attempt-history?slug=game&function=f2").json()
    agent, _ = net.local_agent("reads-helper")
    net._record(net._function("f2", "game"), agent, 0, "int f2(void){return 3;}", 1)
    after = ask(reader, "/api/attempt-history?slug=game&function=f2")
    assert len(after.json()["attempts"]) == len(before["attempts"]) + 1
    assert after.content == app.get("/api/attempt-history?slug=game&function=f2").content


def test_tells_the_app_the_project_was_viewed(both):
    _, net, reader, _, reports = both
    reports.clear()
    ask(reader, "/api/stats?slug=game")
    deadline = time.time() + 5
    while {"viewed": "game"} not in reports and time.time() < deadline:
        time.sleep(0.05)
    assert {"viewed": "game"} in reports
    tool_reader.apply_report(net, None, {"viewed": "game"})
    assert "game" in net.project_activity.viewed


@pytest.mark.parametrize("case", ["no_secret", "wrong_secret", "other_host", "origin", "not_a_brief_list", "busy",
                                  "stale", "preparing", "unknown_project", "bad_query"])
def test_declines_what_the_app_should_answer(both, case):
    _, _, reader, routes, reports = both
    path, headers, host = "/api/stats?slug=game", {}, f"127.0.0.1:{PORT}"
    saved = dict(routes)
    try:
        if case == "no_secret":
            headers = {"User-Agent": "Mozilla/5.0 AppleWebKit/605.1.15"}  # a browser, signed in or not
        elif case == "wrong_secret":
            headers = {"User-Agent": f"Mozilla/5.0 {window.TOKEN_AGENT}not-the-secret"}
        elif case == "other_host":
            host = "evil.example:6497"
        elif case == "origin":
            headers = {"Origin": f"http://127.0.0.1:{PORT}"}
        elif case == "not_a_brief_list":
            path = "/api/projects?since=abc"
        elif case == "busy":
            routes["busy"] = ["game"]
        elif case == "preparing":
            routes["preparing"] = True
        elif case == "unknown_project":
            path = "/api/stats?slug=nope"
        elif case == "bad_query":
            path = "/api/attempt-history?slug=game&function=f1&before=x"
        if case == "stale":
            reader.routes = lambda: saved | {"at": time.time() - 60}
        reader.changed()
        time.sleep(0.3)
        reply = ask(reader, path, headers, host)
        assert reply.status_code == 421 and tool_reader.WINDOW_ANSWERED not in reply.headers
    finally:
        routes.clear()
        routes.update(saved)
        reader.routes = lambda: routes | {"at": time.time()}
        reader.changed()
        time.sleep(0.3)


def test_answers_only_the_allow_list(both):
    _, _, reader, *_ = both
    for path in ("/api/live?slug=game", "/api/map?slug=game", "/api/state", "/"):
        assert ask(reader, path).status_code in (404, 405, 421)
    assert httpx.post(f"{reader.window_url}/api/stats?slug=game", headers={"Host": f"127.0.0.1:{PORT}"} | WINDOW,
                      timeout=30).status_code in (405, 421)
    assert set(window_reads.GATEWAY_READS) == {"/api/stats", "/api/knowledge", "/api/attempt-history",
                                                 "/api/projects?brief=1"}


def test_a_reader_without_the_window_answers_none_of_it(tmp_path):
    net, _ = _game(tmp_path, [("f1", 4, "open")])
    reader = tool_reader.Reader(net, tmp_path / "internal")
    assert reader.window_url is None


# ------------------------------------------------------------------------------------------- the window's address
def test_the_window_uses_the_apps_address_without_a_working_gateway(tmp_path):
    w = window.Window(tmp_path, "http://127.0.0.1:6474/", SECRET, lambda: None)
    assert w.page_url() == "http://127.0.0.1:6474/"
    w.through = lambda: None  # (no window listener, or it didn't answer)
    assert w.page_url() == "http://127.0.0.1:6474/"
    w.through = lambda: "http://127.0.0.1:7123/"
    assert w.page_url() == "http://127.0.0.1:7123/"

    def broken():
        raise RuntimeError("boom")
    w.through = broken
    assert w.page_url() == "http://127.0.0.1:6474/"


@pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for an older gateway")
def test_an_older_gateway_still_serves_the_helpers_and_the_window_uses_the_app(tmp_path):
    """A downloaded gateway from before the window listener rejects its options: the app starts it without them."""
    fake = tmp_path / "old-gateway"
    fake.write_text("#!/bin/sh\n"
                    'for a in "$@"; do case "$a" in --window-*) echo "unknown option $a" >&2; exit 2;; esac; done\n'
                    'echo "LISTENING 45678"\n'
                    "cat >/dev/null\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    gw = mcp_gateway.Gateway(fake, "http://127.0.0.1:1/mcp", tmp_path / "gw.log",
                             window={"upstream": "http://127.0.0.1:1", "reader": None, "reads": []})
    try:
        gw.start()
        assert gw.port == 45678 and gw.window is None and gw.window_port is None
        assert gw.window_url() is None
        assert "--window-listen" not in gw._command(gw.port)
    finally:
        gw.stop()


def test_window_listener_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("DECOMP_WINDOW_GATEWAY", "0")
    assert not mcp_gateway.window_wanted()
    monkeypatch.delenv("DECOMP_WINDOW_GATEWAY")
    assert mcp_gateway.window_wanted()


def test_the_secret_stays_off_the_command_line(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [("f1", 4, "open")])
    seen = {}

    class Stop(Exception):
        pass

    def popen(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs.get("env") or os.environ
        raise Stop
    monkeypatch.setattr(tool_reader.subprocess, "Popen", popen)
    reader = tool_reader.Reader(net, tmp_path / "internal", {"secret": SECRET, "port": PORT})
    with pytest.raises(Stop):
        reader._launch(12345)
    assert not any(SECRET in a for a in seen["argv"])
    assert SECRET in seen["env"][tool_reader.WINDOW_ENV]
