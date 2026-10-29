"""A collaborator's files take precedence over SQLite, without an import button or a second on-disk copy."""

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import saves
from decompnet.app.engine import RomNetwork
from decompnet.app.progress import Progress, _read
from decompnet.network import NetworkError
from decompnet.rom import split


@pytest.fixture
def project(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}, "instructions": "old"}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                         (str(tmp_path / "game"), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')", (pid,))
        for name, address in (("f", 4096), ("g", 4100)):
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,?,?,4,1,'open','')", (pid, name, address))
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    judged = []

    def attempt(agent, name, source, slug):
        judged.append((name, source))
        if source == "compile error":
            return {"matched": False}
        matched = "good" in source and "broken" not in net._context(pid)["header"]
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status=?,matched_source=?,best_source=?,best_score=? "
                       "WHERE project_id=? AND name=?",
                       ("matched" if matched else "open", source if matched else None, source,
                        0 if matched else 10, pid, name))
        return {"matched": matched}

    monkeypatch.setattr(net, "attempt", attempt)
    net.progress.sync("game", force=True)
    return net, saves.default_folder(net, "game"), judged


def manifest(folder, **changes):
    path = folder / saves.MANIFEST
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_new_source_is_discovered_without_editing_the_function_index(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good C", encoding="utf-8")
    assert net.progress.sync("game", force=True)
    assert judged == [("f", "good C")]
    assert net._function("f", "game")["status"] == "matched"
    assert not net.progress.sync("game", force=True)


def test_editing_a_match_rejudges_it_and_preserves_a_failed_contribution(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    path = folder / "src/f.c"
    path.write_text("good original", encoding="utf-8")
    net.progress.sync("game", force=True)
    path.write_text("different C", encoding="utf-8")
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["status"] == "open"
    saves.save(net, "game")
    assert path.read_text() == "different C"
    assert judged == [("f", "good original"), ("f", "different C")]


def test_deleted_sources_reopen_functions_instead_of_being_resurrected(project):
    net, folder, _ = project
    (folder / "src").mkdir()
    path = folder / "src/f.c"
    path.write_text("good", encoding="utf-8")
    net.progress.sync("game", force=True)
    path.unlink()
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["matched_source"] is None
    saves.save(net, "game")
    assert not path.exists()


def test_headers_rejudge_existing_matches(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good", encoding="utf-8")
    net.progress.sync("game", force=True)
    (folder / "include/common.h").write_text("broken header", encoding="utf-8")
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["status"] == "open"
    assert len(judged) == 2


def test_a_header_change_keeps_the_last_match_until_rejudging_can_run(project, monkeypatch):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='known match',best_source='known match',"
                   "best_score=0,matched_at=1 WHERE name='f'")
    net.progress.sync("game", force=True)
    (folder / "include/common.h").write_text("broken header", encoding="utf-8")

    def unavailable(*_args, **_kwargs):
        raise NetworkError("the compiler was cancelled")

    monkeypatch.setattr(net, "attempt", unavailable)
    net.progress.sync("game", force=True)
    fn = net._function("f", "game")
    assert fn["status"] == "open" and fn["best_source"] == "known match"


def test_a_restart_reads_progress_before_writing_stale_database_sources(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good contribution", encoding="utf-8")
    net.progress = Progress(net)
    net.project_row("game")
    assert judged == [("f", "good contribution")]
    assert net._function("f", "game")["matched_source"] == "good contribution"


def test_a_restart_publishes_a_newer_database_match_over_its_old_saved_snapshot(project):
    net, folder, _ = project
    agent, _ = net.local_agent("helper")
    net._record(net._function("f", "game"), agent, 0, "new database match", 1, matched=True)
    assert not (folder / "src/f.c").exists()
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert (folder / "src/f.c").read_text() == "new database match\n"
    assert net._function("f", "game")["matched_source"] == "new database match"


def test_cleared_settings_and_deleted_notes_are_authoritative(project):
    net, folder, _ = project
    agent, _ = net.local_agent("helper")
    net.add_note(agent, "game", "old project note")
    net.progress.sync("game", force=True)
    assert len(json.loads((folder / saves.MANIFEST).read_text())["notes"]) == 1
    manifest(folder, instructions="", notes=[], compiler_ranges=[])
    net.progress.sync("game", force=True)
    assert net.instructions("game") == ""
    assert not net.store.all("SELECT * FROM notes")


def test_external_and_local_manifest_fields_merge_without_losing_global_notes(project):
    net, folder, _ = project
    agent, _ = net.local_agent("helper")
    net.add_note(agent, "game", "local note")
    data = json.loads((folder / saves.MANIFEST).read_text())
    data["instructions"] = "external instructions"
    data["notes"] = [{"function": None, "text": "external note", "by": "friend"}]
    (folder / saves.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    net.progress.sync("game", force=True)
    assert net.instructions("game") == "external instructions"
    notes = {row["text"] for row in net.store.all("SELECT text FROM notes")}
    assert notes == {"local note", "external note"}


def test_legacy_layout_without_status_does_not_rejudge_unchanged_matches(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good source\n", encoding="utf-8")
    net.progress.sync("game", force=True)
    count = len(judged)
    data = json.loads((folder / saves.MANIFEST).read_text())
    data["saved_at"] = time.time() - 10
    for entry in data["layout"]:
        entry.pop("status", None)
    (folder / saves.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    with net.store.tx() as db:
        db.execute("DELETE FROM progress_state WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert len(judged) == count
    assert net._function("f", "game")["status"] == "matched"


def test_first_legacy_save_keeps_a_database_match_newer_than_saved_at(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/g.c").write_text("good external contribution\n", encoding="utf-8")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='newer match',matched_at=? WHERE name='f'",
                   (time.time(),))
        db.execute("DELETE FROM progress_state WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["matched_source"] == "newer match"
    assert (folder / "src/f.c").read_text() == "newer match\n"
    assert net._function("g", "game")["matched_source"] == "good external contribution\n"
    assert judged == [("g", "good external contribution\n")]


def test_a_first_sync_keeps_a_newer_database_match_over_a_same_name_stale_file(project):
    """#101 review: the folder had an older src/f.c, so the newer database match was rejudged away."""
    net, folder, judged = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='good newer C',best_source='good newer C',"
                   "best_score=0,matched_at=200 WHERE name='f'")
        db.execute("UPDATE projects SET config=json_remove(config,'$.progress_revision') WHERE slug='game'")
        db.execute("DELETE FROM progress_state WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good stale C\n", encoding="utf-8")
    manifest(folder, saved_at=100, revision=0)
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    fn = net._function("f", "game")
    assert fn["matched_source"] == "good newer C"
    assert fn["best_source"] == "good newer C"
    assert (folder / "src/f.c").read_text() == "good newer C\n"
    assert judged == []  # the folder's older file was not applied or judged


def test_a_manifest_records_when_it_was_saved_and_a_raising_revision(project):
    net, folder, _ = project
    first = json.loads((folder / saves.MANIFEST).read_text())
    assert isinstance(first.get("saved_at"), (int, float))
    assert first.get("revision", 0) >= 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='good C',best_source='good C',"
                   "best_score=0,matched_at=? WHERE name='f'", (time.time(),))
    net.progress.sync("game", force=True)
    second = json.loads((folder / saves.MANIFEST).read_text())
    assert second["revision"] > first["revision"]
    assert second["saved_at"] >= first["saved_at"]
    saves.save(net, "game")
    third = json.loads((folder / saves.MANIFEST).read_text())
    assert third["revision"] > second["revision"] and third["saved_at"] >= second["saved_at"]


def test_a_first_sync_uses_the_revision_to_keep_a_match_over_an_older_folder(project):
    net, folder, judged = project
    net._update_cfg("game", lambda cfg: cfg.__setitem__("progress_revision", 5))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='good db match',best_source='good db match',"
                   "best_score=0,matched_at=1 WHERE name='f'")
        db.execute("DELETE FROM progress_state WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good old file\n", encoding="utf-8")
    manifest(folder, saved_at=time.time(), revision=3)
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["matched_source"] == "good db match"
    assert (folder / "src/f.c").read_text() == "good db match\n"
    assert judged == []


def test_an_old_manifest_without_a_revision_still_syncs(project):
    net, folder, _ = project
    data = json.loads((folder / saves.MANIFEST).read_text())
    data.pop("revision", None)
    (folder / saves.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    manifest(folder, instructions="from an older save")
    net.progress.sync("game", force=True)
    assert net.instructions("game") == "from an older save"


def test_a_first_sync_keeps_database_settings_and_notes(project):
    """The first sync upgrades the manifest in one write without dropping work only the database has."""
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.instructions','local instructions') WHERE slug='game'")
        db.execute("INSERT INTO notes(project_id,text,created_at) VALUES((SELECT id FROM projects WHERE slug='game'),"
                   "'local note',0)")
        db.execute("DELETE FROM progress_state WHERE project_id=(SELECT id FROM projects WHERE slug='game')")
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert net.instructions("game") == "local instructions"
    assert [row["text"] for row in net.store.all("SELECT text FROM notes")] == ["local note"]
    assert json.loads((folder / saves.MANIFEST).read_text())["revision"] >= 1


def test_an_invalid_merged_rename_does_not_write_the_manifest(project):
    net, folder, _ = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good code", encoding="utf-8")
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.instructions','local instructions') WHERE slug='game'")
    data = json.loads((folder / saves.MANIFEST).read_text())
    data["layout"][0]["name"] = "renamed"
    path = folder / saves.MANIFEST
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(NetworkError):
        net.progress.sync("game", force=True)
    assert path.read_bytes() == before


def test_a_failed_edited_match_keeps_the_last_match_as_a_best_backup(project):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='known match',matched_at=1 WHERE name='f'")
    net.progress.sync("game", force=True)
    path = folder / "src/f.c"
    path.write_text("compile error", encoding="utf-8")
    net.progress.sync("game", force=True)
    fn = net._function("f", "game")
    assert fn["status"] == "open" and fn["best_source"] == "known match"
    assert path.read_text() == "compile error"


def test_local_work_without_events_is_written_and_unrelated_files_survive(project):
    net, folder, _ = project
    (folder / "src").mkdir()
    extra = folder / "src/contributor.txt"
    extra.write_text("keep")
    (folder / ".git").mkdir()
    (folder / ".git/config").write_text("git metadata")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE name='f'")
    assert saves.Autosave(net).run_once() == ["game"]
    assert (folder / "best/f.c").read_text() == "new attempt\n"
    assert extra.read_text() == "keep"
    assert (folder / ".git/config").read_text() == "git metadata"
    before = (folder / saves.MANIFEST).stat().st_mtime_ns
    assert saves.Autosave(net).run_once() == []
    assert (folder / saves.MANIFEST).stat().st_mtime_ns == before


@pytest.mark.parametrize("bad", ["conflict", "json", "deleted"])
def test_unresolved_git_operations_do_not_overwrite_progress(project, bad):
    net, folder, _ = project
    path = folder / saves.MANIFEST
    if bad == "deleted":
        path.unlink()
    else:
        path.write_text("<<<<<<< HEAD\n=======\n>>>>>>> branch" if bad == "conflict" else "{bad")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(NetworkError):
        net.progress.sync("game", force=True)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("field,value", [
    ("symbols", [{"name": "bad-name", "address": 4, "size": 1, "kind": "func"}]),
    ("notes", [{"function": None, "text": 1, "by": "friend"}]),
    ("knowledge", [False]),
])
def test_malformed_manifest_metadata_is_rejected_before_applying(project, field, value):
    net, folder, _ = project
    manifest(folder, **{field: value})
    path = folder / saves.MANIFEST
    before = path.read_bytes()
    with pytest.raises(NetworkError, match="Fix progress/decompdecomp.json"):
        net.progress.sync("game", force=True)
    assert path.read_bytes() == before


def test_unchanged_progress_sync_skips_rendering(project, monkeypatch):
    net, _, _ = project
    monkeypatch.setattr(saves, "_render", lambda *args, **kwargs: pytest.fail("rendered an unchanged project"))
    assert not net.progress.sync("game", force=True)


def test_removing_and_reusing_a_project_id_drops_its_saved_sync_baseline(project, tmp_path):
    net, _, _ = project
    old_id = net._project("game")["id"]
    assert net.store.one("SELECT 1 FROM progress_state WHERE project_id=?", old_id)
    assert net.remove_project("game") == 2
    root = tmp_path / "replacement"
    root.mkdir()
    with net.store.tx() as db:
        project_id = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Replacement',?,?)",
                                (str(root), json.dumps({"kind": "rom", "platform": "n64",
                                                        "rom": {"sha1": "demo"}}))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                   (project_id,))
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                   "VALUES(?,'new_function',4096,4,1,'open','')", (project_id,))
    assert project_id == old_id
    net.progress.sync("game", force=True)
    assert (root / "progress" / saves.MANIFEST).exists()


def test_restoring_the_same_linked_save_returns_fresh_counts_when_sync_is_a_noop(project, monkeypatch):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='known match',best_source='known match',"
                   "best_score=0 WHERE name='f'")
        db.execute("UPDATE functions SET best_source='best attempt',best_score=10 WHERE name='g'")
    net.progress.sync("game", force=True)
    console = SimpleNamespace(read_game=lambda *args: SimpleNamespace(info={"sha1": "demo"}))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: console)
    monkeypatch.setattr(net, "import_rom", lambda *args, **kwargs: "game")
    result = saves.restore(net, str(folder), Path("unused-own-game.bin"), log=lambda *_: None)
    assert result == {"slug": "game", "matched": 1, "best": 1, "lost": []}
    assert net.progress.results.get("game") is None


def test_a_set_aside_function_comes_back_through_save_and_restore(project, tmp_path, monkeypatch):
    """The saved layout marks it asm before _apply runs; its reason must still be restored with it."""
    net, folder, _ = project
    net.set_aside_by_you("g", "game", "hand-written")
    saves.save(net, "game")
    saved = json.loads((folder / saves.MANIFEST).read_text())
    assert saved["set_aside"] and saved["set_aside"][0]["asm_code"].endswith("hand-written")

    work = tmp_path / "fresh"
    work.mkdir()
    fresh = RomNetwork(work / "app.db", work)
    console = SimpleNamespace(read_game=lambda *args: SimpleNamespace(info={"sha1": "demo"}))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: console)

    def import_game(*_args, **_kwargs):
        config = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}}
        with fresh.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                             (str(work / "game"), json.dumps(config))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) "
                       "VALUES(?,1,'',0,'')", (pid,))
            for name, address in (("f", 4096), ("g", 4100)):
                db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                           "VALUES(?,?,?,4,1,'open','')", (pid, name, address))
        return "game"

    monkeypatch.setattr(fresh, "import_rom", import_game)
    monkeypatch.setattr(fresh, "apply_remote_layout", lambda *args: None)
    out = saves.restore(fresh, str(folder), Path("unused-own-game.bin"), log=lambda *_: None)
    row = fresh._function("g", out["slug"])
    assert row["status"] == "asm"
    assert "hand-written" in json.loads(row["meta"])["asm_code"]


