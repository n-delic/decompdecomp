"""Room presence continues while receiving work is slow, without requiring a game or compiler."""

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
