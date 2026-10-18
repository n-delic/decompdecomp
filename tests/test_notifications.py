"""One notification centre for the whole app (REWRITE.md §3): the feed of every project's news, and the helpers
that pause in it."""

from starlette.testclient import TestClient

from decompnet.app import agents
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

from .test_agents import URL, _game
from .test_app_server import PORT, SECRET, WINDOW
from .test_loading import tiny_game


def test_the_feed_starts_now_and_tells_every_projects_news(tmp_path):
    """Opening the window starts the feed where it is: what happened before is history, never news. After that, the
    news of every project comes in order, with its project, and the claims agents make all the time stay out."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    tiny_game(net, "a")
    tiny_game(net, "b")
    net.record_event("a", "match", "func_0")
    start = net.events_after(None)
    assert start["events"] == [] and start["last"] >= 1
    net.record_event("a", "claim", "func_1")  # not news
    net.record_event("b", "paused", "the provider is out of credit")
    news = net.events_after(start["last"])
    assert [(e["slug"], e["project"], e["kind"], e["detail"]) for e in news["events"]] == [
        ("b", "b", "paused", "the provider is out of credit")]
    assert net.events_after(news["last"])["events"] == []
    assert [e["kind"] for e in net.recent_events()] == ["paused", "match"]  # the panel's history, newest first
    net.record_event("gone", "match", "func_0")  # a project removed meanwhile: nothing recorded, no error


def test_the_feed_answers_the_window(tmp_path):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net, "a")
    net.record_event("a", "match", "func_0")
    mcp, _, security, _ = build(net, tmp_path, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    start = client.get("/api/events").json()
    assert start["events"] == []
    net.record_event("a", "link", "src/a.c")
    assert [e["detail"] for e in client.get(f"/api/events?after={start['last']}").json()["events"]] == ["src/a.c"]
    assert [e["kind"] for e in client.get("/api/events?recent=1").json()["events"]] == ["link", "match"]


def test_a_helper_that_pauses_says_so_in_the_feed(tmp_path, monkeypatch):
    """A helper paused on an error it can't get past (out of credit, signed out) said so only in the view of its own
    project, and only while it was open. Now the notification centre tells it, whichever project is open."""
    monkeypatch.setattr(agents.Manager, "_prepare", lambda self, slug: None)
    monkeypatch.setattr(agents.Manager, "_run", lambda self, s: None)
    net = _game(tmp_path)
    m = agents.Manager(net, tmp_path, URL)
    (sid,) = m.start("claude", "game", 1, {"model": "opus"})
    net.local_agent(m.sessions[sid].agent_name)  # (its run, not started here, makes its identity)
    start = net.events_after(None)["last"]
    m._pause(m.sessions[sid], "the provider says the account is out of credit")
    (ev,) = net.events_after(start)["events"]
    assert (ev["kind"], ev["agent"], ev["slug"]) == ("paused", "claude-opus-1", "game")
    m._finish(m.sessions[sid], "nothing left to decompile here")
    assert [e["kind"] for e in net.events_after(start)["events"]] == ["paused", "stopped"]