def test_restore_waiting_for_project_lock_does_not_hold_the_registry_lock(project, monkeypatch):
    net, folder, _ = project
    console = SimpleNamespace(read_game=lambda *args: SimpleNamespace(info={"sha1": "demo"}))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: console)
    def import_with_rebuild(*_args, **_kwargs):
        with net.progress.rebuilding("game"):
            pass
        return "game"

    monkeypatch.setattr(net, "import_rom", import_with_rebuild)
    original = net.progress._project_lock
    requested = threading.Event()
    acquired_registry = threading.Event()
    errors = []

    def observed_lock(slug):
        if threading.current_thread().name == "restore-probe":
            requested.set()
        return original(slug)

    monkeypatch.setattr(net.progress, "_project_lock", observed_lock)
    project_lock = original("game")

    def restore():
        try:
            saves.restore(net, str(folder), Path("unused-own-game.bin"), log=lambda *_: None)
        except Exception as error:  # returned to the main test thread for assertion
            errors.append(error)

    def probe_registry_lock():
        with net.progress.lock:
            acquired_registry.set()

    project_lock.acquire()
    worker = threading.Thread(target=restore, name="restore-probe")
    probe = threading.Thread(target=probe_registry_lock, name="registry-lock-probe")
    worker.start()
    try:
        assert requested.wait(5)
        probe.start()
        registry_was_available = acquired_registry.wait(1)
    finally:
        project_lock.release()
    worker.join(timeout=5)
    if probe.ident is not None:
        probe.join(timeout=5)
    assert registry_was_available
    assert not worker.is_alive() and (probe.ident is None or not probe.is_alive())
    assert not errors


