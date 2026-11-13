"""Map caching must not swallow the page's forced refresh after a rename, split or missed live poll."""

import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

from .test_app_server import PORT, SECRET, WINDOW
from .test_loading import tiny_game


@pytest.fixture
def mapped_game(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    tiny_game(net, functions=80)  # large enough to exercise the compressed response too
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    calls = []
    original = net.function_map

    def function_map(slug):
        calls.append(slug)
        return original(slug)

    monkeypatch.setattr(net, "function_map", function_map)
    with TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                    base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW) as client:
        yield client, net, manager, calls


def test_incremental_map_cache_reuses_both_encodings_and_invalidates_on_progress(mapped_game):
    client, net, _, calls = mapped_game
    first = client.get("/api/map?slug=game", headers={"Accept-Encoding": "gzip"})
    assert first.status_code == 200 and first.headers["Content-Encoding"] == "gzip"
    before = first.json()
    for encoding in ("identity", "gzip"):
        cached = client.get("/api/map?slug=game&since=older", headers={"Accept-Encoding": encoding})
        assert cached.json() == before
        assert cached.headers.get("Content-Encoding") == ("gzip" if encoding == "gzip" else None)
        assert cached.headers["Vary"] == "Accept-Encoding"
    assert calls == ["game"]  # not just the existing since==sig short-circuit
    assert client.get(f"/api/map?slug=game&since={before['sig']}").json() == {
        "sig": before["sig"], "same": True,
    }
    assert calls == ["game"]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=7 WHERE name='func_0'")
    after = client.get(f"/api/map?slug=game&since={before['sig']}").json()
    assert after["sig"] != before["sig"] and "same" not in after
    assert calls == ["game", "game"]
    assert client.get(f"/api/map?slug=game&since={before['sig']}").json() == after
    assert calls == ["game", "game"]


@pytest.mark.parametrize("change,field,value", [
    ("name='renamed_0'", "name", "renamed_0"),
    ("unit='new/unit'", "unit", "new/unit"),
    ("size=32", "size", 32),
    ("n_insns=8", "insns", 8),
    ("attempts=12", "attempts", 12),
    ("status='nosource', meta='{\"gone\":1}'", None, None),
])
def test_forced_map_refresh_sees_changes_outside_the_signature(mapped_game, change, field, value):
    client, net, _, calls = mapped_game
    before = client.get("/api/map?slug=game").json()
    with net.store.tx() as db:
        db.execute(f"UPDATE functions SET {change} WHERE name='func_0'")
    assert net.map_signature("game") == before["sig"]  # these changes arrive through events/live
    after = client.get("/api/map?slug=game").json()  # the page deliberately omits since on a forced refresh
    assert calls == ["game", "game"]
    rows = [dict(zip(after["keys"], row)) for row in after["rows"]]
    if field is None:
        assert len(rows) == 79 and not any(row["name"] == "func_0" for row in rows)
    else:
        row = next(row for row in rows if row["vram"] == 0x100000)
        assert row[field] == value
    # Other tabs with an older signature must get the newly refreshed payload, not the old cached one.
    assert client.get("/api/map?slug=game&since=older").json() == after
    assert calls == ["game", "game"]


def test_removing_a_project_discards_its_cached_map(mapped_game, monkeypatch):
    client, net, _, calls = mapped_game
    monkeypatch.setattr(net, "map_signature", lambda slug: "same-revision")
    client.get("/api/map?slug=game")
    removed = client.post("/api/delete", json={"slug": "game"}, headers={"X-Decomp": "1"})
    assert removed.status_code == 200, removed.text
    tiny_game(net, functions=1)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='replacement'")
    after = client.get("/api/map?slug=game&since=older").json()
    assert len(after["rows"]) == 1 and after["rows"][0][after["keys"].index("name")] == "replacement"
    assert calls == ["game", "game"]


def test_reimport_discards_a_cached_map_even_if_its_signature_doesnt_change(mapped_game, tmp_path, monkeypatch):
    client, net, manager, calls = mapped_game
    before = client.get("/api/map?slug=game").json()
    game = tmp_path / "owned-game.rom"
    game.write_bytes(b"test input, not game data")
    monkeypatch.setattr("decompnet.app.routes.projects.consoles.identify", lambda path: SimpleNamespace(id="ps2"))
    monkeypatch.setattr(manager, "stop_project", lambda slug: None)

    def reimport(slug, path, **kwargs):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET name='renamed_0' WHERE name='func_0'")
        return {"slug": slug}

    monkeypatch.setattr(net, "reimport_rom", reimport)
    response = client.post("/api/reimport", json={"slug": "game", "path": str(game), "confirmed": True},
                           headers={"X-Decomp": "1"})
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    for _ in range(100):
        result = client.get(f"/api/job?id={job}").json()
        if result["state"] != "running":
            break
        time.sleep(0.01)
    assert result["state"] == "done", result
    assert net.map_signature("game") == before["sig"]
    after = client.get("/api/map?slug=game&since=older").json()
    assert after["rows"][0][after["keys"].index("name")] == "renamed_0"
    assert calls == ["game", "game"]
