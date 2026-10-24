"""A ROM project's work saved as plain files and rebuilt from them on another computer (app/saves.py): the splits,
names, headers, notes, settings and what agents learned come back, every saved match is judged again, and a save
only restores onto the same game."""

import json
import shutil

import pytest

from decompnet.app import saves
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_restoring_room_members_credits_does_not_reject_their_names(tmp_path, monkeypatch):
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
    author = "@alex.claude-opus-1"
    manifest = {"notes": [{"by": author, "text": "Try an unsigned comparison.", "function": "f"}],
                "functions": {"f": {"vram": 4096, "best_by": author}}}
    result = saves._apply(net, "game", tmp_path, manifest, lambda *_: None)
    agent, _ = net.local_agent(author)
    assert result == {"matched": 0, "best": 1, "lost": []}
    assert judged == [(net.local_agent("progress-judge")[0].id, "f", "void f(void) {}", "game")]
    assert net.store.one("SELECT agent_id FROM notes WHERE text=?", "Try an unsigned comparison.")["agent_id"] == agent.id


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
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug, name, root, config) VALUES('game', 'Game', ?, ?)",
                   (str(old_home / "game"), json.dumps({"progress_folder": str(old_home / "game" / "progress")})))
    # Moving the folder is an offline operation; Windows won't remove open database files.
    reader = getattr(net.store._local, "conn", None)
    if reader is not None:
        reader.close()
    net.store.conn.close()
    shutil.copytree(old_home, new_home)
    shutil.rmtree(tmp_path / "old")
    moved = RomNetwork(new_home / "decomp.db", new_home)
    assert moved.project_row("game")["root"] == str(new_home / "game")
    assert saves.default_folder(moved, "game") == new_home / "game" / "progress"


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