def test_restore_rejects_instructions_only_existing_project_work(project, tmp_path, monkeypatch):
    net, folder, _ = project
    stale = tmp_path / "stale-save"
    shutil.copytree(folder, stale)
    net.set_instructions("game", "new local instructions")
    console = SimpleNamespace(read_game=lambda *args: SimpleNamespace(info={"sha1": "demo"}))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: console)
    monkeypatch.setattr(net, "import_rom", lambda *args, **kwargs: pytest.fail("imported over existing work"))
    with pytest.raises(NetworkError, match="already open"):
        saves.restore(net, str(stale), Path("unused-own-game.bin"), log=lambda *_: None)
    assert net.instructions("game") == "new local instructions"


def test_an_opened_checkout_remains_the_live_folder(project, tmp_path):
    net, folder, _ = project
    checkout = tmp_path / "checkout"
    shutil.copytree(folder, checkout)
    net._update_cfg("game", lambda cfg: cfg.__setitem__("progress_folder", str(checkout)))
    net.progress.disk.pop("game", None)
    net.progress.sync("game", force=True)
    manifest(checkout, instructions="from PR")
    net.progress.sync("game", force=True)
    assert saves.default_folder(net, "game") == Path(checkout)
    assert net.instructions("game") == "from PR"


def test_a_pull_keeps_an_independent_agents_unsaved_work(project):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='local attempt',best_score=10 WHERE name='g'")
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good PR", encoding="utf-8")
    net.progress.sync("game", force=True)
    assert (folder / "best/g.c").read_text() == "local attempt\n"
    assert net._function("g", "game")["best_source"] == "local attempt\n"


