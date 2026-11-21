"""A compile made 290 database reads, 62% of them the same two lookups; under load each read costs about 3 ms. Within
one tool call, revisions and the rows a call asks for again are read once until the app writes (Store.versions_held),
and a project's slug is looked up once (project_files)."""
from decompnet.store import Store
from tests.test_claims import _game


def counted(net, monkeypatch):
    reads = []
    original = Store._read

    def read(self, sql, args, single):
        reads.append(sql)
        return original(self, sql, args, single)
    monkeypatch.setattr(Store, "_read", read)
    return reads


def test_revisions_and_rows_are_read_once_a_call_until_a_write(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [("a", 4, "open")])
    store = net.project_store("game")
    reads = counted(net, monkeypatch)
    with Store.versions_held():
        first = store.read_version("inputs", 1)
        row = store.held_one("SELECT * FROM functions WHERE name=?", "a")
        assert store.read_version("inputs", 1) == first
        assert store.held_one("SELECT * FROM functions WHERE name=?", "a")["best_score"] == row["best_score"]
        assert len(reads) == 2
        with store.tx() as db:  # a write: what the call reads next is read again, and sees it
            db.execute("UPDATE functions SET best_score=7 WHERE name='a'")
            assert store.held_one("SELECT * FROM functions WHERE name=?", "a")["best_score"] == 7  # (inside: read)
        assert store.held_one("SELECT * FROM functions WHERE name=?", "a")["best_score"] == 7
        store.read_version("inputs", 1)
        assert len(reads) == 5
    reads.clear()
    store.read_version("inputs", 1)
    store.held_one("SELECT * FROM functions WHERE name=?", "a")
    assert reads.count("SELECT * FROM functions WHERE name=?") == 1  # No row cache outside the held call.


def test_a_project_slug_is_looked_up_once(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [("a", 4, "open")])
    first = net.project_store("game")
    reads = counted(net, monkeypatch)
    assert net.project_store("game") is first and net.project_store("game") is first
    assert not [sql for sql in reads if "FROM projects WHERE slug" in sql]
