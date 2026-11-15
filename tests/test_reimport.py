"""Reimporting a ROM project replaces its analysis while preserving valid work."""

import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.consoles import Console
from decompnet.network import NetworkError
from decompnet.rom import Picked, split


@pytest.mark.parametrize("legacy_reset",[False,True])
def test_reimport_replaces_analysis_and_keeps_only_unchanged_matches(tmp_path, monkeypatch, legacy_reset):
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
        db.execute("UPDATE functions SET last_checked=123,last_improved=120 WHERE id=?", (changed_id,))
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

    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) VALUES(?,2,'private types','source',2,'local_source_test')",(pid,))
        db.execute("UPDATE functions SET unit='local_source_test' WHERE id=?",(unchanged_id,))
        if legacy_reset:
            db.execute("UPDATE functions SET unit='original_segment' WHERE id=?",(unchanged_id,))
            db.execute("INSERT INTO attempts(function_id,score,compiled,context_version,source,created_at) VALUES(?,0,1,2,?,2)",
                       (unchanged_id,"void clamp_s32(void) {}"))
        db.execute('UPDATE functions SET meta=? WHERE id=?',
                   (json.dumps({'naming':{'method':'operator'},'reopened':True}),unchanged_id))

    game = tmp_path / "selected.z64"
    game.write_bytes(b"new rom")

    class TestConsole(Console):  # the connector's own split/judge/asm parts, with only the facts this test needs
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
    assert net.store.one('SELECT unit FROM functions WHERE id=?',unchanged_id)['unit']=='local_source_test'
    unchanged = net.store.one("SELECT status, matched_source FROM functions WHERE id=?", unchanged_id)
    assert json.loads(net.store.one("SELECT meta FROM functions WHERE id=?",unchanged_id)["meta"])["naming"]=={"method":"operator"}
    assert unchanged["status"] == "matched"
    assert unchanged["matched_source"] == "void clamp_s32(void) {}"
    assert net.store.one("SELECT name FROM functions WHERE id=?", unchanged_id)["name"] == "clamp_s32"
    assert net.store.one("SELECT address FROM symbols WHERE project_id=? AND name='clamp_s32'", pid)["address"] == 0x80000000
    assert not net.store.one("SELECT 1 FROM symbols WHERE project_id=? AND name='func_80000000'", pid)
    kept = net.store.one("SELECT text FROM notes WHERE text LIKE '%it matched with this C%'")["text"]
    assert "gone();" in kept and any("their C is kept in the project's notes" in m for m in messages)
    changed = net.store.one("SELECT status, matched_source, target, last_checked, last_improved "
                            "FROM functions WHERE id=?", changed_id)
    assert changed["status"] == "open" and changed["matched_source"] is None
    assert changed["target"] == bytes.fromhex("24020002")
    assert changed["last_checked"] is None and changed["last_improved"] is None
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
    assert net.store.one("SELECT header FROM context_versions WHERE project_id=? AND unit='' ORDER BY version DESC LIMIT 1",
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


@pytest.mark.parametrize('derived', [False, True])
def test_reimport_pairs_saved_matches_by_overlay_and_address(tmp_path, monkeypatch, derived):
    from decompnet import consoles

    root = tmp_path / 'banks'
    root.mkdir()
    net = RomNetwork(tmp_path / 'banks.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'rom': {'file': 'rom.z64', 'sha1': 'same', 'analysis_sha1': 'stale'}, 'segments': []}
    originals = {}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('banks', 'Banks', str(root), 'mips', json.dumps(cfg))).lastrowid
        for index in [1, 2]:
            name = f'renamed_bank_{index}'
            body = bytes.fromhex(f'2402000{index}')
            fid = db.execute('INSERT INTO functions(project_id,name,unit,vram,size,n_insns,target,relocs,status,matched_source) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?)', (pid, name, f'overlay_{index:03d}', 0x80300000,
                              4, 1, body, '[]', 'matched', f'int {name}(void) {{ return {index}; }}')).lastrowid
            originals[fid] = (name, body)

    def replacement(work, *a, **kw):
        functions = []
        # Reverse order to catch accidental address-only pairing and stolen match identities.
        for index in [2, 1]:
            unit = f'overlay_{index:03d}'
            line = split.AsmLine(index * 4, 0x80300000, 0x24020000 + index, 'addiu', f'v0, zero, {index}')
            functions.append(split.RomFunction(f'func_80300000_{unit}', 0x80300000, index * 4, unit,
                                                [line], work / (unit + '.s')))
        if derived:(work/'analysis.bin').write_bytes(b'derived image')
        return split.SplitResult(functions, [], [], {}, work / 'splat.yaml', 'KMC', '', image='analysis.bin' if derived else '')
    monkeypatch.setattr(split, 'split_rom', replacement)
    picked = Picked('n64', bytes(16), 'rom.z64', {'title': 'Banks', 'size': 16, 'sha1': 'same',
                    'game_code': 'DEMO', 'region': 'Test', 'compiler_guess': 'KMC'}, True)
    net.import_split(consoles.get('n64'), picked, reimport_slug='banks', log=lambda *_: None)
    import hashlib
    recorded = net.cfg(net.project_row('banks'))['rom']
    assert recorded['sha1'] == 'same'
    assert recorded['analysis_sha1'] == hashlib.sha1(b'derived image' if derived else bytes(16)).hexdigest()
    for fid, (name, body) in originals.items():
        row = net.store.one('SELECT * FROM functions WHERE id=?', fid)
        assert row['name'] == name and row['target'] == body and row['status'] == 'matched'
    assert len(net.store.all('SELECT * FROM functions WHERE project_id=?', pid)) == 2


def test_reimport_keeps_agents_names_and_judges_c_whose_references_were_read_anew(tmp_path, monkeypatch):
    """A racing game lost 234 matches at once: its new split sized bss blocks over 177 addresses helpers had named
    (the names went, so matched C stopped linking), and a newer reader annotated references the old targets left as
    numbers (same bytes, other relocations), so the C was dropped unjudged."""
    root = tmp_path / "game"
    root.mkdir()
    (root / "old.z64").write_bytes(b"old")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "n64", "big_endian": True, "gp": 0,
           "rom": {"file": "old.z64", "program": "old.z64", "sha1": "old"}, "segments": [],
           "compiler": {"toolchain": "chosen-toolchain", "flags": ["-G0"], "timeout": 30}}
    helper = net.attribution_agent("claude-helper")
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                         ("game", "old game", str(root), "mips", json.dumps(cfg))).lastrowid
        fid = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,matched_agent_id,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, "race_update", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void race_update(void) { gRecords = 0; }", helper.id, 0x80000000)).lastrowid
        for name, address, size, kind in (("gRecords", 0x800C35A0, 0, "data"), ("gSlotState", 0x800C35A0, 0, "data"),
                                          ("gLapCount", 0x800D0000, 0, "bss"), ("gStreamState", 0x800C02AE, 0, "bss"),
                                          ("stream_state", 0x800C02AE, 0, "bss")):
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, name, address, size, kind))
    game = tmp_path / "selected.z64"
    game.write_bytes(b"new rom")

    class TestConsole(Console):
        id = "n64"
        name = "Nintendo 64"
        arch = "mips"

        def read_game(self, file, name):
            return Picked("n64", b"new rom", "new.z64", {"title": "new game", "size": 7, "game_code": "ABCD",
                                                         "region": "US", "compiler_guess": "KMC", "sha1": "new"}, True)

        def debug_info(self, program, known):
            return None

        def default_compiler(self, info, guess):
            return "detected-toolchain"

        def default_flags(self, toolchain, gp):
            return []

        def after_import(self, net, slug, chosen, log):
            pass

        def target_from_split(self, fn, resolve, big_endian, gp):
            from decompnet.rom.match import RomTarget
            return RomTarget(fn.name, fn.vram, bytes.fromhex("24020001"), [(0, 5, 0x800C35A0)], True, 0)

    console = TestConsole()
    monkeypatch.setattr("decompnet.app.engine.importing.consoles.identify", lambda _: console)
    net.console = lambda _: console
    line = split.AsmLine(0, 0x80000000, 0x24020001, "addiu", "v0, zero, 1")
    functions = [split.RomFunction("func_80000000", 0x80000000, 4, "code", [line], root / "f.s")]
    data = [split.DataSymbol("D_800C02AE", 0x800C02AE, None, 66514, "bss")]  # sized over gRecords
    data.append(split.DataSymbol("D_800CFFF0", 0x800CFFF0, None, 0x100, "bss"))  # gLapCount lies inside this one

    def fake_split(workdir, *args, **kwargs):
        return split.SplitResult(functions, data, [split.Segment("code", 0, 16, 0x80000000)], {}, None, "", "")

    monkeypatch.setattr("decompnet.app.engine.importing.split.split_rom", fake_split)
    judged = []

    def attempt(agent, name, source, project=None):
        judged.append((agent.name, name, source))
        return {"matched": False}

    net.attempt = attempt
    messages = []
    net.reimport_rom("game", game, confirmed=True, log=messages.append)
    names = {r["name"]: r["address"] for r in net.store.all("SELECT name, address FROM symbols WHERE project_id=?", pid)}
    assert names["gRecords"] == names["gSlotState"] == 0x800C35A0  # both names of one address
    sizes = {r["name"]: r["size"] for r in net.store.all("SELECT name, size FROM symbols WHERE project_id=?", pid)}
    assert sizes["gStreamState"] == sizes["stream_state"] == 66514  # both names of the block keep its size
    assert "D_800C02AE" not in sizes
    assert names["gLapCount"] == 0x800D0000 and names["D_800CFFF0"] == 0x800CFFF0  # inside a sized block
    assert judged == [("claude-helper", "race_update", "void race_update(void) { gRecords = 0; }")]
    row = net.store.one("SELECT status, best_source FROM functions WHERE id=?", fid)
    assert row["status"] == "open" and row["best_source"] == "void race_update(void) { gRecords = 0; }"
    assert any("0 of 1 still match" in m for m in messages)