def test_a_compile_started_before_a_pull_cannot_overwrite_it(project):
    net, folder, _ = project
    with pytest.raises(NetworkError, match="changed while this attempt compiled"):
        with net.progress.judging("game"):
            (folder / "src").mkdir()
            (folder / "src/f.c").write_text("good PR", encoding="utf-8")
            worker = threading.Thread(target=lambda: net.progress.sync("game", force=True))
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            agent, _ = net.local_agent("helper")
            net._record(net._function("f", "game"), agent, 0, "stale source", 1, matched=True)
    assert net._function("f", "game")["matched_source"] == "good PR"


@pytest.mark.parametrize("worker", ["autopilot", "permuter"])
def test_background_compile_discards_results_if_progress_changes(worker, project, monkeypatch):
    net, folder, _ = project
    from decompnet.scoring import DiffResult

    proj = net.project_row("game")
    fn = net._function("f", "game")
    agent, _ = net.local_agent(worker)

    def compile_with_pull(*_args):
        manifest(folder, instructions="changed during compile")
        net.progress.sync("game", force=True)
        return SimpleNamespace(ok=True, obj=object(), log="")

    monkeypatch.setattr(net, "_compile", compile_with_pull)
    monkeypatch.setattr(net, "_evaluate", lambda *_: (object(), None, []))
    monkeypatch.setattr(net, "_target_obj", lambda *_: object())
    monkeypatch.setattr("decompnet.scoring.diff", lambda *_: DiffResult(1, 1, [], False))
    if worker == "autopilot":
        from decompnet.app.engine import autopilot

        monkeypatch.setattr(net, "_seed_for", lambda *_: None)
        monkeypatch.setattr(net, "_draft_plan", lambda *_args, **_kwargs: (None, "gcc", None))
        monkeypatch.setattr(net, "_asm_text", lambda *_: "asm")
        monkeypatch.setattr(autopilot.seeds, "generate_from_asm", lambda *_args, **_kwargs: "draft")
        monkeypatch.setattr(autopilot, "compiler_for", lambda *_: {"flags": []})
        with pytest.raises(NetworkError, match="changed while this attempt compiled"):
            net._autopilot_one(agent, proj, fn["id"])
    else:
        with pytest.raises(NetworkError, match="changed while this attempt compiled"):
            net.permuter._score(agent, proj, fn, "draft", 10, None)


