"""The function list dates describe attempt history, including failed checks."""

import json
import sqlite3

from decompnet.app.engine import RomNetwork
from decompnet.store import SCHEMA


def test_function_map_reports_last_check_and_last_real_improvement(tmp_path):
    # Start with the original schema: opening the project backfills existing attempts once.
    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    with db:
        pid = db.execute(
            "INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
            ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"})),
        ).lastrowid
        ids = {}
        for name in ("improved", "never_improved", "untouched"):
            ids[name] = db.execute(
                "INSERT INTO functions(project_id,name,size,n_insns,status) VALUES(?,?,?,?,?)",
                (pid, name, 40, 10, "open"),
            ).lastrowid
        for name, score, compiled, at in (
            ("improved", 900, 1, 10), ("improved", 900, 1, 20),
            ("improved", 700, 1, 30), ("improved", None, 0, 40),
            ("never_improved", None, 0, 50),
        ):
            db.execute(
                "INSERT INTO attempts(function_id,score,compiled,created_at) VALUES(?,?,?,?)",
                (ids[name], score, compiled, at),
            )
    db.close()
    net = RomNetwork(path, tmp_path)
    rows = {row["name"]: row for row in net.function_map("game")}
    assert (rows["improved"]["last_improved"], rows["improved"]["last_checked"]) == (30, 40)
    assert (rows["never_improved"]["last_improved"], rows["never_improved"]["last_checked"]) == (None, 50)
    assert (rows["untouched"]["last_improved"], rows["untouched"]["last_checked"]) == (None, None)


def test_new_attempts_update_cached_dates_without_counting_ties_or_ceiling(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("game", "Game", str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,n_insns,status) VALUES(?,?,?,?)",
                         (pid, "f", 10, "open")).lastrowid
    agent, _ = net.local_agent("test-recency")
    clock = iter((10, 20, 30, 40, 50))
    monkeypatch.setattr("decompnet.network.time.time", lambda: next(clock))
    fn = net.store.one("SELECT * FROM functions WHERE id=?", fid)
    for score in (1000, 900, 900, None, 700):
        net._record(fn, agent, score, "candidate", 1)
        row = net.store.one("SELECT last_improved FROM functions WHERE id=?", fid)
        assert row["last_improved"] == {1000: None, 900: 20, None: 20, 700: 50}[score]
    row = net.store.one("SELECT last_checked,last_improved FROM functions WHERE id=?", fid)
    assert (row["last_checked"], row["last_improved"]) == (50, 50)


def test_remote_best_improves_date_without_inventing_a_local_check(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("game", "Game", str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status) VALUES(?,?,?,?,?)",
                         (pid, "f", 0x80000000, 10, "open")).lastrowid
    monkeypatch.setattr("decompnet.app.engine.rooms.time.time", lambda: 42)
    net.apply_remote_best("game", 0x80000000, 1000, "ceiling")
    assert net.store.one("SELECT last_improved FROM functions WHERE id=?", fid)["last_improved"] is None
    net.apply_remote_best("game", 0x80000000, 900, "remote candidate")
    row = net.store.one("SELECT last_checked,last_improved FROM functions WHERE id=?", fid)
    assert (row["last_checked"], row["last_improved"]) == (None, 42)
    net.apply_remote_best("game", 0x80000000, 900, "tie")
    assert net.store.one("SELECT last_improved FROM functions WHERE id=?", fid)["last_improved"] == 42
