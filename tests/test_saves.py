"""A ROM project's work saved as plain files and rebuilt from them on another computer (app/saves.py): the splits,
names, headers, notes, settings and what agents learned come back, every saved match is judged again, and a save
only restores onto the same game."""

import json
import shutil
from pathlib import Path

import pytest

from decompnet.app import saves
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_restore_notes_preserves_measurements_without_scanning_history(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')",
                         (str(tmp_path),)).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                   "VALUES(?,'f',4096,4,1,'open','')", (pid,))
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    monkeypatch.setattr(net, "add_note", lambda *args: pytest.fail("restore must not infer a new session"))
    statements = []
    net.store.conn.set_trace_callback(statements.append)
    notes = [{"by": "worker", "function": "f", "text": f"note {i}", "created_at": 123,
              "score_before": 120, "score_after": 120} for i in range(100)]
    notes += [{"by": "worker", "text": "legacy"},
              {"by": "worker", "function": "gone", "text": "removed function"}]
    saves._apply_context(net, "game", tmp_path, {"notes": notes}, lambda *_: None)
    net.store.conn.set_trace_callback(None)
    rows = net.store.all("SELECT * FROM notes ORDER BY id")
    assert len(rows) == 101
    assert (rows[0]["score_before"], rows[0]["score_after"], rows[0]["created_at"]) == (120, 120, 123)
    assert rows[-1]["score_before"] is None
    assert not any("FROM attempts" in sql or "FROM helper_sessions" in sql for sql in statements)
    proj = net._project("game")
    manifest = saves._render(net, "game")[0]
    saved = json.loads(manifest[saves.MANIFEST])["notes"]
    assert saved[0]["score_before"] == saved[0]["score_after"] == 120
    assert saved[0]["created_at"] == 123
    assert proj["id"] == pid


