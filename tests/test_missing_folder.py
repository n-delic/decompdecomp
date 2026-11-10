"""A project whose folder was deleted from the computer: its records are still listed, its Remove works without the
folder, and the same game can be imported again afterwards."""

import json
import shutil

from starlette.testclient import TestClient

from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.consoles import ROM, Console
from decompnet.rom import Picked, split

from .test_app_server import PORT, SECRET, WINDOW


def _client(tmp_path, net):
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET)
    return TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                      base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)


def _rom_project(net, root):
    root.mkdir()
    (root / "game.rom").write_bytes(b"game")
    config = {"kind": "rom", "platform": "n64", "big_endian": True, "gp": 0,
              "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": [], "timeout": 20},
              "rom": {"file": "game.rom", "sha1": "same", "title": "Game"}, "segments": []}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                         ("gone", "Gone Game", str(root), "mips", json.dumps(config))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(?,1,'',0)", (pid,))
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,target,relocs) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "func_80000000", 4, 1, 0x80000000, bytes(4), "[]"))


class _Console(Console):
    """The console's import, with a split made here: no compiler or game file is needed to test removing and adding."""

    id = "n64"
    name = "Nintendo 64"
    arch = "mips"
    mode = ROM

    def read_game(self, file, name=""):
        return Picked("n64", b"game", "game.z64", {
            "title": "Gone", "size": 4, "game_code": "GAME", "region": "US", "compiler_guess": "KMC",
            "sha1": "same",
        }, True)

    def split_game(self, workdir, picked, *, symbols=None, log=print):
        line = split.AsmLine(0, 0x80000000, 0x24020001, "addiu", "v0, zero, 1")
        function = split.RomFunction("func_80000000", 0x80000000, 4, "code", [line], workdir / "same.s")
        return split.SplitResult([function], [], [split.Segment("code", 0, 4, 0x80000000)], {}, None, "", "")

    def default_compiler(self, info, splat_guess):
        return "gcc-2.7.2-kmc"

    def default_flags(self, toolchain, gp):
        return []


def test_a_missing_folder_is_listed_and_removed_without_it(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    root = tmp_path / "gone"
    _rom_project(net, root)
    shutil.rmtree(root)

    summary = net.summary("gone")
    assert "folder is missing" in summary["missing"] and str(root) in summary["missing"]
    overview = net.overview("gone")
    assert overview["missing"] == summary["missing"]
    assert not root.exists()  # listing or opening it must not write its folder back into being

    client = _client(tmp_path, net)
    listed = client.get("/api/projects").json()
    assert [p["slug"] for p in listed] == ["gone"] and "folder is missing" in listed[0]["missing"]
    opened = client.get("/api/project", params={"slug": "gone"})
    assert opened.status_code == 200 and opened.json()["missing"] == summary["missing"]

    removed = client.post("/api/delete", headers={"X-Decomp": "1"}, json={"slug": "gone"})
    assert removed.status_code == 200 and removed.json() == {"ok": True}
    assert net.store.one("SELECT 1 FROM projects WHERE slug='gone'") is None
    assert not root.exists()  # removing a missing project removes its records, never a folder it doesn't have


def test_a_folder_moved_into_the_workspace_is_relinked_not_missing(tmp_path):
    """A whole Decomp folder copied to another computer keeps its projects: a project found again in the workspace by
    its name is put back before it's called missing."""
    workspace = tmp_path / "work"
    net = RomNetwork(tmp_path / "app.db", workspace)
    root = tmp_path / "gone"
    _rom_project(net, root)
    workspace.mkdir()
    root.rename(workspace / "gone")

    summary = net.summary("gone")
    assert summary["missing"] is None
    assert net.project_row("gone")["root"] == str(workspace / "gone")


def test_the_same_game_can_be_imported_after_the_missing_project_is_removed(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    root = tmp_path / "gone"
    _rom_project(net, root)
    shutil.rmtree(root)
    assert "folder is missing" in net.summary("gone")["missing"]

    client = _client(tmp_path, net)
    assert client.post("/api/delete", headers={"X-Decomp": "1"}, json={"slug": "gone"}).status_code == 200
    monkeypatch.setattr("decompnet.app.engine.importing.consoles.identify", lambda _: _Console())
    game = tmp_path / "game.z64"
    game.write_bytes(b"game")
    slug = net.import_rom(game, confirmed=True, name="game.z64")

    assert slug == "gone" and root.is_dir()
    assert net.summary(slug)["missing"] is None
    assert net.store.one("SELECT COUNT(*) AS n FROM functions WHERE project_id=(SELECT id FROM projects WHERE slug=?)",
                         slug)["n"] == 1