def test_a_best_attempt_can_be_edited_and_deleted(project):
    net, folder, _ = project
    (folder / "best").mkdir()
    path = folder / "best/f.c"
    path.write_text("first attempt", encoding="utf-8")
    net.progress.sync("game", force=True)
    path.write_text("worse attempt", encoding="utf-8")
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["best_source"] == "worse attempt"
    path.unlink()
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["best_source"] is None


def test_an_uninstalled_compiler_does_not_clear_existing_work(project, monkeypatch):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='good old',status='matched' WHERE name='f'")
    net.progress.sync("game", force=True)
    manifest(folder, compiler={"toolchain": "gcc-2.7.2-kmc"})
    monkeypatch.setattr("decompnet.app.progress.toolchains.installed", lambda _: None)
    with pytest.raises(NetworkError, match="gcc-2.7.2-kmc"):
        net.progress.sync("game", force=True)
    assert net.store.one("SELECT matched_source FROM functions WHERE name='f'")["matched_source"] == "good old"


def test_an_uninstalled_compiler_is_only_needed_for_code_to_judge(project, monkeypatch):
    net, folder, _ = project
    manifest(folder, compiler={"toolchain": "gcc-2.7.2-kmc"}, notes=[])
    monkeypatch.setattr("decompnet.app.progress.toolchains.installed", lambda _: None)
    net.progress.sync("game", force=True)  # a settings-only change: nothing is compiled
    assert net._function("f", "game")["status"] == "open"


def test_a_project_compiler_replaces_an_unknown_folders_choice(project, monkeypatch):
    """A save from another tool names a compiler decompdecomp can't install; the project's own Settings are the
    compiler the user already picked, so its saved code is judged with it and the folder is corrected."""
    net, folder, judged = project
    net._update_cfg("game", lambda cfg: cfg.__setitem__("compiler", {"toolchain": "gcc-2.7.2-kmc", "flags": []}))
    manifest(folder, compiler={"toolchain": "a-tool-this-app-does-not-have", "flags": []})
    monkeypatch.setattr("decompnet.app.progress.toolchains.installed",
                        lambda tid: {"installed": True} if tid == "gcc-2.7.2-kmc" else None)
    (folder / "src").mkdir(exist_ok=True)
    (folder / "src/f.c").write_text("good with the project compiler", encoding="utf-8")
    assert net.progress.sync("game", force=True)
    assert json.loads((folder / saves.MANIFEST).read_text())["compiler"]["toolchain"] == "gcc-2.7.2-kmc"
    assert ("f", "good with the project compiler") in judged


@pytest.mark.parametrize("remote", [False, True])
def test_opening_a_save_keeps_its_checkout_connected(project, tmp_path, monkeypatch, remote):
    net, folder, _ = project
    checkout = tmp_path / "checkout"
    shutil.copytree(folder, checkout)
    (checkout / ".git").mkdir()
    (checkout / ".git/config").write_text("original repository")
    (checkout / "src").mkdir()
    (checkout / "src/f.c").write_text("good contribution", encoding="utf-8")
    console = SimpleNamespace(read_game=lambda *args: SimpleNamespace(info={"sha1": "demo"}))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: console)
    monkeypatch.setattr(saves, "fetch", lambda *args: checkout)
    source = "https://example.org/progress.git" if remote else str(checkout)
    if remote:
        net = RomNetwork(tmp_path / "remote.db", tmp_path)
        root = tmp_path / "remote-game"
        root.mkdir()
        monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)

        def import_remote(*_args, **_kwargs):
            config = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}}
            with net.store.tx() as db:
                pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                                 (str(root), json.dumps(config))).lastrowid
                db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) "
                           "VALUES(?,1,'',0,'')", (pid,))
                for name, address in (("f", 4096), ("g", 4100)):
                    db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                               "VALUES(?,?,?,4,1,'open','')", (pid, name, address))
            return "game"

        def attempt(_agent, name, body, _slug):
            with net.store.tx() as db:
                db.execute("UPDATE functions SET status='matched',matched_source=?,best_source=?,best_score=0 "
                           "WHERE name=?", (body, body, name))
            return {"matched": True}

        monkeypatch.setattr(net, "import_rom", import_remote)
        monkeypatch.setattr(net, "attempt", attempt)
    else:
        net._update_cfg("game", lambda cfg: cfg.__setitem__("progress_folder", str(checkout)))
        monkeypatch.setattr(net, "import_rom", lambda *args, **kwargs: "game")
    result = saves.restore(net, source, tmp_path / "own-game", log=lambda _: None)
    assert result == {"slug": "game", "matched": 1, "best": 0, "lost": []}
    live = saves.default_folder(net, "game")
    assert live == (root / "progress" if remote else checkout)
    assert (live / ".git/config").read_text() == "original repository"
    manifest(live, instructions="next pull")
    net.progress.sync("game", force=True)
    assert net.instructions("game") == "next pull"


