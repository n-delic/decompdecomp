"""Hosted projects: exported small (hosted_export.py) and served by the gateway alone (gateway/src/hosted.rs).
Agents on other machines claim, fetch what they need to compile on their own computer and submit; only a verifier's
compile marks a function matched; re-exporting into a running server keeps the server's work."""
import json
import sqlite3
import subprocess
import threading

import httpx
import pytest

from decompnet import hosted_export
from decompnet.app import mcp_gateway
from decompnet.app.engine import RomNetwork

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")
COMPILER = {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2", "-G0"], "cmd": "/Users/owner/private/cc"}


def project(tmp_path, functions=30):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    config = {"kind": "rom", "platform": "n64", "compiler": COMPILER, "instructions": "Name things plainly.",
              "compiler_ranges": [{"start": 0x80002000, "end": 0x80003000, "toolchain": "ido-7.1", "flags": ["-O1"]}]}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 1, str(tmp_path), json.dumps(config))).lastrowid
        for k in range(functions):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram,target,relocs,asm) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (pid, f"f{k}", 4 * (k + 1), k + 1, "matched" if k == 29 else "open", "src/a.c",
                        0x80001000 + 0x100 * k, bytes([k, 0x03, 0xE0, 0x08]), "[]", f"/* f{k} */ jr $ra"))
        db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                   "VALUES(?,3,'typedef int s32;',NULL,'',0,'')", (pid,))
    return net


def gateway(*args):
    return [str(mcp_gateway.built()), "hosted", *args]


@pytest.fixture
def hosted(tmp_path):
    net = project(tmp_path)
    folder = tmp_path / "hosted"
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    tokens = {name: subprocess.run(gateway("add-user", "--dir", str(folder), "--name", name, "--role", role),
                                   capture_output=True, text=True, check=True).stdout.strip()
              for name, role in [*((f"agent{i}", "agent") for i in range(30)), ("checker", "verifier")]}
    server = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                              stdout=subprocess.PIPE, text=True)
    port = int(server.stdout.readline().split()[1])
    yield net, folder, tokens, f"http://127.0.0.1:{port}"
    server.terminate()
    server.wait(5)


def mcp(base, token, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    reply = httpx.post(f"{base}/p/game/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    reply.raise_for_status()
    return reply.json()["result"]


def tool(base, token, name, **arguments):
    result = mcp(base, token, "tools/call", {"name": name, "arguments": arguments})
    return result["content"][0]["text"], result["isError"]


def test_the_export_is_small_and_keeps_owner_paths_private(tmp_path):
    net = project(tmp_path)
    path = tmp_path / "out" / "projects" / "game.sqlite3"
    counts = hosted_export.export(net, "game", path, log=lambda _: None)
    assert counts["functions"] == 30 and counts["contexts"] == 1
    db = sqlite3.connect(path)
    meta = dict(db.execute("SELECT key,value FROM meta"))
    assert meta["format"] == hosted_export.FORMAT and meta["name"] == "Game"
    assert json.loads(meta["compiler"]) == {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2", "-G0"]}  # no owner paths
    assert "/Users/owner" not in path.read_bytes().decode("latin-1")
    ranged = json.loads(db.execute("SELECT compiler FROM functions WHERE vram=0x80002000").fetchone()[0])
    assert ranged == {"toolchain": "ido-7.1", "flags": ["-O1"]}  # each function's own compiler
    assert db.execute("SELECT target FROM functions WHERE name='f1'").fetchone()[0] == bytes([1, 3, 0xE0, 8])
    import zlib
    assert zlib.decompress(db.execute("SELECT header FROM contexts WHERE unit=''").fetchone()[0]) == b"typedef int s32;"


def test_an_agent_claims_compiles_elsewhere_and_a_verifier_confirms(hosted):
    net, folder, tokens, base = hosted
    agent = tokens["agent0"]
    assert mcp(base, agent, "initialize", {"protocolVersion": "2025-06-18"})["serverInfo"]["name"] == "decomp"
    names = {t["name"] for t in mcp(base, agent, "tools/list")["tools"]}
    assert {"claim_function", "submit_result", "get_function", "release_function"} <= names
    info, _ = tool(base, agent, "project_info")
    assert "1/30 functions matched" in info and "Name things plainly." in info
    receipt, error = tool(base, agent, "claim_function")
    assert not error and receipt.startswith("CLAIMED f0 @0x80001000")
    token = next(line.split(": ")[1] for line in receipt.splitlines() if line.startswith("claim_token: "))
    target, _ = tool(base, agent, "get_function", function="f0", section="target")
    assert "target (4 bytes, hex): 0003e008" in target
    assert "typedef int s32;" in tool(base, agent, "get_function", function="f0", section="context")[0]
    reply, error = tool(base, agent, "submit_result", function="f0", claim_token=token, source="void f0(void){}", score=0)
    assert not error and "once a verifier" in reply
    # Not matched until verified.
    assert "matched" not in tool(base, agent, "get_function", function="f0")[0].split("\n")[1]
    pending = httpx.get(f"{base}/v1/p/game/pending", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    [submission] = pending["submissions"]
    assert submission["source"] == "void f0(void){}" and submission["compiler"]["toolchain"] == "gcc-2.7.2-kmc"
    # Agents can't verify their own work.
    denied = httpx.post(f"{base}/v1/p/game/verdicts", json={"id": submission["id"], "score": 0},
                        headers={"Authorization": f"Bearer {agent}"})
    assert denied.status_code == 403
    verdict = httpx.post(f"{base}/v1/p/game/verdicts", json={"id": submission["id"], "score": 0},
                         headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    assert verdict == {"id": submission["id"], "state": "verified", "matched": True}
    assert "2/30 functions matched" in tool(base, agent, "project_info")[0]
    assert json.loads(tool(base, agent, "get_claims")[0])["claims"] == []
    matches = httpx.get(f"{base}/v1/p/game/matches", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    assert [m["function"] for m in matches["matches"]] == ["f0"] and matches["matches"][0]["by"] == "agent0"

    # The owner exports again while the server runs: the server's match stays.
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    assert "2/30 functions matched" in tool(base, agent, "project_info")[0]


def test_thirty_agents_claiming_at_once_never_share_a_function(hosted):
    _, _, tokens, base = hosted
    claimed = {}

    def claim(i):
        text, error = tool(base, tokens[f"agent{i}"], "claim_function")
        claimed[i] = text.split()[1] if text.startswith("CLAIMED") else text

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = [v for v in claimed.values() if v.startswith("f")]
    assert len(got) == 29 and len(set(got)) == 29  # 29 open functions, each to one agent
    assert sum(v.startswith("Nothing to claim") for v in claimed.values()) == 1


def test_no_token_no_project(hosted):
    _, _, _, base = hosted
    assert httpx.post(f"{base}/p/game/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert httpx.post(f"{base}/p/other/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      headers={"Authorization": "Bearer nope"}).status_code == 401
