"""What keeps the app quick with big games and many agents: reads beside writes, settings parsed once, polls that
don't take the writer's lock. Measured on a copy of the owner's database (GTA San Andreas, 12,627 functions, and three
more games); these tests need no compiler and no game."""

import json
import sqlite3
import threading
import time

from decompnet.app.engine import RomNetwork
from decompnet.app.engine import core as engine_core
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


def test_settings_are_parsed_once_per_version(tmp_path, monkeypatch):
    """GTA San Andreas's settings are 193 KB, and one request parsed them 4 to 12 times (a range check, 462)."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    proj = tiny_game(net)
    parsed = []
    real = json.loads
    monkeypatch.setattr(engine_core.json, "loads", lambda text, *a, **k: parsed.append(1) or real(text, *a, **k))
    first = net.cfg(proj)
    assert net.cfg(net._project("game")) is first and len(parsed) <= 1
    net._update_cfg("game", lambda cfg: cfg.__setitem__("instructions", "be brief"))
    assert net.cfg(net._project("game"))["instructions"] == "be brief" and "instructions" not in first


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


def test_one_compiler_map_build_at_a_time_and_the_last_one_answers_meanwhile(tmp_path, monkeypatch):
    """Each match made GTA San Andreas's compiler map stale, and every brief and map request waiting rebuilt it at
    once (600-870 ms each). One build runs per project; meanwhile the last map answers at once."""
    from decompnet import compilermap

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    proj = tiny_game(net)
    builds, release = [], threading.Event()
    real = compilermap.build

    def slow_build(functions, big_endian):
        builds.append(1)
        if len(builds) > 1:
            release.wait(5)
        return real(functions, big_endian)
    monkeypatch.setattr(compilermap, "build", slow_build)
    first = net.compiler_map(proj)
    with net.store.tx() as db:  # a match
        db.execute("UPDATE functions SET status='matched', matched_at=? WHERE project_id=? AND name='func_0'",
                   (time.time(), proj["id"]))
    answers = []
    askers = [threading.Thread(target=lambda: answers.append(net.compiler_map(proj))) for _ in range(6)]
    for t in askers:
        t.start()
    for t in askers:
        t.join(2)
    assert len(answers) == 6 and all(a is first for a in answers)  # at once, while the new one builds
    release.set()
    deadline = time.time() + 5
    while net.compiler_map(proj) is first and time.time() < deadline:
        time.sleep(0.02)
    assert net.compiler_map(proj) is not first and len(builds) == 2
    assert net.compiler_map(proj, fresh=True) is net.compiler_map(proj)


def test_the_rom_map_comes_without_the_compiler_map(tmp_path, monkeypatch):
    """The map is drawn at once; the compiler map's colours come in a later request (GTA San Andreas: the map waited
    710-800 ms for the compiler map after any match)."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    tiny_game(net)
    monkeypatch.setattr(net, "compiler_map", lambda *a, **k: (_ for _ in ()).throw(AssertionError("built for the map")))
    rows = net.function_map("game")
    assert len(rows) == 4 and not any("cc" in r for r in rows)
    monkeypatch.undo()
    columns = net.map_columns("game")
    assert [c[0] for c in columns] == [r["vram"] for r in rows] and all(c[1].endswith("-G8") and not c[2] for c in columns)


def test_what_briefs_read_from_the_whole_game_is_built_once_per_layout(tmp_path, monkeypatch):
    """GTA San Andreas's first claim brief took 5.2 s: C++ classes 3 s, look-alikes 1.2 s, twins 0.7 s, built when
    the brief asked. They're built when the project opens, once (briefs asked meanwhile wait for that one build), and
    again only when a split or a cut changes the functions."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    proj = tiny_game(net)
    built, prints = [], []

    def slow_classes(p):
        time.sleep(0.2)
        built.append("classes")
    monkeypatch.setattr(net, "classes", slow_classes)
    real_prints = net.twins._rom_prints
    monkeypatch.setattr(net.twins, "_rom_prints", lambda p: prints.append(1) or real_prints(p))
    askers = [threading.Thread(target=net.warm_briefs, args=(proj,)) for _ in range(3)]
    for t in askers:
        t.start()
    for t in askers:
        t.join(5)
    net.warm_briefs(proj)
    net.twins.prints(proj)
    assert built == ["classes"] and prints == [1]
    with net.store.tx() as db:  # a split: one more function
        db.execute("INSERT INTO functions(project_id, name, size, n_insns, vram, target, relocs) "
                   "VALUES(?,'func_9',8,2,?,?,'[]')", (proj["id"], 0x100100, bytes(8)))
    net.warm_briefs(proj)
    assert built == ["classes", "classes"] and prints == [1, 1]
