"""What keeps the app quick with big games and many agents: reads beside writes, settings parsed once, polls that
don't take the writer's lock. Measured on a copy of the owner's database (GTA San Andreas, 12,627 functions, and three
more games); these tests need no compiler and no game."""

import json
import sqlite3
import threading
import time

from decompnet.app.engine import RomNetwork
from decompnet.store import WAL_SIZE_LIMIT_BYTES, Store

GCC = {"toolchain": "ee-gcc-2.95.3", "flags": ["-O2", "-G8"]}


def tiny_game(net: RomNetwork, slug: str = "game", functions: int = 4) -> dict:
    """A ROM project in the database only: settings, a header and a few functions, no files."""
    config = {"kind": "rom", "platform": "ps2", "big_endian": False, "compiler": GCC, "rom": {"file": "game.rom"}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug, name, root, arch, config) VALUES(?,?,?,?,?)",
                         (slug, slug, "/nowhere", "mips", json.dumps(config))).lastrowid
        db.execute("INSERT INTO context_versions(project_id, version, header, created_at) VALUES(?,1,'',0)", (pid,))
        for i in range(functions):
            db.execute("INSERT INTO functions(project_id, name, size, n_insns, vram, target, relocs) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, f"func_{i}", 16, 4, 0x100000 + 16 * i, bytes(16), "[]"))
    return net._project(slug)


def test_reads_go_on_while_a_write_waits(tmp_path):
    """16 agents on two games: every GUI request queued behind the one connection's lock. A read now runs on the
    thread's own connection and sees what was committed, while another thread holds a write open."""
    store = Store(tmp_path / "app.db")
    with store.tx() as db:
        db.execute("INSERT INTO settings(key, value) VALUES('a', '1')")
    writing, done = threading.Event(), threading.Event()

    def write():
        with store.tx() as db:
            db.execute("UPDATE settings SET value='2' WHERE key='a'")
            writing.set()
            done.wait(5)
    writer = threading.Thread(target=write)
    writer.start()
    writing.wait(5)
    seen = []
    reader = threading.Thread(target=lambda: seen.append(store.one("SELECT value FROM settings WHERE key='a'")["value"]))
    reader.start()
    reader.join(2)
    assert seen == ["1"]  # at once, and what was committed
    done.set()
    writer.join(5)
    assert store.one("SELECT value FROM settings WHERE key='a'")["value"] == "2"


def test_a_read_inside_a_transaction_sees_what_it_wrote(tmp_path):
    store = Store(tmp_path / "app.db")
    with store.tx() as db:
        db.execute("INSERT INTO settings(key, value) VALUES('b', 'new')")
        assert store.one("SELECT value FROM settings WHERE key='b'")["value"] == "new"


def test_an_in_memory_database_reads_what_it_wrote():
    store = Store(":memory:")
    with store.tx() as db:
        db.execute("INSERT INTO settings(key, value) VALUES('c', 'x')")
    assert [r["value"] for r in store.all("SELECT value FROM settings WHERE key='c'")] == ["x"]


def test_the_log_starts_empty_and_stays_small(tmp_path):
    """The owner's write-ahead log was 90 MB beside a 172 MB database: it never shrank. Opening the database moves
    what's in it into the database, and the log is cut back after checkpoints."""
    path = tmp_path / "app.db"
    Store(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA wal_autocheckpoint=0")  # what the last run left
    raw.executemany("INSERT INTO settings(key, value) VALUES(?, ?)", [(str(i), "x" * 1000) for i in range(500)])
    raw.commit()
    wal = tmp_path / "app.db-wal"
    assert wal.stat().st_size > 100_000
    store = Store(path)
    assert wal.stat().st_size == 0
    assert store.conn.execute("PRAGMA journal_size_limit").fetchone()[0] == WAL_SIZE_LIMIT_BYTES
    assert store.one("SELECT COUNT(*) AS n FROM settings WHERE value LIKE 'x%'")["n"] == 500
    raw.close()


def test_the_live_feed_takes_the_writers_lock_only_when_a_claim_ran_out(tmp_path, monkeypatch):
    """The GUI asks for the live feed every second; each answer was a write transaction (claims that ran out),
    queued behind every agent's writes."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    proj = tiny_game(net)
    agent, _ = net.local_agent("helper")
    fid = net.store.one("SELECT id FROM functions WHERE project_id=? LIMIT 1", proj["id"])["id"]
    expired = []
    real = net._expire_claims
    monkeypatch.setattr(net, "_expire_claims", lambda db: expired.append(1) or real(db))
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fid, agent.id, time.time(), time.time() + 600))
    assert len(net.live("game")["claims"]) == 1 and not expired
    with net.store.tx() as db:
        db.execute("UPDATE claims SET expires_at=?", (time.time() - 1,))
    assert net.live("game")["claims"] == [] and expired == [1]
