"""Progress manifests no longer pile up as unused shared payloads; unused payloads are collected and compacted away."""

import json
import threading
import time
import zlib
from pathlib import Path

import pytest

from decompnet import payload_links
from decompnet.app import database_storage, header_cleanup, saves
from decompnet.app.engine import RomNetwork
from decompnet.network import Network


class Log:
    def __init__(self):
        self.cancel_event = threading.Event()
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise RuntimeError('cancelled')

    def progress(self, data):
        pass


@pytest.fixture
def project(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}, "instructions": "old"}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                         (str(tmp_path / "game"), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')", (pid,))
        # Enough functions that the manifest is large text (the shared store's threshold is 512 bytes).
        for index in range(40):
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,?,?,4,1,'open','')", (pid, f"func_{index:04d}", 4096 + 4 * index))
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)
    net.progress.sync("game", force=True)
    return net, pid, saves.default_folder(net, "game")


def payloads(net):
    return net.store.one("SELECT count(*) AS n FROM text_payloads")["n"]


def stored_manifest(net, pid):
    row = net.store.one("SELECT manifest FROM progress_manifests WHERE project_id=?", pid)
    return zlib.decompress(row["manifest"]).decode("utf-8")


def note(net, pid, text):
    with net.store.tx() as db:
        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,NULL,?,?)",
                   (pid, text, time.time()))


def test_repeated_syncs_keep_one_manifest_and_add_no_shared_payloads(project):
    net, pid, folder = project
    before = payloads(net)
    for index in range(6):
        note(net, pid, f"note {index} " + "x" * 40)
        assert net.progress.sync("game", force=True)
    assert payloads(net) == before
    row = net.store.one("SELECT disk_manifest,projected_manifest FROM main.progress_state WHERE project_id=?", pid)
    assert row["disk_manifest"] is None and row["projected_manifest"] is None
    assert net.store.one("SELECT count(*) AS n FROM progress_manifests")["n"] == 1
    # The merge base is exactly what was published.
    assert stored_manifest(net, pid) == (folder / saves.MANIFEST).read_text(encoding="utf-8")
    net.progress.baselines.clear()
    assert net.progress._baseline(pid).projected.manifest == (folder / saves.MANIFEST).read_text(encoding="utf-8")


def test_an_older_versions_manifest_is_the_merge_base_and_its_payload_is_released(project):
    net, pid, folder = project
    published = (folder / saves.MANIFEST).read_text(encoding="utf-8")
    # The format older versions wrote: both manifests in the compressed columns, one shared payload.
    with net.store.tx() as db:
        db.execute("DELETE FROM progress_manifests")
        db.execute("UPDATE progress_state SET disk_manifest=?,projected_manifest=? WHERE project_id=?",
                   (published, published, pid))
    legacy = net.store.one("SELECT lower(hex(projected_manifest)) AS hash FROM main.progress_state")["hash"]
    assert net.store.one("SELECT 1 FROM text_payloads WHERE hash=?", legacy)
    payload_links.collect(net.store)  # completes the link index; the manifest is still referenced
    assert net.store.one("SELECT 1 FROM text_payloads WHERE hash=?", legacy)
    net.progress.baselines.clear()
    baseline = net.progress._baseline(pid)
    assert baseline.projected.manifest == published and not baseline.stored
    note(net, pid, "a new note " + "y" * 40)
    assert net.progress.sync("game", force=True)
    assert net.store.one("SELECT 1 FROM text_payloads WHERE hash=?", legacy) is None
    assert stored_manifest(net, pid) == (folder / saves.MANIFEST).read_text(encoding="utf-8")


def test_release_keeps_payloads_until_the_index_is_complete_and_while_referenced(project):
    net, pid, _ = project
    text = "shared text " * 100
    with net.store.tx() as db:
        db.execute("UPDATE progress_state SET projected_manifest=? WHERE project_id=?", (text, pid))
        db.execute("UPDATE functions SET asm=? WHERE name='func_0000'", (text,))
    key = net.store.one("SELECT lower(hex(projected_manifest)) AS hash FROM main.progress_state")["hash"]
    with net.store.tx() as db:
        db.execute("UPDATE progress_state SET projected_manifest=NULL")
        # Not indexed: nothing can be proven unused.
        assert payload_links.release(db, [key]) == 0
    payload_links.collect(net.store)
    with net.store.tx() as db:
        assert payload_links.indexed(db)
        assert payload_links.release(db, [key]) == 0  # functions.asm still names it
        db.execute("UPDATE functions SET asm='nop' WHERE name='func_0000'")
        assert payload_links.release(db, [key]) == 1
    # A column whose link triggers are new (a table created later) makes the index incomplete again.
    with net.store.tx() as db:
        for event in ("insert", "update", "delete"):
            db.execute(f"DROP TRIGGER payload_link_functions_asm_{event}")
        assert not payload_links.indexed(db)
        payload_links.install(db, "functions", "asm", blob=True)
        assert not payload_links.indexed(db)


