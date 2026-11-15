"""A save is written only when what it says changed.

A live 2,000-function game rewrote its 27 MB manifest about every two minutes, and two rewrites in three changed only
its "saved_at" and "revision": the dirty triggers fired on writes of the same value (and on functions.meta keys the
render never reads), and the publish counted the fresh stamp as a change."""
import json
import sqlite3

from decompnet import store as storage
from decompnet.app import saves
from tests import test_progress_speed as speed

project = speed.project


def _token(net, pid):
    return net.project_store(pid).one("SELECT token FROM progress_dirty WHERE project_id=?", pid)["token"]


def test_writes_of_the_same_value_do_not_mark_the_save(project):
    net, _, pid, _, _ = project
    before = _token(net, pid)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=best_score, status=status, matched_source=matched_source "
                   "WHERE project_id=? AND name='f1'", (pid,))
        # A meta key the save doesn't show (a review flag), on an open function
        db.execute("UPDATE functions SET meta=? WHERE project_id=? AND name='f2'",
                   (json.dumps({"assembly_review": {"reason": "x"}}), pid))
        db.execute("UPDATE symbols SET name=name WHERE project_id=?", (pid,))
    assert _token(net, pid) == before
    assert net.progress.sync("game", force=True) is False


def test_a_change_the_save_shows_still_marks_it(project):
    net, folder, pid, _, _ = project
    before = _token(net, pid)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=? WHERE project_id=? AND name='f2'",
                   (json.dumps({"not_c": {"agent-1": {"model": "m", "why": "hand-written"}}}), pid))
    assert _token(net, pid) != before
    before = _token(net, pid)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=7 WHERE project_id=? AND name=?", (pid, f"f{speed.FUNCTIONS - 1}"))
    assert _token(net, pid) != before
    assert net.progress.sync("game", force=True) is True
    manifest = json.loads((folder / saves.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["functions"][f"f{speed.FUNCTIONS - 1}"]["best_score"] == 7


def test_invalid_meta_never_fails_the_write(project):
    net, _, pid, _, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta='not json' WHERE project_id=? AND name='f3'", (pid,))
        db.execute("UPDATE functions SET meta='{}' WHERE project_id=? AND name='f3'", (pid,))


def test_a_render_that_only_restamps_the_manifest_writes_nothing(project):
    net, folder, pid, _, _ = project
    manifest = folder / saves.MANIFEST
    text, stat = manifest.read_text(encoding="utf-8"), manifest.stat()
    # Marked dirty with nothing the render reads changed (as an older trigger, or a rolled-back write, leaves it)
    with net.store.tx() as db:
        db.execute("UPDATE progress_dirty SET token='forced', metadata=1 WHERE project_id=?", (pid,))
    net.progress.sync("game", force=True)
    assert manifest.read_text(encoding="utf-8") == text
    assert manifest.stat().st_mtime_ns == stat.st_mtime_ns
    # and it is settled: the next check has nothing to publish
    assert net.progress.sync("game", force=True) is False
    # A real change afterwards still publishes, with the next revision
    revision = json.loads(text)["revision"]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=9 WHERE project_id=? AND name=?", (pid, f"f{speed.FUNCTIONS - 1}"))
    assert net.progress.sync("game", force=True) is True
    assert json.loads(manifest.read_text(encoding="utf-8"))["revision"] == revision + 1


def test_only_stamp_differs():
    def text(saved_at, revision, body="x"):
        return json.dumps({"format": 1, "saved_at": saved_at, "revision": revision, "title": body}, indent=1) + "\n"
    assert saves.only_stamp_differs(text(1.5, 7), text(2.5, 8))
    assert not saves.only_stamp_differs(text(1.5, 7, "a"), text(2.5, 8, "b"))
    assert not saves.only_stamp_differs(text(1.5, 7), text(1.5, 7))  # (identical: nothing to keep)
    assert not saves.only_stamp_differs(text(1.5, 7), None)
    assert not saves.only_stamp_differs(json.dumps({"title": "x"}), json.dumps({"title": "x", "a": 1}))


def test_the_trigger_reads_the_meta_keys_the_save_shows():
    assert storage.DIRTY_META_KEYS == saves.SET_ASIDE_META
    for key in saves.SET_ASIDE_META:
        assert f"'$.{key}'" in storage.DIRTY_TRIGGERS


def test_an_older_databases_triggers_are_replaced(tmp_path):
    path = tmp_path / "app.db"
    storage.Store(path).conn.close()
    conn = sqlite3.connect(path)
    for name in storage.DIRTY_UPDATE_TRIGGERS:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        conn.execute(f"DROP TRIGGER {name}")
        # The definition before this change: no check that the value changed
        start = sql.index("\nWHEN (") if "\nWHEN (" in sql else sql.index("\nWHEN OLD.")
        rest = sql[sql.index(" AND EXISTS", start) + len(" AND "):]
        conn.execute(sql[:start] + "\nWHEN " + rest)
    conn.commit()
    assert all("IS NOT NEW." not in conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
               for name in storage.DIRTY_UPDATE_TRIGGERS)
    conn.close()
    reopened = storage.Store(path)
    for name in storage.DIRTY_UPDATE_TRIGGERS:
        assert "IS NOT NEW." in reopened.one("SELECT sql FROM sqlite_master WHERE name=?", name)["sql"]