@pytest.mark.parametrize("author", ["@alex.claude-opus-1", "claude-sonnet-5-5-1"])
def test_restoring_room_members_credits_does_not_reject_their_names(tmp_path, monkeypatch, author):
    """A room credit starts with @; opening its save used runner-name validation and aborted restoration."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')",
                         (str(tmp_path),)).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                   "VALUES(?,'f',4096,4,1,'open','')", (pid,))
    (tmp_path / "best").mkdir()
    (tmp_path / "best" / "f.c").write_text("void f(void) {}")
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    judged = []

    def attempt(agent, name, source, slug):
        judged.append((agent.id, name, source, slug))
        return {"matched": False}

    monkeypatch.setattr(net, "attempt", attempt)
    original_agent, token = net.local_agent(author)
    original_hash = net.store.one("SELECT token_hash FROM agents WHERE id=?", original_agent.id)["token_hash"]
    manifest = {"notes": [{"by": author, "text": "Try an unsigned comparison.", "function": "f"}],
                "functions": {"f": {"vram": 4096, "best_by": author}}}
    result = saves._apply(net, "game", tmp_path, manifest, lambda *_: None)
    agent = net.attribution_agent(author)
    assert net.authenticate(token).id == original_agent.id
    assert net.store.one("SELECT token_hash FROM agents WHERE id=?", agent.id)["token_hash"] == original_hash
    assert result == {"matched": 0, "best": 1, "lost": []}
    assert judged == [(net.local_agent("progress-judge")[0].id, "f", "void f(void) {}", "game")]
    assert net.store.one("SELECT agent_id FROM notes WHERE text=?", "Try an unsigned comparison.")["agent_id"] == agent.id


def test_restoring_leaves_out_ranges_whose_compiler_isnt_known(tmp_path, monkeypatch):
    """#118: a save written where a project used a compiler decompdecomp doesn't have carried its address ranges
    back unchecked, and every function in one then failed to compile with the registry's "unknown toolchain" error."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')", (str(tmp_path),))
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    log = []
    manifest = {"compiler_ranges": [
        {"start": 0x80000000, "end": 0x80001000, "toolchain": "msvcxx-x86_64", "flags": ["/O2"]},
        {"start": 0x80001000, "end": 0x80002000, "toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]},
    ]}
    saves._apply(net, "game", tmp_path, manifest, log.append)
    cfg = json.loads(net.store.one("SELECT config FROM projects WHERE slug='game'")["config"])
    assert [r["toolchain"] for r in cfg["compiler_ranges"]] == ["gcc-2.7.2-kmc"]
    assert any("msvcxx-x86_64" in line and "0x80000000-0x80001000" in line for line in log)


@pytest.fixture
def worked(built, tmp_path):  # noqa: F811
    """The demo game with some work done: a split, a match, a rename, a best attempt, a note, settings."""
    (tmp_path / "one").mkdir()
    net = RomNetwork(tmp_path / "one" / "decomp.db", tmp_path / "one")
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    other = net.store.one("SELECT name FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns DESC "
                          "LIMIT 1", net._project(slug)["id"])["name"]
    net.compile_and_diff(agent, other, f"void {other}(void) {{ }}", slug)
    net.release(agent, other, "tried an empty body", slug)
    net.add_note(agent, slug, "clamps to [lo, hi]", "clamp_s32")
    net.set_instructions(slug, "Name functions in snake_case.")
    return net, slug, other


def test_a_saved_project_comes_back_on_another_computer(worked, tmp_path):
    net, slug, other = worked
    said = saves.save(net, slug, tmp_path / "save")
    assert said["matched"] == 1 and said["best"] == 1
    manifest = json.loads((tmp_path / "save" / saves.MANIFEST).read_text())
    assert (tmp_path / "save" / "src" / "clamp_s32.c").exists() and manifest["game"]["sha1"]
    assert not any(p.suffix in (".z64", ".bin", ".iso") for p in (tmp_path / "save").rglob("*"))  # no game data
    readme = (tmp_path / "save" / "README.md").read_text()
    assert "https://github.com/n-delic/decompdecomp" in readme
    assert "AI agents were used during the decompilation" in readme
    assert f"**Platform:** {manifest['platform']}" in readme
    assert f"**Compiler:** {manifest['compiler']['toolchain']}" in readme
    assert "**Progress:** 1 matching function(s), 1 best attempt(s)" in readme
    assert "the only part of the project to share" in readme and "best/<function>.c" in readme  # what to share, what's in it

    (tmp_path / "two").mkdir()
    fresh = RomNetwork(tmp_path / "two" / "decomp.db", tmp_path / "two")
    out = saves.restore(fresh, str(tmp_path / "save"), ROM, log=lambda *_: None)
    assert out["matched"] == 1 and out["lost"] == [] and out["best"] == 1
    fn = fresh._function("clamp_s32", out["slug"])
    assert fn["status"] == "matched" and fn["vram"] == 0x800007D0  # the split and the name came back
    assert fresh.instructions(out["slug"]) == "Name functions in snake_case."
    assert fresh._function(other, out["slug"])["best_source"]
    assert any("clamps" in r["text"] for r in fresh.store.all("SELECT text FROM notes"))
    assert fresh.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0  # nothing left claimed


def test_a_new_restore_adopts_saved_settings_over_fresh_import_defaults(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    root = tmp_path / "workspace" / "game"
    root.mkdir(parents=True)
    save = tmp_path / "save"
    (save / "include").mkdir(parents=True)
    (save / saves.MANIFEST).write_text(json.dumps({
        "format": saves.FORMAT, "title": "Game", "platform": "n64", "game": {"sha1": "demo"},
        "headers": {"": "common.h"}, "layout": [{"name": "f", "vram": 4096, "size": 4}],
        "functions": {}, "instructions": "instructions from saved project", "knowledge": [], "notes": [],
    }), encoding="utf-8")
    (save / "include/common.h").write_text("/* saved header */\n", encoding="utf-8")
    monkeypatch.setattr(saves.consoles, "identify",
                        lambda _: type("Console", (), {"read_game": lambda *args: type(
                            "Picked", (), {"info": {"sha1": "demo"}})()})())
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)

    def import_game(*_args, **_kwargs):
        config = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"},
                  "instructions": "fresh import defaults"}
        with net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                             (str(root), json.dumps(config))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) "
                       "VALUES(?,1,'',0,'')", (pid,))
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,'f',4096,4,1,'open','')", (pid,))
        return "game"

    monkeypatch.setattr(net, "import_rom", import_game)
    result = saves.restore(net, str(save), tmp_path / "own-game.bin", log=lambda *_: None)
    assert result == {"matched": 0, "best": 0, "lost": [], "slug": "game"}
    assert net.instructions("game") == "instructions from saved project"


