"""The project page's live feed: the overview kept between polls, sections sent only when they moved, and the
server-sent change stream (/api/stream) that tells the page when to ask."""

import asyncio
import json
import threading
import time

import pytest
from starlette.testclient import TestClient

from decompnet.app import window
from decompnet.app.engine import RomNetwork
from decompnet.app.live_sections import changed, compact
from decompnet.app.routes import projects as project_routes
from decompnet.app.server import build

from .test_loading import tiny_game

PORT, SECRET = 6498, "s3cret-for-stream-tests"
WINDOW = {"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}{SECRET}"}


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    (home / ".decomp").mkdir()
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, manager, security, _ = build(net, home / ".decomp", PORT, SECRET)
    net.autosave.close()
    asgi = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
    client = TestClient(asgi, base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    return client, net, asgi


def counting(net, monkeypatch):
    built = []
    original = type(net).overview

    def overview(self, slug, **kw):
        built.append(slug)
        return original(self, slug, **kw)
    monkeypatch.setattr(type(net), "overview", overview)
    return built


def test_live_overview_is_rebuilt_only_when_a_counter_moves(app, monkeypatch):
    _, net, _ = app
    proj = tiny_game(net, "cached-overview")
    built = counting(net, monkeypatch)
    first, version = net.live_overview("cached-overview")
    again, same = net.live_overview("cached-overview")
    assert built == ["cached-overview"] and same == version and again["functions"] == first["functions"] == 4
    # a function's change moves map_revision: the next poll rebuilds and shows it
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched', best_score=0 WHERE project_id=? AND name='func_0'", (proj["id"],))
    after, moved = net.live_overview("cached-overview")
    assert len(built) == 2 and moved != version and after["matched"] == 1
    # an event (last_activity) moves the key too
    net.record_event("cached-overview", "note", "hello")
    net.live_overview("cached-overview")
    assert len(built) == 3
    # what lives in memory is read on every poll without a rebuild, and changes the version
    net.autopilots["cached-overview"] = {"state": "running", "matched": 1, "stop": threading.Event()}
    try:
        running, version2 = net.live_overview("cached-overview")
        assert len(built) == 3 and running["autopilot"]["state"] == "running"
        assert version2.split(".")[0] == net.live_overview("cached-overview")[1].split(".")[0]
    finally:
        net.autopilots.pop("cached-overview", None)
    # and the TTL bounds anything no counter sees
    monkeypatch.setattr(type(net), "LIVE_OVERVIEW_TTL", 0.0)
    net.forget_live_overview("cached-overview")
    net.live_overview("cached-overview")
    net.live_overview("cached-overview")
    assert len(built) == 5


def test_compact_live_skips_unchanged_sections_without_hashing_the_overview(app):
    client, net, _ = app
    tiny_game(net, "compact-live")
    first = client.get("/api/live", params={"slug": "compact-live", "compact": "1"}).json()
    assert {"overview", "claims", "sessions"} <= set(first) and first["versions"]["overview"]
    second = client.get("/api/live", params={"slug": "compact-live", "compact": "1",
                                             "v": json.dumps(first["versions"])}).json()
    assert "overview" not in second and "claims" not in second and "sessions" not in second
    net.record_event("compact-live", "note", "hello")
    only = client.get("/api/live", params={"slug": "compact-live", "compact": "1", "only": "feed"}).json()
    assert "overview" not in only and "claims" not in only and "sessions" not in only and only["events"]
    # compact() takes the engine's version as it is: no digest of the overview's contents
    out = compact({"overview": object(), "claims": [], "sessions": [], "versions": {"overview": "v1"}},
                  json.dumps({"overview": "v1"}))
    assert "overview" not in out and out["versions"]["overview"] == "v1"


def test_live_signature_moves_with_each_section(app):
    _, net, _ = app
    proj = tiny_game(net, "signature-live")
    before = net.live_signature("signature-live")
    assert net.live_signature("signature-live") == before
    net.record_event("signature-live", "note", "hello")
    after = net.live_signature("signature-live")
    assert "feed" in changed(before, after) and "overview" in changed(before, after) and "claims" not in changed(before, after)
    aid = net.local_agent("tester")[0].id
    with net.store.tx() as db:
        fid = db.execute("SELECT id FROM functions WHERE project_id=? LIMIT 1", (proj["id"],)).fetchone()["id"]
        db.execute("INSERT INTO claims(function_id, agent_id, claimed_at, expires_at) VALUES(?,?,?,?)",
                   (fid, aid, time.time(), time.time() + 600))
    claimed = net.live_signature("signature-live")
    assert changed(after, claimed) == ["claims"]


def test_stream_refuses_requests_without_the_window_secret(app):
    client, _, _ = app
    anon = TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}")
    assert anon.get("/api/stream", params={"slug": "x"}).status_code == 401
    assert client.get("/api/stream", params={"slug": "no-such-project"}).status_code == 400


