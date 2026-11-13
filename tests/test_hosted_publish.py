"""Publishing a project to a remote hosted server (hosted_export.publish, POST /v1/p/<slug>/upload)."""
import shutil
import sqlite3
import subprocess

import httpx
import pytest

from decompnet import hosted_export
from decompnet.app import mcp_gateway
from tests.test_hosted import gateway, tool
from tests.test_hosted_pack import game

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")


@pytest.fixture
def server(tmp_path):
    folder = tmp_path / "server"
    tokens = {name: subprocess.run(gateway("add-user", "--dir", str(folder), "--name", name, "--role", role),
                                   capture_output=True, text=True, check=True).stdout.strip()
              for name, role in [("owner", "admin"), ("alice", "agent"), ("box", "verifier")]}
    process = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                               stdout=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{int(process.stdout.readline().split()[1])}"
    yield base, tokens, folder
    process.terminate()
    process.wait(5)


def claim_and_submit(base, token, function, score=0):
    receipt, _ = tool(base, token, "claim_function", function=function)
    claim = next(line.split(": ")[1] for line in receipt.splitlines() if line.startswith("claim_token: "))
    tool(base, token, "submit_result", function=function, claim_token=claim, source=f"int {function};", score=score)
    return claim


def test_publish_creates_then_updates_and_keeps_the_servers_work(tmp_path, server):
    base, tokens, folder = server
    net = game(tmp_path)
    first = hosted_export.publish(net, "game", base, tokens["owner"], log=lambda _: None)
    assert first["project"]["created"] and first["pack"]["kind"] == "pack"
    assert "1/30 functions matched" in tool(base, tokens["alice"], "project_info")[0]
    assert httpx.get(f"{base}/v1/p/game/pack", headers={"Authorization": f"Bearer {tokens['box']}"}).status_code == 200
    # Work on the server: f0 matched (verified), f1 held with a submission waiting.
    claim_and_submit(base, tokens["alice"], "f0")
    claim_and_submit(base, tokens["alice"], "f1", score=5)
    auth = {"Authorization": f"Bearer {tokens['box']}"}
    first_id = min(s["id"] for s in httpx.get(f"{base}/v1/p/game/pending", headers=auth).json()["submissions"])
    httpx.post(f"{base}/v1/p/game/verdicts", json={"id": first_id, "score": 0}, headers=auth).raise_for_status()
    # The owner changes the project: new assembly for f2, f3 gone.
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm='/* redone */ jr $ra' WHERE name='f2'")
        db.execute("UPDATE functions SET gone_mark='x' WHERE name='f3'")
    again = hosted_export.publish(net, "game", base, tokens["owner"], log=lambda _: None)
    assert not again["project"]["created"] and again["project"]["removed"] == 1
    info = tool(base, tokens["alice"], "project_info")[0]
    assert "2/30 functions matched" in info  # f0's server match stays
    assert "/* redone */" in tool(base, tokens["alice"], "get_function", function="f2", section="assembly")[0]
    assert "status removed" in tool(base, tokens["alice"], "get_function", function="f3")[0]
    claims = tool(base, tokens["alice"], "get_claims")[0]
    assert '"function":"f1"' in claims  # the claim and its waiting submission stay
    assert len(httpx.get(f"{base}/v1/p/game/pending", headers=auth).json()["submissions"]) == 1


def test_the_server_merges_exactly_as_a_local_export(tmp_path, server):
    base, tokens, folder = server
    net = game(tmp_path)
    hosted_export.publish(net, "game", base, tokens["owner"], log=lambda _: None)
    claim_and_submit(base, tokens["alice"], "f0")
    auth = {"Authorization": f"Bearer {tokens['box']}"}
    [pending] = httpx.get(f"{base}/v1/p/game/pending", headers=auth).json()["submissions"]
    httpx.post(f"{base}/v1/p/game/verdicts", json={"id": pending["id"], "score": 0}, headers=auth)
    local = tmp_path / "local.sqlite3"
    live = folder / "projects" / "game.sqlite3"
    db = sqlite3.connect(live)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    shutil.copy(live, local)  # the same live file, merged by Python here and by the server there
    with net.store.tx() as tx:
        tx.execute("UPDATE functions SET asm='changed' WHERE name='f5'")
        tx.execute("UPDATE functions SET gone_mark='x' WHERE name='f6'")
    fresh = tmp_path / "fresh.sqlite3"
    hosted_export.write_fresh(net, "game", fresh)
    data = fresh.read_bytes()
    hosted_export.merge(local, fresh)
    reply = httpx.post(f"{base}/v1/p/game/upload?kind=project", content=data,
                       headers={"Authorization": f"Bearer {tokens['owner']}"})
    assert reply.status_code == 200, reply.text

    def rows(path):
        db = sqlite3.connect(path)
        try:
            return (db.execute("SELECT * FROM functions ORDER BY id").fetchall(),
                    db.execute("SELECT * FROM meta ORDER BY key").fetchall(),
                    db.execute("SELECT * FROM contexts ORDER BY unit").fetchall())
        finally:
            db.close()
    assert rows(local) == rows(live)


def test_only_admins_publish_and_only_the_right_file(tmp_path, server):
    base, tokens, folder = server
    net = game(tmp_path)
    fresh = tmp_path / "fresh.sqlite3"
    hosted_export.write_fresh(net, "game", fresh)
    url = f"{base}/v1/p/game/upload?kind=project"
    assert httpx.post(url, content=fresh.read_bytes(), headers={"Authorization": f"Bearer {tokens['alice']}"}).status_code == 403
    owner = {"Authorization": f"Bearer {tokens['owner']}"}
    wrong = httpx.post(f"{base}/v1/p/other/upload?kind=project", content=fresh.read_bytes(), headers=owner)
    assert wrong.status_code == 400 and "not \"other\"" in wrong.json()["error"]
    assert httpx.post(url, content=b"not a database", headers=owner).status_code == 400
    as_pack = httpx.post(f"{base}/v1/p/game/upload?kind=pack", content=fresh.read_bytes(), headers=owner)
    assert as_pack.status_code == 400 and "is a project, not a pack" in as_pack.text
    assert not (folder / "projects" / "other.sqlite3").exists()