def test_a_restore_that_cannot_judge_leaves_no_half_imported_project(tmp_path, monkeypatch):
    """#101 review: importing and splitting happen before the save is applied; a failed restore (a known compiler
    that isn't installed here) must not leave the imported project and its folder behind."""
    from types import SimpleNamespace

    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    save = tmp_path / "save"
    (save / "src").mkdir(parents=True)
    (save / "include").mkdir()
    (save / "src/f.c").write_text("void f(void) {}\n", encoding="utf-8")
    (save / "include/common.h").write_text("/* saved header */\n", encoding="utf-8")
    (save / saves.MANIFEST).write_text(json.dumps({
        "format": saves.FORMAT, "title": "Game", "platform": "n64", "game": {"sha1": "demo"},
        "headers": {"": "common.h"}, "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": []},
        "layout": [{"name": "f", "vram": 4096, "size": 4}],
        "functions": {"f": {"vram": 4096, "matched_by": ""}}, "knowledge": [], "notes": [],
    }), encoding="utf-8")
    monkeypatch.setattr(saves.consoles, "identify", lambda _: SimpleNamespace(
        read_game=lambda *_: SimpleNamespace(info={"sha1": "demo"})))
    root = tmp_path / "workspace" / "game"

    def import_game(*_args, **_kwargs):
        root.mkdir(parents=True)
        with net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                             (str(root), json.dumps({"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}}))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                       (pid,))
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,'f',4096,4,1,'open','')", (pid,))
        return "game"

    monkeypatch.setattr(net, "import_rom", import_game)
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    monkeypatch.setattr("decompnet.app.progress.toolchains.installed", lambda _: None)
    with pytest.raises(NetworkError, match="gcc-2.7.2-kmc"):
        saves.restore(net, str(save), tmp_path / "own-game", log=lambda *_: None)
    assert net.store.all("SELECT slug FROM projects") == []
    assert not root.exists()
    assert (save / saves.MANIFEST).exists()  # the user's saved folder is untouched


def test_a_save_restores_only_onto_the_same_game(worked, tmp_path):
    net, slug, _ = worked
    saves.save(net, slug, tmp_path / "save")
    other = tmp_path / "other.z64"
    data = bytearray(ROM.read_bytes())
    data[0x1000] ^= 0xFF
    other.write_bytes(bytes(data))
    (tmp_path / "x").mkdir()
    with pytest.raises(NetworkError, match="not the same game"):
        saves.restore(RomNetwork(tmp_path / "x" / "d.db", tmp_path / "x"), str(tmp_path / "save"), other,
                      log=lambda *_: None)
    shutil.rmtree(tmp_path / "save" / "src")
    (tmp_path / "save" / saves.MANIFEST).unlink()
    with pytest.raises(NetworkError, match="isn't a decompdecomp save"):
        saves.restore(net, str(tmp_path / "save"), ROM, log=lambda *_: None)


def test_restoring_does_not_replace_newer_work_in_an_already_imported_game(worked, tmp_path, monkeypatch):
    net, slug, _ = worked
    saves.save(net, slug, tmp_path / "save")
    before = net.store.one("SELECT matched_source FROM functions WHERE name='clamp_s32'")["matched_source"]
    monkeypatch.setattr(net, "import_rom", lambda *args, **kwargs: pytest.fail("imported into existing project"))
    with pytest.raises(NetworkError, match="already open"):
        saves.restore(net, str(tmp_path / "save"), ROM, log=lambda *_: None)
    assert net.store.one("SELECT matched_source FROM functions WHERE name='clamp_s32'")["matched_source"] == before


def test_saving_to_a_chosen_folder_prunes_only_files_from_its_previous_manifest(worked, tmp_path):
    net, slug, _ = worked
    folder = tmp_path / "chosen-save"
    saves.save(net, slug, folder)
    source = folder / "src/clamp_s32.c"
    unrelated = folder / "src/contributor-notes.txt"
    unrelated.write_text("keep this")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,matched_agent_id=NULL,matched_at=NULL "
                   "WHERE name='clamp_s32'")
    saves.save(net, slug, folder)
    assert not source.exists()
    assert unrelated.read_text() == "keep this"


def test_publish_rejects_symlink_paths_before_writing(tmp_path):
    root = tmp_path / "save"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "src").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(NetworkError, match="symbolic links"):
        saves._publish(root, {"src/f.c": "code"})
    assert not (outside / "f.c").exists()


def test_autosave_saves_what_changed_only(worked):
    net, slug, _ = worked
    project_id = net._project(slug)["id"]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new attempt',best_score=10 WHERE id="
                   "(SELECT id FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns DESC LIMIT 1)",
                   (project_id,))
    auto = saves.Autosave(net)
    assert auto.run_once() == [slug] and auto.run_once() == []
    assert any((saves.default_folder(net, slug) / "best").glob("*.c"))
    assert (saves.default_folder(net, slug) / saves.MANIFEST).exists()


