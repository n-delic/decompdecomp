"""Room presence continues while receiving work is slow, without requiring a game or compiler."""

import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from decompnet.app import collab
from decompnet.network import NetworkError


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setattr(collab, "HEARTBEAT_EVERY", 0.01)
    clients, links = [], []

    def make(handler, fork=False):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        net = SimpleNamespace(project_row=lambda _: {"config": "{}"}, fork_slug=lambda _: fork)
        link = collab.Link(net, "connection-test", "https://host.example", "room", "secret", "guest", client)
        links.append(link)
        return link

    yield make
    for link in links:
        link.stop()
    for thread in threading.enumerate():
        if thread.name in ("link-connection-test", "room-heartbeat-connection-test"):
            thread.join(timeout=2)
            assert not thread.is_alive()
    for client in clients:
        client.close()


@pytest.mark.parametrize("fork", [False, True])
def test_presence_continues_during_slow_sync(connection, fork):
    applying, release, alive = threading.Event(), threading.Event(), threading.Event()
    requests = []

    def host(request):
        assert request.url.path == "/collab/whoami"
        assert request.url.params["room"] == "room"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.extensions["timeout"]["read"] == collab.HEARTBEAT_TIMEOUT
        requests.append(1)
        if applying.is_set() and len(requests) > 1:
            alive.set()
        return httpx.Response(200, json={"name": "guest", "room": "room"})

    link = connection(host, fork)
    link.last_ok = time.time() - 60
    link.synced = True

    def slow_sync():
        applying.set()
        release.wait(2)

    link.sync = slow_sync
    try:
        link.run()
        assert applying.wait(1)
        assert alive.wait(1)  # the heartbeat reaches the host while sync is still blocked
        assert link.status()["state"] == "connected"
        link.refused, link.error = True, collab.WAITING
        assert link.status()["state"] == "waiting"
        assert link.error == collab.WAITING
        link.refused, link.synced = False, False
        assert link.status()["state"] == "syncing"  # a heartbeat doesn't mean the work has arrived
    finally:
        link.stop()
        release.set()


def test_sync_retries_and_logs_recovery(connection, caplog):
    recovered = threading.Event()
    link = connection(lambda _: httpx.Response(200, json={}))
    calls = []

    def sync():
        calls.append(1)
        if len(calls) < 3:
            raise NetworkError("can't reach the host (ReadTimeout)")
        link.stop()
        recovered.set()

    link.sync = sync
    with caplog.at_level("INFO", logger="decompdecomp"):
        link.run(every=0.01)
        assert recovered.wait(1)
        for thread in threading.enumerate():
            if thread.name == "link-connection-test":
                thread.join(timeout=1)
    # Repeated failures don't bury useful diagnostics in a line every two seconds.
    assert sum("sync failed" in r.message for r in caplog.records) == 1
    assert any("sync recovered" in r.message for r in caplog.records)
    assert "secret" not in caplog.text


def test_failed_heartbeat_does_not_keep_room_online(connection, caplog):
    failed = threading.Event()

    def host(request):
        failed.set()
        raise httpx.ConnectError("unreachable", request=request)

    link = connection(host)
    link.last_ok = time.time() - 60
    link.error = "local sync error"
    link.sync = lambda: None
    with caplog.at_level("WARNING", logger="decompdecomp"):
        link.run()
        assert failed.wait(1)
        link.stop()
    assert link.status()["state"] == "offline"
    assert link.last_contact == 0
    assert link.error == "local sync error"


