"""Reverting a compiler range removed in a project's Settings: the range, its compiler and its flags come back
exactly as they were, through the same /api/compiler add path the Settings use."""

import json
from types import SimpleNamespace

from starlette.testclient import TestClient

from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.consoles.gc_wii import GAMECUBE
from decompnet.forks.project import ForkProject

from .test_app_server import PORT, SECRET, WINDOW
from .test_gc_compilers import _project as gc_project
from .test_loading import tiny_game


def _client(tmp_path, net):
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET)
    return TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                      base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)


def test_a_rom_range_removed_and_reverted_through_the_api_comes_back_identical(tmp_path, monkeypatch):
    """A quirk removed with its matched functions reopened: Revert adds the range again as it was, and the functions
    stay open for the judge and agents (the revert doesn't mark them matched by itself)."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = "revert-rom"
    proj = tiny_game(net, slug, functions=1)
    part = {"start": 0x100000, "end": 0x100010, "toolchain": "mwcps2-3.0.1", "flags": ["-O3", "-sdatathreshold", "8"]}
    with net.store.tx() as db:
        cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
        cfg["compiler_ranges"] = [part]
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        db.execute("UPDATE functions SET status='matched', matched_source='void func_0(void) {}', matched_at=1 "
                   "WHERE project_id=?", (proj["id"],))
    monkeypatch.setattr(net, "regression_test", lambda *a, **k: [("func_0", None)])
    monkeypatch.setattr(net, "_judge_with", lambda *a, **k: (False, SimpleNamespace(score=100), ""))
    monkeypatch.setattr(net.progress, "sync", lambda *a, **k: None)
    client = _client(tmp_path, net)
    headers = {"X-Decomp": "1"}

    removed = client.post("/api/compiler", headers=headers, json={
        "slug": slug, "ranges": "0x100000-0x100010", "remove": True, "reopen": True,
    })
    assert removed.status_code == 200, removed.text
    assert removed.json()["reopened"] == ["func_0"]
    assert removed.json()["compiler_ranges"] == []
    row = net.store.one("SELECT status, best_source FROM functions WHERE project_id=? AND name='func_0'", proj["id"])
    assert row["status"] == "open" and row["best_source"] == "void func_0(void) {}"

    reverted = client.post("/api/compiler", headers=headers, json={
        "slug": slug, "ranges": "0x100000-0x100010", "toolchain": part["toolchain"], "flags": part["flags"],
    })
    assert reverted.status_code == 200, reverted.text
    assert net.cfg(net.project_row(slug))["compiler_ranges"] == [part]
    # the judge decides what matches, not the revert: the reopened function waits for its next attempt
    assert net.store.one("SELECT status FROM functions WHERE project_id=? AND name='func_0'",
                         proj["id"])["status"] == "open"


def test_a_project_files_compiler_removed_and_reverted_through_the_api_comes_back_identical(tmp_path, monkeypatch):
    """In project mode the part is a source file: Revert puts that same file back on the same compiler, and no other
    file changes. (How the switch builds and judges the file is the console's, tested elsewhere.)"""
    root = tmp_path / "game"
    root.mkdir()
    fp = gc_project(root)
    source = "src/auto/text_80005000.c"
    GAMECUBE.set_file_compiler(fp, source, "GC/1.2.5")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug, name, root, arch, config) VALUES(?,?,?,?,?)",
                   ("revert-gc", "Revert GC", str(root), "ppc",
                    json.dumps({"kind": "fork", "platform": "gc", "version": "GZ2E01", "compiler": "GC/1.3.2"})))

    def switch(self, slug, source, compiler):
        _, project, cfg = self.get(slug)
        GAMECUBE.set_file_compiler(project, source, compiler)
    monkeypatch.setattr(type(net.forks), "set_file_compiler_by_hand", switch)
    monkeypatch.setattr(net.progress, "sync", lambda *a, **k: None)
    client = _client(tmp_path, net)
    headers = {"X-Decomp": "1"}

    removed = client.post("/api/compiler", headers=headers, json={
        "slug": "revert-gc", "ranges": "0x80005000-0x80005100", "remove": True,
    })
    assert removed.status_code == 200, removed.text
    assert removed.json()["reopened"] == []
    assert GAMECUBE.file_compiler(ForkProject(root), source) == "GC/1.3.2"

    reverted = client.post("/api/compiler", headers=headers, json={
        "slug": "revert-gc", "ranges": "0x80005000-0x80005100", "toolchain": "GC/1.2.5",
    })
    assert reverted.status_code == 200, reverted.text
    assert GAMECUBE.file_compiler(ForkProject(root), source) == "GC/1.2.5"
    assert GAMECUBE.file_compiler(ForkProject(root), "src/auto/text_80004000.c") == "GC/1.3.2"