def test_an_unchanged_match_does_not_need_recompiling_after_restart(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good\n", encoding="utf-8")
    net.progress.sync("game", force=True)
    net.progress = Progress(net)
    net.progress.sync("game", force=True)
    assert judged == [("f", "good\n")]


def test_an_old_full_snapshot_baseline_is_converted_to_per_file_hashes(project):
    """A database from before the baseline kept hashes keeps working, and stops storing two full copies."""
    net, folder, _ = project
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")["id"]
    files = _read(folder)
    with net.store.tx() as db:
        db.execute("UPDATE progress_state SET format=1,disk=?,projected=? WHERE project_id=?",
                   (json.dumps(files), json.dumps(files), pid))
    net.progress = Progress(net)
    assert not net.progress.sync("game", force=True)
    row = net.store.one("SELECT format,disk,projected FROM progress_state WHERE project_id=?", pid)
    assert row["format"] == 2 and row["disk"] == "{}" and row["projected"] == "{}"
    assert net.store.all("SELECT 1 FROM progress_files WHERE project_id=? AND side='projected'", pid)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE project_id=? AND name='f'",
                   (pid,))
    assert net.progress.sync("game", force=True)
    assert (folder / "best/f.c").read_text(encoding="utf-8") == "new attempt\n"


def test_dirty_triggers_never_fail_writes_for_rows_left_by_a_removed_project(tmp_path):
    """A database with a removed project's functions left in it (same id reused later) must still open and write."""
    database = tmp_path / "app.db"
    net = RomNetwork(database, tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,matched_source) "
                   "VALUES(999,'orphan',4096,4,1,'matched','','orphan C')")
        db.execute("UPDATE functions SET matched_source='changed' WHERE project_id=999")
        db.execute("INSERT INTO notes(project_id,text,created_at) VALUES(999,'orphan note',0)")
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(999,1,'',0,'')")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(999,'orphan',4096,4,'func')")
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(999,'orphan knowledge','accepted',0)")
        db.execute("UPDATE notes SET text='changed' WHERE project_id=999")
        db.execute("UPDATE context_versions SET header='changed' WHERE project_id=999")
        db.execute("UPDATE symbols SET name='changed' WHERE project_id=999")
        db.execute("UPDATE knowledge SET status='accepted' WHERE project_id=999")
        db.execute("DELETE FROM notes WHERE project_id=999")
        db.execute("DELETE FROM context_versions WHERE project_id=999")
        db.execute("DELETE FROM knowledge WHERE project_id=999")
    assert net.store.one("SELECT 1 FROM progress_dirty WHERE project_id=999") is None
    net.store.conn.close()

    reopened = RomNetwork(database, tmp_path)  # opening removes the leftover symbols; the triggers must not fail it
    assert reopened.store.one("SELECT name FROM functions WHERE project_id=999")["name"] == "orphan"
    with reopened.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='again' WHERE project_id=999")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(999,'orphan',4096,4,'func')")
        db.execute("DELETE FROM functions WHERE project_id=999")
        fork = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('fork','Fork',?,?)",
                          (str(tmp_path / "fork"), json.dumps({"kind": "fork"}))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,unit) VALUES(?,'fork_function','')", (fork,))
    assert reopened.store.one("SELECT 1 FROM progress_dirty WHERE project_id=999") is None
    assert reopened.store.one("SELECT 1 FROM progress_dirty WHERE project_id=?", fork) is None


def test_saving_preserves_source_line_numbers(project):
    net, folder, _ = project
    source = "\n\nint f(void) { return __LINE__; }\n\n"
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=?,status='matched' WHERE name='f'", (source,))
    saves.save(net, "game")
    assert (folder / "src/f.c").read_text() == source


def test_saving_to_a_chosen_folder_ignores_a_broken_default_folder(project, tmp_path):
    """#101 review: "Save now" elsewhere must still rescue the work when progress/decompdecomp.json has conflicts."""
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='good saved C',best_source='good saved C',"
                   "best_score=0 WHERE name='f'")
    (folder / saves.MANIFEST).write_text("<<<<<<< HEAD\n=======\n>>>>>>> branch\n", encoding="utf-8")
    chosen = tmp_path / "chosen"
    result = saves.save(net, "game", chosen)
    assert result["folder"] == str(chosen) and result["matched"] == 1
    assert (chosen / "src/f.c").read_text() == "good saved C\n"
    assert "f" in json.loads((chosen / saves.MANIFEST).read_text())["functions"]
    assert "<<<<<<<" in (folder / saves.MANIFEST).read_text()