def test_join_shares_only_stronger_saved_rom_progress():
    class RoomLink:
        cursor = {}

        def __init__(self):
            self.attempts = []
            self.reports = []
            self.synced = False

        def _req(self, method, route, params):
            assert method == "GET" and route == "/collab/state" and params["fresh"] == 1
            return {
                "matched": [{"vram": 0x1000, "name": "already_matched"}],
                "functions": [
                    {"vram": 0x1000, "size": 4, "name": "already_matched"},
                    {"vram": 0x2000, "size": 4, "name": "saved_match"},
                    {"vram": 0x3000, "size": 4, "name": "better_attempt"},
                    {"vram": 0x4000, "size": 4, "name": "worse_attempt"},
                ],
                "best": [{"vram": 0x4000, "score": 3}],
            }

        def attempt(self, agent, function, source):
            self.attempts.append((function, source))
            return {"matched": True}

        def call(self, route, agent, **data):
            self.reports.append((route, data))

        def sync(self):
            self.synced = True

    class Net:
        def local_agent(self, name):
            assert name == "saved-progress"
            return SimpleNamespace(name=name), None

    candidates = [
        {"name": "already_matched", "vram": 0x1000, "size": 4, "status": "matched",
         "matched_source": "already"},
        {"name": "saved_match", "vram": 0x2000, "size": 4, "status": "matched",
         "matched_source": "match"},
        {"name": "better_attempt", "vram": 0x3000, "size": 4, "status": "open",
         "best_score": 2, "best_source": "better"},
        {"name": "worse_attempt", "vram": 0x4000, "size": 4, "status": "open",
         "best_score": 4, "best_source": "worse"},
    ]
    room = RoomLink()
    log = []

    collab._share_saved_progress(Net(), "project", room, candidates, fork=False,
                                 best_comparable=True, log=log.append)

    assert room.attempts == [("saved_match", "match")]
    assert room.reports == [("/collab/best", {"function": "better_attempt", "source": "better", "score": 2})]
    assert room.synced
    assert any("host confirmed 1 saved match" in message for message in log)


def test_a_rom_guest_can_wait_for_approval_after_joining(monkeypatch):
    hello = {"kind": "rom", "room": "game", "sha1": "same", "title": "Game", "game_code": "ABCD",
             "region": "USA", "functions": 1, "matched": 0,
             "compiler": {"toolchain": "gcc", "flags": ""}}
    monkeypatch.setattr(collab, "_hello", lambda *args: ("https://host.example", "invite", hello))
    monkeypatch.setattr("decompnet.consoles.identify", lambda _: SimpleNamespace(
        read_game=lambda *_: SimpleNamespace(info={"sha1": "same"})))
    monkeypatch.setattr(collab, "_saved_progress", lambda *args, **kwargs: [])
    requests = []

    def host(request):
        requests.append(request.url.path)
        if request.url.path == "/collab/join":
            return httpx.Response(200, json={"token": "secret", "name": "guest", "waiting": True})
        return httpx.Response(400)

    class Net:
        store = SimpleNamespace(all=lambda *_: [])

        def import_rom(self, *args, **kwargs):
            return "game"

        def project_row(self, slug):
            assert slug == "game"
            return {"config": json.dumps({"compiler": hello["compiler"]})}

        def link_project(self, *args, **kwargs):
            return SimpleNamespace(run=lambda: None)

    with httpx.Client(transport=httpx.MockTransport(host)) as client:
        slug = collab.join_room(Net(), "https://host.example/join/invite", "guest", b"rom",
                                log=lambda *_: None, client=client, start=False)

    assert slug == "game"
    assert requests == ["/collab/join"]


def test_heartbeat_recovers_without_rejoining(connection, caplog):
    recovered = threading.Event()
    requests = []

    def host(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            raise httpx.ReadTimeout("slow host", request=request)
        if len(requests) == 3:
            recovered.set()  # the previous heartbeat has already updated last_contact and logged recovery
        return httpx.Response(200, json={"name": "guest", "room": "room"})

    link = connection(host)
    link.synced = True
    link.last_ok = time.time() - 60
    link.sync = lambda: None
    with caplog.at_level("INFO", logger="decompdecomp"):
        link.run()
        assert recovered.wait(1)
        link.stop()
    assert link.status()["state"] == "connected"
    assert set(requests) == {"/collab/whoami"}
    assert any("heartbeat recovered" in r.message for r in caplog.records)


@pytest.mark.parametrize("status", [400, 502])
def test_rejected_heartbeat_does_not_count_as_contact(connection, status):
    answered = threading.Event()

    def host(request):
        answered.set()
        return httpx.Response(status, json={"error": "not available"})

    link = connection(host)
    link.last_ok = time.time() - 60
    link.sync = lambda: None
    link.run()
    assert answered.wait(1)
    link.stop()
    assert link.last_contact == 0
    assert link.status()["state"] == "offline"