def test_a_whole_decomp_folder_copied_elsewhere_keeps_its_projects(tmp_path):
    """Project folders were stored as absolute paths (/home/decomp/Decomp/... inside the old Windows install's WSL):
    a Decomp folder copied to another computer or user name finds its projects again by their names."""
    old_home, new_home = tmp_path / "old" / "Decomp", tmp_path / "new" / "Decomp"
    (old_home / "game").mkdir(parents=True)
    net = RomNetwork(old_home / "decomp.db", old_home)
    old_root = old_home / "game"
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug, name, root, config) VALUES('game', 'Game', ?, ?)",
                   (str(old_root), json.dumps({"progress_folder": str(old_root / "progress"),
                                               "save_folder": str(old_root / "exports")})))
    # Moving the folder is an offline operation; Windows won't remove open database files.
    reader = getattr(net.store._local, "conn", None)
    if reader is not None:
        reader.close()
    net.store.conn.close()
    shutil.copytree(old_home, new_home)
    shutil.rmtree(tmp_path / "old")
    moved = RomNetwork(new_home / "decomp.db", new_home)
    assert moved.project_row("game")["root"] == str(new_home / "game")
    with moved.store.tx() as db:
        old_root = str(old_home / "game")
        db.execute("UPDATE projects SET root=?,config=? WHERE slug='game'",
                   (old_root, json.dumps({"progress_folder": str(old_home / "game" / "progress"),
                                          "save_folder": str(old_home / "game" / "exports")})))
    assert moved.find_moved_projects() == ["game"]
    cfg = moved.cfg(moved.project_row("game"))
    assert cfg["progress_folder"] == str(new_home / "game" / "progress")
    assert cfg["save_folder"] == str(new_home / "game" / "exports")
    assert saves.default_folder(moved, "game") == new_home / "game" / "progress"


def test_restoring_an_old_manifest_preserves_why_a_function_was_set_aside(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')",
                         (str(tmp_path),)).lastrowid
        function_id = db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,meta) "
                                 "VALUES(?,'f',4096,4,1,'open','',?)",
                                 (pid, json.dumps({"keep": "this metadata"}))).lastrowid
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    saves._apply(net, "game", tmp_path, {
        "layout": [{"name": "f", "vram": 4096, "size": 4}],
        "set_aside": [{"name": "f", "vram": 4096, "asm_code": "agents set it aside",
                       "not_c": {"alex": {"model": "claude", "why": "no C expression matches", "t": 1.0}}}],
        "functions": {},
    }, lambda *_: None)
    row = net.store.one("SELECT status,meta FROM functions WHERE id=?", function_id)
    assert row["status"] == "asm"
    assert json.loads(row["meta"]) == {
        "keep": "this metadata", "asm_code": "agents set it aside",
        "not_c": {"alex": {"model": "claude", "why": "no C expression matches", "t": 1.0}},
    }


def test_a_project_mode_games_save_writes_its_closest_attempts(tmp_path):
    """#43: a DS project had no Save, and its near-misses (97-99.9%) were only in the database. Its save writes each
    open function's closest attempt, saying how close it is and where it belongs; matched C is the project's own."""
    import json

    from decompnet.app import saves
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "platform": "nds", "ready": True}, "x")
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,best_source,best_score,best_agent_id,"
                   "meta) VALUES(1,'f',40,10,'open','u','int f(void) { return 1; }',30,?,?)",
                   (agent.id, json.dumps({"source": "src/auto/u.c"})))
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,matched_source) "
                   "VALUES(1,'g',8,2,'matched','u','void g(void) {}')")
    said = saves.save(net, "p")
    assert said["best"] == 1 and said["folder"] == str(root / "progress")
    best = (root / "progress" / "best" / "f.c").read_text()
    assert "97.0% match" in best and "claude-sonnet-5-5-1" in best and "src/auto/u.c" in best and "return 1;" in best
    assert not (root / "progress" / "best" / "g.c").exists() and (root / "progress" / "README.md").exists()


def test_plain_save_directs_standalone_build_checkouts_to_repository_sync(tmp_path):
    (tmp_path/'build.json').write_text('{}')
    (tmp_path/'build.py').write_text('# keep build setup')
    with pytest.raises(NetworkError,match='Sync to Git repository'):
        saves._check_save_paths(tmp_path,chosen=True)
    assert (tmp_path/'build.py').read_text()=='# keep build setup'


