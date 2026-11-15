"""Verification packs (hosted_pack.py): judging a hosted project's submissions on a computer without the game."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import textwrap
import threading

import httpx
import pytest

from decompnet import hosted_export, hosted_judge, hosted_pack
from decompnet.app import mcp_gateway
from decompnet.app.engine import RomNetwork
from tests.test_hosted import gateway, project

ROM = bytes(range(256)) * 64  # 16 KiB


def game(tmp_path):
    """The hosted tests' project, with a game file, segments and symbols."""
    net = project(tmp_path)
    (tmp_path / "rom.z64").write_bytes(ROM)
    with net.store.tx() as db:
        config = json.loads(db.execute("SELECT config FROM projects").fetchone()[0])
        config |= {"rom": {"file": "rom.z64", "sha1": hashlib.sha1(ROM).hexdigest(), "size": len(ROM)},
                   "segments": [{"name": "main", "rom_start": 0x1000, "rom_end": 0x3000, "vram": 0x80001000}],
                   "gp": 0, "big_endian": True, "progress_folder": "/Users/owner/progress",
                   "collab": {"room": "secret"}}
        db.execute("UPDATE projects SET config=?", (json.dumps(config),))
        pid = db.execute("SELECT id FROM projects").fetchone()[0]
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "gTable", 0x80002800, 16, "data"))
    return net


def test_a_pack_holds_what_the_judge_reads_and_nothing_of_the_owners(tmp_path):
    net = game(tmp_path)
    path = tmp_path / "packs" / "game.sqlite3"
    counts = hosted_pack.export_pack(net, "game", path)
    assert counts["functions"] == 30 and counts["segments"] == 1
    db = sqlite3.connect(path)
    meta = dict(db.execute("SELECT key,value FROM meta"))
    config = json.loads(meta["config"])
    assert set(config) <= set(hosted_pack.JUDGE_KEYS)
    assert "progress_folder" not in config and "collab" not in config and "instructions" not in config
    assert config["compiler"] == {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2", "-G0"]}  # no owner command
    assert config["rom"]["file"] == hosted_pack.PROGRAM
    assert b"/Users/owner" not in path.read_bytes()
    import zlib
    [(start, data)] = db.execute("SELECT rom_start,data FROM segments").fetchall()
    assert start == 0x1000 and zlib.decompress(data) == ROM[0x1000:0x3000]  # the program, not the whole game
    assert ("gTable", 0x80002800, 16, "data") in db.execute("SELECT name,address,size,kind FROM symbols").fetchall()


def test_the_scratch_project_is_the_project_as_the_judge_sees_it(tmp_path):
    net = game(tmp_path)
    pack = tmp_path / "packs" / "game.sqlite3"
    hosted_pack.export_pack(net, "game", pack)
    built = []

    def build():
        built.append(hosted_pack.scratch(pack, tmp_path / "cache"))
    threads = [threading.Thread(target=build) for _ in range(4)]  # a verifier's judges start together
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 4 and len(list((tmp_path / "cache").glob("game-*/ready"))) == 1
    scratch, slug = built[0]
    proj = scratch._project(slug)
    assert scratch.reader(proj)(0x80002000, 8) == ROM[0x2000:0x2008]  # a data read the linker makes
    assert scratch.symbols(proj["id"]).resolve("gTable") == 0x80002800
    fn = scratch._function("f3", slug)
    assert bytes(fn["target"]) == bytes([3, 3, 0xE0, 8]) and fn["vram"] == 0x80001300
    assert scratch._context(proj["id"], "src/a.c")["header"] == "typedef int s32;"
    assert scratch.cfg(proj)["segments"][0]["vram"] == 0x80001000


def test_judging_from_packs(tmp_path, monkeypatch):
    net = game(tmp_path)
    folder = tmp_path / "packs"
    hosted_pack.export_pack(net, "game", folder / "game.sqlite3")
    monkeypatch.setattr(RomNetwork, "_judge_with", lambda self, proj, fn, tc, flags, source:
                        (True, None, "") if proj["root"].endswith("root") and "MATCH" in source else (False, None, "no"))
    packs = hosted_judge.Packs(folder, tmp_path / "cache")
    assert packs.describe()["projects"] == ["game"]
    request = {"slug": "game", "function": {"name": "f1", "vram": 0x80001100, "target": bytes([1, 3, 0xE0, 8]).hex()},
               "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": []}, "source": "MATCH"}
    assert packs.judge(request) == {"score": 0, "detail": "byte-identical"}
    assert "no verification pack" in packs.judge(request | {"slug": "other"})["error"]
    assert "another build" in packs.judge(request | {"function": request["function"] | {"target": "00"}})["error"]


@pytest.mark.skipif(mcp_gateway.built() is None, reason="the gateway isn't built (cargo build --release in gateway/)")
def test_only_verifiers_get_packs_and_a_verifier_keeps_them_current(tmp_path):
    net = game(tmp_path)
    folder = tmp_path / "hosted"
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    hosted_pack.export_pack(net, "game", folder / "packs" / "game.sqlite3")
    token = {name: subprocess.run(gateway("add-user", "--dir", str(folder), "--name", name, "--role", role),
                                  capture_output=True, text=True, check=True).stdout.strip()
             for name, role in [("alice", "agent"), ("box", "verifier"), ("other-box", "verifier")]}
    server = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                              stdout=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{int(server.stdout.readline().split()[1])}"
    try:
        url = f"{base}/v1/p/game/pack"
        assert httpx.get(url, headers={"Authorization": f"Bearer {token['alice']}"}).status_code == 403
        got = httpx.get(url, headers={"Authorization": f"Bearer {token['box']}"})
        assert got.status_code == 200 and got.content == (folder / "packs" / "game.sqlite3").read_bytes()
        again = httpx.get(url, headers={"Authorization": f"Bearer {token['box']}", "If-None-Match": got.headers["etag"]})
        assert again.status_code == 304
        # The verifier downloads the pack and hands its judge --packs.
        judge = tmp_path / "judge.py"
        judge.write_text(textwrap.dedent("""
            import json, sys
            open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))
            for line in sys.stdin:
                print(json.dumps({"projects": ["game"], "toolchains": []}), flush=True)
        """))
        mine = tmp_path / "mine"
        run = subprocess.run([str(mcp_gateway.built()), "verifier", "--server", base, "--token", token["box"],
                              "--name", "box", "--poll", "0.2", "--idle-exit", "1", "--packs", str(mine),
                              "--judge", sys.executable, "--judge-arg", str(judge), "--judge-arg", str(tmp_path / "argv")],
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        assert (mine / "game.sqlite3").read_bytes() == got.content
        assert json.loads((tmp_path / "argv").read_text()) == ["--packs", str(mine)]
    finally:
        server.terminate()
        server.wait(5)