def test_sync_refuses_to_write_through_a_linked_progress_file(project, tmp_path):
    net, folder, _ = project
    for name in ("README.md", saves.save_readme.CARD):
        outside = tmp_path / f"outside-{name}"
        outside.write_text("outside", encoding="utf-8")
        path = folder / name
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as error:
            pytest.skip(f"symlinks are unavailable: {error}")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE name='f'")
    with pytest.raises(NetworkError, match="symbolic links"):
        net.progress.sync("game", force=True)
    assert all((tmp_path / f"outside-{name}").read_text() == "outside"
               for name in ("README.md", saves.save_readme.CARD))


def test_sync_refuses_a_default_progress_folder_that_is_a_link_elsewhere(project, tmp_path):
    net, folder, _ = project
    outside = tmp_path / "elsewhere"
    shutil.copytree(folder, outside)
    shutil.rmtree(folder)
    try:
        folder.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    readme = (outside / "README.md").read_text()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE name='f'")
    with pytest.raises(NetworkError, match="symbolic link"):
        net.progress.sync("game", force=True)
    assert not (outside / "best/f.c").exists()
    assert (outside / "README.md").read_text() == readme  # the render never wrote through the link either


def test_a_progress_folder_the_user_chose_may_be_a_link(project, tmp_path):
    net, folder, _ = project
    outside = tmp_path / "elsewhere"
    shutil.copytree(folder, outside)
    shutil.rmtree(folder)
    try:
        folder.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    net._update_cfg("game", lambda cfg: cfg.__setitem__("progress_folder", str(folder)))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE name='f'")
    net.progress.sync("game", force=True)
    assert (outside / "best/f.c").read_text() == "new attempt\n"
    saves.save(net, "game")  # Save now writes the manifest through the configured link too
    assert (outside / saves.MANIFEST).exists()


def test_switching_branches_can_undo_a_function_split(project):
    net, folder, _ = project
    with net.store.tx() as db:
        for name, address in (("f", 4096), ("g", 4100)):
            function = split.RomFunction(name, address, address - 4096, "", [
                split.AsmLine(address - 4096, address, 0x24020001, "addiu", "$v0, $zero, 1")], Path("unused.s"))
            db.execute("UPDATE functions SET asm=? WHERE name=?", (net._asm_source(function), name))
    manifest(folder, layout=[{"name": "f", "vram": 4096, "size": 8}])
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["size"] == 8
    assert not net.store.one("SELECT 1 FROM functions WHERE name='g'")


def _overlay_project(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "rom": {"sha1": "demo"}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                         (str(tmp_path / "game"), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                   (pid,))
        for name, unit in (("stage_a_func_80001000", "ovl_stage_a"),
                           ("stage_b_func_80001000", "ovl_stage_b")):
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,matched_source,"
                       "best_source,best_score,matched_at) VALUES(?,?,4096,8,2,'matched',?,?,?,0,1)",
                       (pid, name, unit, f"int {name}(void) {{ return 1; }}", f"int {name}(void) {{ return 1; }}"))
    net.attempt = lambda *args: pytest.fail("a matching overlay function was judged again")
    return net, pid


def test_two_overlays_at_one_address_keep_their_rows_and_matches_on_the_first_sync(tmp_path):
    net, pid = _overlay_project(tmp_path)
    slug = "game"
    saves.save(net, slug)
    folder = saves.default_folder(net, slug)
    manifest = json.loads((folder / saves.MANIFEST).read_text())
    assert len(manifest["layout"]) == 2 and {entry["vram"] for entry in manifest["layout"]} == {4096}
    snapshot = [dict(row) for row in net.store.all(
        "SELECT name,vram,unit,status,matched_source FROM functions WHERE project_id=? ORDER BY name", pid)]
    with net.store.tx() as db:  # an existing project from main, whose progress folder has no baseline yet
        db.execute("DELETE FROM progress_state WHERE project_id=?", (pid,))
    net.progress = Progress(net)
    net.progress.sync(slug, force=True)
    assert [dict(row) for row in net.store.all(
        "SELECT name,vram,unit,status,matched_source FROM functions WHERE project_id=? ORDER BY name", pid)] == snapshot