def test_automatic_collection_waits_for_an_index_and_runs_in_the_background(project):
    net, pid, _ = project

    class Jobs:
        _lock = threading.RLock()
        jobs = {}

        def __init__(self):
            self.calls = []

        def start(self, kind, work, **options):
            self.calls.append((kind, work, options))
            return 'job'

    jobs = Jobs()
    database_storage._next_collect.pop(id(net), None)
    database_storage.collect_tick(net, jobs, 0)
    start = database_storage.COLLECT_STARTUP_SECONDS
    assert database_storage.collect_tick(net, jobs, start) is None and not jobs.calls  # no index yet
    payload_links.collect(net.store)
    unused = "unused " * 200
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm=? WHERE name='func_0001'", (unused,))
        db.execute("UPDATE functions SET asm='nop' WHERE name='func_0001'")
    before = payloads(net)
    assert database_storage.collect_tick(net, jobs, start + 1) is None  # paced
    database_storage.collect_tick(net, jobs, start + database_storage.COLLECT_SECONDS)
    assert [call[0] for call in jobs.calls] == [database_storage.COLLECT_KIND]
    assert jobs.calls[0][2]['foreground'] is False
    assert jobs.calls[0][1](Log())['removed'] == 1
    assert payloads(net) == before - 1


def test_notes_are_read_again_only_when_they_or_function_names_change(project):
    net, pid, _ = project
    store = saves._knowledge_store(net, pid)
    note(net, pid, "first")
    reads = []
    original = saves._read_note_rows

    def counted(store, pid):
        reads.append(pid)
        return original(store, pid)

    saves._read_note_rows = counted
    try:
        assert [r["text"] for r in saves._note_rows(store, pid)] == ["first"]
        assert [r["text"] for r in saves._note_rows(store, pid)] == ["first"]
        assert len(reads) == 1
        with net.store.tx() as db:
            db.execute("UPDATE notes SET text='edited'")
        assert [r["text"] for r in saves._note_rows(store, pid)] == ["edited"]
        with net.store.tx() as db:
            db.execute("UPDATE notes SET function_id=(SELECT id FROM functions WHERE name='func_0002')")
        assert saves._note_rows(store, pid)[0]["fn"] == "func_0002"
        with net.store.tx() as db:
            db.execute("UPDATE functions SET name='renamed' WHERE name='func_0002'")
        assert saves._note_rows(store, pid)[0]["fn"] == "renamed"
        assert len(reads) == 4
        assert saves._note_rows(store, pid)[0]["fn"] == "renamed"
        assert len(reads) == 4
    finally:
        saves._read_note_rows = original


def test_compaction_covers_each_project_database(tmp_path):
    catalog = Network(tmp_path / 'catalog.sqlite3')
    leaves = {}
    try:
        for pid, slug in ((1, 'first'), (2, 'second')):
            folder = tmp_path / slug
            folder.mkdir()
            leaf = Network(folder / 'project.sqlite3', repair_archive=catalog.store.conn.repair_archive)
            leaves[pid] = leaf.store
            for store in (catalog.store, leaf.store):
                with store.tx() as db:
                    db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(?,?,?,'mips','.','{}')",
                               (pid, slug, slug))
            leaf._pool.shutdown()
        catalog.project_store = leaves.__getitem__
        store = leaves[2]
        with store.tx() as db:
            db.execute('CREATE TABLE scratch(payload BLOB)')
            db.execute('INSERT INTO scratch VALUES(randomblob(2000000))')
        store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with store.tx() as db:
            db.execute('DELETE FROM scratch')
        store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        size = header_cleanup.disk_bytes(Path(store.path))
        result = header_cleanup.compact(catalog, Log())
        assert result['reclaimed_bytes'] > 1000000
        assert header_cleanup.disk_bytes(Path(store.path)) < size - 1000000
        assert store.one('PRAGMA integrity_check')[0] == 'ok'
    finally:
        catalog._pool.shutdown()
        for store in {catalog.store, *leaves.values()}:
            store.conn.close()


def test_offline_compaction_opens_project_databases_with_their_namespace(tmp_path, monkeypatch, capsys):
    """tools/compact_databases.py on a real layout: project databases carry an identifier namespace and need the
    catalog's repair archive, as the app opens them (it crashed on the first project database before). A deleted
    project's database is fenced against writes and never opened by the app, so it is compacted only (opening an old
    one upgraded its schema and hit the fence)."""
    import importlib.util
    import sqlite3
    import sys

    from decompnet.shard_ids import KEY
    from decompnet.store import Store
    internal = tmp_path / '.decomp'
    (internal / 'project-databases').mkdir(parents=True)
    catalog = Network(internal / 'decomp.db')
    try:
        leaf = Store(internal / 'project-databases' / '7.sqlite3', project_namespace=7,
                     repair_archive=catalog.store.conn.repair_archive)
        with leaf.tx() as db:
            db.execute('CREATE TABLE scratch(payload BLOB)')
            db.execute('INSERT INTO scratch VALUES(randomblob(2000000))')
        with leaf.tx() as db:
            db.execute('DELETE FROM scratch')
        leaf.conn.close()
        # A deleted project's database stays on disk fenced against writes (project_files.Files._mark_removed).
        from decompnet.project_files import Files
        gone = Store(internal / 'project-databases' / '8.sqlite3', project_namespace=8,
                     repair_archive=catalog.store.conn.repair_archive)
        Files._mark_removed(None, gone)
        gone.conn.close()
    finally:
        catalog._pool.shutdown()
        catalog.store.conn.close()
    before = (internal / 'project-databases' / '7.sqlite3').stat().st_size
    spec = importlib.util.spec_from_file_location(
        'compact_databases', Path(__file__).parents[1] / 'tools' / 'compact_databases.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(sys, 'argv', ['compact_databases.py', str(tmp_path)])
    assert tool.main() == 0
    path = internal / 'project-databases' / '7.sqlite3'
    assert path.stat().st_size < before - 1000000
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()[0] == '7'
    with sqlite3.connect(internal / 'project-databases' / '8.sqlite3') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    out = capsys.readouterr().out
    assert out.split('8.sqlite3:')[1].splitlines()[1].strip() == 'a deleted project: compacted only'