def test_stream_says_hello_then_what_changed(app, monkeypatch):
    client, net, _ = app
    tiny_game(net, "streamed")
    monkeypatch.setattr(project_routes, "STREAM_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(project_routes, "STREAM_SECONDS", 1.5)
    timer = threading.Timer(0.5, lambda: net.record_event("streamed", "note", "news"))
    timer.start()
    started = time.monotonic()
    response = client.get("/api/stream", params={"slug": "streamed"})
    timer.join()
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert time.monotonic() - started < 5
    messages = [block for block in response.text.split("\n\n") if block.startswith("event:")]
    kinds = [m.split("\n")[0].removeprefix("event: ") for m in messages]
    assert kinds[0] == "hello" and "changed" in kinds
    news = json.loads(messages[kinds.index("changed")].split("data: ", 1)[1])
    assert "feed" in news["sections"] and news["event"] > 0


def run_stream(asgi, receive_after, *, on_start=None, timeout=5):
    """Drive the ASGI app by hand: the stream's receive channel says the page went after `receive_after` s."""
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/api/stream", "raw_path": b"/api/stream", "query_string": b"slug=disconnects",
             "root_path": "", "server": ("127.0.0.1", PORT), "client": ("127.0.0.1", 1),
             "headers": [(b"host", f"127.0.0.1:{PORT}".encode()), (b"user-agent", WINDOW["User-Agent"].encode())]}
    sent = []

    async def main():
        began = asyncio.get_running_loop().time()
        asked = False

        async def receive():
            nonlocal asked
            if not asked:
                asked = True
                return {"type": "http.request", "body": b"", "more_body": False}
            if receive_after is None:
                await asyncio.sleep(3600)
            await asyncio.sleep(max(0, receive_after - (asyncio.get_running_loop().time() - began)))
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if on_start and message["type"] == "http.response.start":
                on_start()
        await asyncio.wait_for(asgi(scope, receive, send), timeout)
    started = time.monotonic()
    asyncio.run(main())
    return sent, time.monotonic() - started


def test_stream_ends_when_the_page_goes(app, monkeypatch):
    _, net, asgi = app
    tiny_game(net, "disconnects")
    monkeypatch.setattr(project_routes, "STREAM_CHECK_SECONDS", 0.05)
    sent, took = run_stream(asgi, 0.3)
    assert took < 3 and sent[0]["status"] == 200
    assert b"event: hello" in b"".join(m.get("body", b"") for m in sent)


def test_stream_ends_on_shutdown(app, monkeypatch):
    _, net, asgi = app
    monkeypatch.setattr(project_routes, "STREAM_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(net, "shutting_down", False, raising=False)
    sent, took = run_stream(asgi, None, on_start=lambda: setattr(net, "shutting_down", True))
    assert took < 3 and sent[-1]["type"] == "http.response.body" and not sent[-1].get("more_body")


def test_page_follows_the_stream_and_backs_off_without_it():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("needs node")
    subprocess.run(["node", "tests/gui_live_stream.js"], check=True, capture_output=True, text=True)
