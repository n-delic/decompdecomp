"""A collaborator's files take precedence over SQLite, without an import button or a second on-disk copy."""

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import saves
from decompnet.app.engine import RomNetwork
from decompnet.app.progress import Progress
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


def test_a_restart_reads_progress_before_writing_stale_database_sources(project):
    net, folder, judged = project
    (folder / "src").mkdir()
    (folder / "src/f.c").write_text("good contribution", encoding="utf-8")
    net.progress = Progress(net)
    net.project_row("game")
    assert judged == [("f", "good contribution")]
    assert net._function("f", "game")["matched_source"] == "good contribution"


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


def test_an_unavailable_compiler_does_not_clear_existing_work(project, monkeypatch):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='good old',status='matched' WHERE name='f'")
    net.progress.sync("game", force=True)
    manifest(folder, compiler={"toolchain": "missing"})
    monkeypatch.setattr("decompnet.app.progress.toolchains.installed", lambda _: None)
    with pytest.raises(NetworkError, match="Install the compiler"):
        net.progress.sync("game", force=True)
    assert net.store.one("SELECT matched_source FROM functions WHERE name='f'")["matched_source"] == "good old"


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
    monkeypatch.setattr(net, "import_rom", lambda *args, **kwargs: "game")
    monkeypatch.setattr(saves, "fetch", lambda *args: checkout)
    source = "https://example.org/progress.git" if remote else str(checkout)
    result = saves.restore(net, source, tmp_path / "own-game", log=lambda _: None)
    assert result == {"slug": "game", "matched": 1, "best": 0, "lost": []}
    live = saves.default_folder(net, "game")
    assert live == (folder if remote else checkout)
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


def test_saving_preserves_source_line_numbers(project):
    net, folder, _ = project
    source = "\n\nint f(void) { return __LINE__; }\n\n"
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=?,status='matched' WHERE name='f'", (source,))
    saves.save(net, "game")
    assert (folder / "src/f.c").read_text() == source


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
