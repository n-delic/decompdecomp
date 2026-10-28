"""Reimporting a ROM project replaces its analysis while preserving valid work."""

import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from decompnet.rom import Picked, split


def test_reimport_replaces_analysis_and_keeps_only_unchanged_matches(tmp_path, monkeypatch):
    root = tmp_path / "game"
    root.mkdir()
    (root / "old.z64").write_bytes(b"old")
    (root / "old.yaml").write_text("old split", encoding="utf-8")
    (root / "progress").mkdir()
    (root / "progress" / "save.json").write_text("saved work", encoding="utf-8")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {
        "kind": "rom", "platform": "n64", "big_endian": True, "gp": 0,
        "rom": {"file": "old.z64", "program": "old.z64", "sha1": "old"},
        "segments": [], "compiler": {"toolchain": "chosen-toolchain", "flags": ["-G0"], "timeout": 30},
        "auto_compiler": True, "export": {"path": "old/source"}, "autopilot": {"matched": 100},
        "collab": {"open": True},
    }
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                         ("game", "old game", str(root), "mips", json.dumps(cfg))).lastrowid
        unchanged_id = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, "clamp_s32", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void clamp_s32(void) {}", 0x80000000),  # renamed since import (polishing): paired by its address
        ).lastrowid
        changed_id = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, "func_80000004", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void func_80000004(void) {}", 0x80000004),
        ).lastrowid
        removed_id = db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source) "
                                "VALUES(?,?,?,?,?,?,?)", (pid, "func_80000008", 4, 1, 0x80000008, "matched",
                                                          "void func_80000008(void) { gone(); }")).lastrowid
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "clamp_s32", 0x80000000, 4, "func"))
        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,?,?,?)",
                   (pid, changed_id, "keep this note", 1))
        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,?,?,?)",
                   (pid, removed_id, "note on removed function", 2))
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) "
                   "VALUES(?,?,?,?,?,?)", (pid, 1, "user header", "custom", 1, ""))

    game = tmp_path / "selected.z64"
    game.write_bytes(b"new rom")

    class TestConsole:
        id = "n64"
        name = "Nintendo 64"
        arch = "mips"

        def read_game(self, file, name):
            return Picked("n64", b"new rom", "new.z64", {
                "title": "new game", "size": 7, "game_code": "ABCD", "region": "US",
                "compiler_guess": "KMC", "sha1": "new",
            }, True)

        def debug_info(self, program, known):
            return None

        def default_compiler(self, info, guess):
            return "detected-toolchain"

        def default_flags(self, toolchain, gp):
            return []

        def after_import(self, net, slug, chosen, log):
            assert chosen is True

    console = TestConsole()
    monkeypatch.setattr("decompnet.app.engine.importing.consoles.identify", lambda _: console)
    net.console = lambda _: console
    line_same = split.AsmLine(0, 0x80000000, 0x24020001, "addiu", "v0, zero, 1")
    line_changed = split.AsmLine(4, 0x80000004, 0x24020002, "addiu", "v0, zero, 2")
    line_new = split.AsmLine(8, 0x8000000C, 0x24020003, "addiu", "v0, zero, 3")
    functions = [
        split.RomFunction("func_80000000", 0x80000000, 4, "code", [line_same], root / "same.s"),
        split.RomFunction("func_80000004", 0x80000004, 4, "code", [line_changed], root / "changed.s"),
        split.RomFunction("func_8000000C", 0x8000000C, 8, "code", [line_new], root / "new.s"),
    ]

    def fake_split(workdir, *args, **kwargs):
        (workdir / "new.yaml").write_text("new split", encoding="utf-8")
        return split.SplitResult(functions, [], [split.Segment("code", 0, 16, 0x80000000)], {}, None, "", "")

    monkeypatch.setattr("decompnet.app.engine.importing.split.split_rom", fake_split)

    messages = []
    result = net.reimport_rom("game", game, confirmed=True, log=messages.append)
    assert result == {"slug": "game", "functions": 3, "matched": 1}
    assert messages[0] == "Starting reimport: reading the selected game and checking its format."
    assert any("New split ready: 3 functions" in message for message in messages)
    assert any("Replacing project files" in message for message in messages)
    assert any("reimport kept 1 matched functions" in message for message in messages)
    assert messages[-1] == "Reimport complete: 3 functions; 1 still match."
    assert (root / "progress" / "save.json").read_text(encoding="utf-8") == "saved work"
    assert (root / "new.z64").read_bytes() == b"new rom"
    assert (root / "new.yaml").read_text(encoding="utf-8") == "new split"
    assert not (root / "old.yaml").exists()
    unchanged = net.store.one("SELECT status, matched_source FROM functions WHERE id=?", unchanged_id)
    assert unchanged["status"] == "matched"
    assert unchanged["matched_source"] == "void clamp_s32(void) {}"
    assert net.store.one("SELECT name FROM functions WHERE id=?", unchanged_id)["name"] == "clamp_s32"
    assert net.store.one("SELECT address FROM symbols WHERE project_id=? AND name='clamp_s32'", pid)["address"] == 0x80000000
    assert not net.store.one("SELECT 1 FROM symbols WHERE project_id=? AND name='func_80000000'", pid)
    kept = net.store.one("SELECT text FROM notes WHERE text LIKE '%it matched with this C%'")["text"]
    assert "gone();" in kept and any("their C is kept in the project's notes" in m for m in messages)
    changed = net.store.one("SELECT status, matched_source, target FROM functions WHERE id=?", changed_id)
    assert changed["status"] == "open" and changed["matched_source"] is None
    assert changed["target"] == bytes.fromhex("24020002")
    assert net.store.one("SELECT text FROM notes WHERE function_id=?", changed_id)["text"] == "keep this note"
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?",
                         pid, "func_80000008") is None
    orphaned_note = net.store.one("SELECT function_id, text FROM notes WHERE text LIKE '%removed function%'")
    assert orphaned_note["function_id"] is None
    assert orphaned_note["text"] == "[func_80000008 before reimport] note on removed function"
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, "func_8000000C")
    saved = json.loads(net.project_row("game")["config"])
    assert saved["compiler"] == cfg["compiler"] and saved["auto_compiler"] is True
    assert net.project_row("game")["name"] == "old game"
    assert "export" not in saved and "autopilot" not in saved
    assert net.store.one("SELECT header FROM context_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                         pid)["header"] == "user header"
    assert net.store.one("SELECT kind FROM events WHERE project_id=? ORDER BY id DESC LIMIT 1",
                         pid)["kind"] == "reimport"
    assert not list(tmp_path.glob(".game-reimport-backup-*"))


def test_reimport_still_blocks_projects_with_an_active_room(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    root = tmp_path / "game"
    root.mkdir()
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                   ("game", "Game", str(root), "mips", json.dumps({
                       "kind": "rom", "platform": "n64", "collab": {"open": True},
                   })))

    class TestConsole:
        id = "n64"

    console = TestConsole()
    net.console = lambda _: console
    net.rooms["game"] = object()
    with pytest.raises(NetworkError, match="leave the shared room"):
        net.import_split(console, Picked("n64", b"rom", "game.z64", {}, True), reimport_slug="game")


def test_reimport_pairs_the_two_functions_an_overlay_puts_at_one_address():
    """A game's overlay loads at the same address as main code, so two functions can share one. Paired by address
    alone, the second was left unpaired and its name (held by the old twin) was inserted again."""
    from types import SimpleNamespace

    from decompnet.app.engine.importing import pair_old_functions

    old = [{"id": 1, "name": "func_00100000", "vram": 0x100000},
           {"id": 2, "name": "ovl_ntgui2_func_00100000", "vram": 0x100000},
           {"id": 3, "name": "agents_name", "vram": 0x200000}]
    new = [SimpleNamespace(name="ovl_ntgui2_func_00100000", vram=0x100000),
           SimpleNamespace(name="func_00100000", vram=0x100000),
           SimpleNamespace(name="func_00200000", vram=0x200000),
           SimpleNamespace(name="func_00300000", vram=0x300000)]
    paired = pair_old_functions(old, new)
    assert paired["func_00100000"]["id"] == 1 and paired["ovl_ntgui2_func_00100000"]["id"] == 2
    assert paired["func_00200000"]["id"] == 3  # renamed since: still paired by its address
    assert "func_00300000" not in paired  # new in this split
    assert len({o["id"] for o in paired.values()}) == len(paired)  # each old function pairs once
