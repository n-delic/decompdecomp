"""Verified hosted matches taken into the app's full project (app/hosted_sync.py)."""
import stat
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from decompnet import hosted_export
from decompnet.app import hosted_sync, mcp_gateway, room_matches
from tests.test_hosted import gateway, project, tool


def test_links_are_private_and_editable(tmp_path):
    hosted_sync.link(tmp_path, "game", "https://h.example/", "dd_secret")
    hosted_sync.link(tmp_path, "other", "https://h.example", "dd_2", remote="o")
    path = hosted_sync.links_file(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [(i["slug"], i["remote"], i["server"]) for i in hosted_sync.read_links(tmp_path)] == [
        ("game", "game", "https://h.example"), ("other", "o", "https://h.example")]
    assert hosted_sync.unlink(tmp_path, "game") and not hosted_sync.unlink(tmp_path, "game")
    args = SimpleNamespace(slug="x", server=None, token=None, remote=None, remove=False)
    assert hosted_sync.main(args, tmp_path) == 2


def fake_server(pages):
    asked = []

    def get(url, token):
        asked.append((url, token))
        return pages.pop(0) if pages else {"matches": []}
    return get, asked


def test_new_matches_are_judged_here_and_credited(tmp_path, monkeypatch):
    net = project(tmp_path)
    judged = []

    def verify(net_, slug, agent, function, source):
        judged.append((agent.name, function, source))
        if "real" in source:
            with net.store.tx() as db:
                db.execute("UPDATE functions SET status='matched' WHERE name=?", (function,))
        return {"matched": "real" in source}
    monkeypatch.setattr(room_matches, "verify", verify)
    pages = [{"matches": [
        {"function": "f1", "vram": 0x80001100, "source": "real f1", "by": "alice b", "matched_at": 10.5},
        {"function": "f2", "vram": 0x80001200, "source": "not here", "by": "bob", "matched_at": 11.0},
        {"function": "f3", "vram": 0x1, "source": "real", "by": "bob", "matched_at": 12.0},  # another function here
        {"function": "f29", "vram": 0x80001000 + 0x100 * 29, "source": "real", "by": "bob", "matched_at": 13.0},
    ]}]
    get, asked = fake_server(pages)
    counts = hosted_sync.pull(net, {"slug": "game", "server": "https://h", "token": "t", "since": 0}, get=get)
    assert counts == {"recorded": 1, "already": 1, "not_reproduced": 1, "skipped": 1, "since": 13.0}
    assert judged == [("hosted.alice-b", "f1", "real f1"), ("hosted.bob", "f2", "not here")]
    assert net.store.one("SELECT owner,origin FROM agents WHERE name='hosted.alice-b'")["origin"] == "hosted"
    assert asked == [("https://h/v1/p/game/matches?since=0.0", "t")]


def test_a_round_remembers_where_it_got_to(tmp_path, monkeypatch):
    net = project(tmp_path)
    internal = tmp_path / "internal"
    internal.mkdir()
    hosted_sync.link(internal, "game", "https://h", "t")
    monkeypatch.setattr(hosted_sync, "pull", lambda net, item: {"recorded": 0, "already": 0, "not_reproduced": 0,
                                                                 "skipped": 0, "since": 42.0})
    sync = hosted_sync.Sync(net, internal)
    sync.round()
    assert hosted_sync.read_links(internal)[0]["since"] == 42.0 and sync.last["game"]["since"] == 42.0
    monkeypatch.setattr(hosted_sync, "pull", lambda net, item: (_ for _ in ()).throw(httpx.ConnectError("down")))
    sync.round()
    assert "down" in sync.last["game"]["error"]


@pytest.mark.skipif(mcp_gateway.built() is None, reason="the gateway isn't built (cargo build --release in gateway/)")
def test_a_match_made_on_the_server_reaches_the_project(tmp_path, monkeypatch):
    net = project(tmp_path)
    folder = tmp_path / "hosted"
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    token = {name: subprocess.run(gateway("add-user", "--dir", str(folder), "--name", name, "--role", role),
                                  capture_output=True, text=True, check=True).stdout.strip()
             for name, role in [("alice", "agent"), ("checker", "verifier")]}
    server = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                              stdout=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{int(server.stdout.readline().split()[1])}"
    try:
        receipt, _ = tool(base, token["alice"], "claim_function", function="f3")
        claim = next(line.split(": ")[1] for line in receipt.splitlines() if line.startswith("claim_token: "))
        tool(base, token["alice"], "submit_result", function="f3", claim_token=claim, source="int f3(void){}", score=0)
        auth = {"Authorization": f"Bearer {token['checker']}"}
        [pending] = httpx.get(f"{base}/v1/p/game/pending", headers=auth).json()["submissions"]
        httpx.post(f"{base}/v1/p/game/verdicts", json={"id": pending["id"], "score": 0}, headers=auth).raise_for_status()
        recorded = []

        def verify(net_, slug, agent, function, source):
            recorded.append((agent.name, function, source))
            return {"matched": True}
        monkeypatch.setattr(room_matches, "verify", verify)  # (the app's judge needs the game's real compiler)
        counts = hosted_sync.pull(net, {"slug": "game", "server": base, "token": token["checker"], "since": 0})
        assert counts["recorded"] == 1 and recorded == [("hosted.alice", "f3", "int f3(void){}")]
        again = hosted_sync.pull(net, {"slug": "game", "server": base, "token": token["checker"], "since": counts["since"]})
        assert again["recorded"] == 0  # only what's new
    finally:
        server.terminate()
        server.wait(5)