def test_new_attribution_has_no_token_and_later_runner_keeps_its_identity(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    credit = net.attribution_agent("new-author")
    row = net.store.one("SELECT token_hash FROM agents WHERE id=?", credit.id)
    assert row["token_hash"].startswith("attribution:")
    assert net.attribution_agent("new-author").id == credit.id
    runner, token = net.local_agent("new-author")
    assert runner.id == credit.id and net.authenticate(token).id == credit.id
    assert net.attribution_agent("new-author").id == credit.id
    assert net.authenticate(token).id == credit.id


@pytest.mark.parametrize('public_import', [False, True])
def test_combined_checkout_restores_verified_work_without_building(worked, tmp_path, public_import, monkeypatch):
    from decompnet.forks.project import ForkProject

    net, slug, other = worked
    checkout = tmp_path / 'checkout'
    saves.save(net, slug, checkout / 'progress')
    (checkout / 'objdiff.json').write_text('{}')
    (checkout / 'build.json').write_text('{}')
    (checkout / 'build.py').write_text('raise RuntimeError("do not execute the standalone build")')
    assert ForkProject(checkout).needs()['saved_progress']
    fresh_home = tmp_path / 'fresh'
    fresh_home.mkdir()
    fresh = RomNetwork(fresh_home / 'db', fresh_home)
    if public_import:
        fresh.forks._register('checkout', 'Checkout', '', checkout,
                              {'kind': 'fork', 'ready': False, 'platform': 'n64', 'repo_url': 'https://example.com/repo'}, 'cloned')
        result = fresh.forks.setup('checkout', ROM, log=lambda *_: None)
        assert fresh.store.one("SELECT 1 FROM projects WHERE slug='checkout'") is None
        assert checkout.is_dir()
    else:
        result = saves.restore(fresh, str(checkout), ROM, log=lambda *_: None)
    assert result['matched'] == 1 and result['best'] == 1 and result['lost'] == []
    assert fresh._function('clamp_s32', result['slug'])['status'] == 'matched'
    assert fresh._function(other, result['slug'])['best_source']
    assert fresh.instructions(result['slug']) == 'Name functions in snake_case.'
    config = fresh.cfg(fresh.project_row(result['slug']))
    assert Path(config['progress_folder']) == checkout / 'progress'
    if public_import:
        assert config['repo_url'] == 'https://example.com/repo'


@pytest.mark.parametrize('error,advice', [(PermissionError('denied'), 'write permission'),
                                         (FileNotFoundError('gone'), 'disk is connected'),
                                         (OSError(28, 'disk full'), 'Free disk space'),
                                         (RuntimeError('invalid manifest'), 'complete traceback')])
def test_autosave_errors_show_cause_folder_build_and_retry_other_projects(tmp_path, caplog, monkeypatch, error, advice):
    from types import SimpleNamespace

    clock = [100.0]
    monkeypatch.setattr(saves.time, 'monotonic', lambda: clock[0])
    rows = [{'slug': 'bad', 'root': str(tmp_path), 'config': '[]'},
            {'slug': 'good', 'root': str(tmp_path), 'config': '{}'}]
    calls = []
    def sync(slug, **kwargs):
        calls.append(slug)
        if slug == 'bad':
            raise error
        return True
    net = SimpleNamespace(store=SimpleNamespace(all=lambda *_: rows), progress=SimpleNamespace(sync=sync))
    net.project_store = lambda _: net.store
    auto = saves.Autosave(net)
    assert auto.run_once() == ['good']
    assert advice in caplog.text and type(error).__name__ in caplog.text
    assert str(tmp_path / 'progress') in caplog.text
    assert 'RetroReversing fork' in caplog.text
    auto.run_once()
    assert calls == ['bad', 'good', 'good']
    clock[0] += 31
    auto.run_once()
    assert calls.count('bad') == 2


def test_restore_reuses_adjacent_demo_game_with_independent_project_database(worked, tmp_path):
    net, slug, _ = worked
    pid = net.project_row(slug)['id']
    with net.knowledge._store(pid).tx() as db:
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(?,?,'accepted',1)",
                   (pid, 'Preserve verified knowledge'))
    save = tmp_path/'portable'
    saves.save(net, slug, save)
    shutil.copyfile(ROM, save/ROM.name)
    (tmp_path/'fresh').mkdir()
    fresh = RomNetwork(tmp_path/'fresh/app.db', tmp_path/'fresh', project_databases=True)
    result = saves.restore(fresh, str(save), log=lambda *_:None)
    assert result['matched'] == 1 and result['best'] == 1 and not result['lost']
    proj = fresh.project_row(result['slug'])
    assert fresh.project_store(proj['id']) is not fresh.store
    assert fresh.knowledge._store(proj['id']).one("SELECT text FROM knowledge WHERE status='accepted'")['text'] == 'Preserve verified knowledge'
