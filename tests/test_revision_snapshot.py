"""read_version answers from a snapshot, but never misses a commit: its own, or another connection's."""
import sqlite3

from decompnet.store import Store


def bump(path, kind):
    other = sqlite3.connect(path)  # another connection, as another process would be
    other.execute("INSERT INTO read_revisions VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET revision=revision+1", (kind,))
    other.commit()
    other.close()


def test_a_commit_by_another_connection_is_seen_at_once(tmp_path):
    path = tmp_path / 'app.db'
    store = Store(path)
    before = store.read_version('inputs', 7)
    assert store.read_version('inputs', 7) == before
    for expected in range(before + 1, before + 4):
        bump(path, 'inputs:7')
        assert store.read_version('inputs', 7) == expected


def test_own_writes_and_new_kinds_are_seen(tmp_path):
    store = Store(tmp_path / 'app.db')
    assert store.read_version('never-written', 1) == 0
    with store.tx() as db:
        db.execute("INSERT INTO read_revisions VALUES('never-written:1',5)")
    assert store.read_version('never-written', 1) == 5