def test_a_layout_that_cannot_be_applied_changes_neither_database_nor_folder(tmp_path):
    net, pid = _overlay_project(tmp_path)
    with net.store.tx() as db:
        db.execute("DELETE FROM functions WHERE project_id=?", (pid,))
        for name, address, size in (("f", 0x1000, 4), ("g", 0x1004, 4), ("h", 0x2000, 4), ("i", 0x2008, 4)):
            function = split.RomFunction(name, address, address - 0x1000 if address < 0x2000 else address - 0x2000,
                                         "", [split.AsmLine(address, address, 0x24020001, "addiu", "$v0, $zero, 1")],
                                         Path("unused.s"))
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,asm) "
                       "VALUES(?,?,?,4,1,'open','',?)", (pid, name, address, net._asm_source(function)))
    net.reader = lambda proj: lambda address, n: b"\0\0\0\1"  # the gap is not padding
    saves.save(net, "game")
    folder = saves.default_folder(net, "game")
    path = folder / saves.MANIFEST
    manifest = json.loads(path.read_text())
    manifest["layout"] = [{"name": "f", "vram": 0x1000, "size": 8, "status": "open"},
                          {"name": "h", "vram": 0x2000, "size": 12, "status": "open"}]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    db_before = "\n".join(net.store.conn.iterdump())
    folder_before = {p.relative_to(folder).as_posix(): p.read_bytes()
                     for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(NetworkError, match="crosses a gap"):
        net.progress.sync("game", force=True)
    assert "\n".join(net.store.conn.iterdump()) == db_before
    assert {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*") if p.is_file()} == folder_before


def test_a_layout_join_that_would_remove_a_match_is_refused(project):
    net, _, _ = project
    with net.store.tx() as db:
        for name, address in (("f", 4096), ("g", 4100)):
            function = split.RomFunction(name, address, address - 4096, "", [
                split.AsmLine(address - 4096, address, 0x24020001, "addiu", "$v0, $zero, 1")], Path("unused.s"))
            db.execute("UPDATE functions SET asm=? WHERE name=?", (net._asm_source(function), name))
        db.execute("UPDATE functions SET status='matched',matched_source='known match' WHERE name='g'")
    before = [dict(row) for row in net.store.all("SELECT * FROM functions ORDER BY name")]
    with pytest.raises(NetworkError, match="already matches"):
        saves._join_saved_layout(net, net._project("game"),
                                 [{"name": "f", "vram": 4096, "size": 8, "status": "open"}])
    assert [dict(row) for row in net.store.all("SELECT * FROM functions ORDER BY name")] == before


def test_joining_a_saved_layout_keeps_the_tails_attempts_and_says_what_joined(project):
    net, folder, _ = project
    agent, _ = net.local_agent("helper")
    with net.store.tx() as db:
        for name, address in (("f", 4096), ("g", 4100)):
            function = split.RomFunction(name, address, address - 4096, "", [
                split.AsmLine(address - 4096, address, 0x24020001, "addiu", "$v0, $zero, 1")], Path("unused.s"))
            db.execute("UPDATE functions SET asm=? WHERE name=?", (net._asm_source(function), name))
        head = db.execute("SELECT id,attempts FROM functions WHERE name='f'").fetchone()
        tail = db.execute("SELECT id FROM functions WHERE name='g'").fetchone()
        db.execute("UPDATE functions SET attempts=5 WHERE id=?", (tail["id"],))
        db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at) "
                   "VALUES(?,?,0,1,1,'tail attempt',1)", (tail["id"], agent.id))
    report = []
    saves._join_saved_layout(net, net._project("game"), [{"name": "f", "vram": 4096, "size": 8}], report.append)
    joined = net.store.one("SELECT id,attempts FROM functions WHERE name='f'")
    assert joined["id"] == head["id"] and joined["attempts"] == head["attempts"] + 5
    assert net.store.one("SELECT function_id FROM attempts WHERE source='tail attempt'")["function_id"] == head["id"]
    assert not net.store.one("SELECT 1 FROM functions WHERE name='g'")
    assert any("joined g into f" in line and "1 attempt(s)" in line for line in report)


def test_an_edit_arriving_during_a_save_is_read_on_the_next_pass(project, monkeypatch):
    net, folder, _ = project
    original = saves._publish
    done = False

    def publish(*args, **kwargs):
        nonlocal done
        result = original(*args, **kwargs)
        if not done:
            done = True
            (folder / "src").mkdir(exist_ok=True)
            (folder / "src/f.c").write_text("good concurrent PR", encoding="utf-8")
        return result

    monkeypatch.setattr(saves, "_publish", publish)
    net.set_instructions("game", "local setting")
    net.progress.sync("game", force=True)
    net.progress.sync("game", force=True)
    assert net._function("f", "game")["matched_source"] == "good concurrent PR"


def test_a_file_edit_between_acknowledgment_and_signature_is_read_next_time(project, monkeypatch):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.instructions','local setting') WHERE slug='game'")
    progress = net.progress
    original = progress._signature
    calls = 0

    def signature(path):
        nonlocal calls
        calls += 1
        if calls == 2:  # after _read acknowledged the files, before the cache signature is stored
            (path / "src").mkdir(exist_ok=True)
            (path / "src/f.c").write_text("good late contributor edit", encoding="utf-8")
        return original(path)

    monkeypatch.setattr(progress, "_signature", signature)
    assert progress.sync("game", force=True)
    assert progress.sync("game", force=True)
    assert net._function("f", "game")["matched_source"] == "good late contributor edit"


def test_polishing_started_before_a_pull_cannot_replace_the_contribution(project):
    net, folder, _ = project
    (folder / "src").mkdir()
    path = folder / "src/f.c"
    path.write_text("good original", encoding="utf-8")
    net.progress.sync("game", force=True)
    agent, _ = net.local_agent("helper")
    with pytest.raises(NetworkError, match="changed while this attempt compiled"):
        with net.progress.judging("game"):
            path.write_text("good PR", encoding="utf-8")
            worker = threading.Thread(target=lambda: net.progress.sync("game", force=True))
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            net.readability._readable(net._project("game"), net._function("f", "game"), agent, "stale polish")
    assert net._function("f", "game")["matched_source"] == "good PR"
