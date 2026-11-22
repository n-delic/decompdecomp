"""tools/helper_metrics.py: the before/after measure of the app's matching helpers, read from a data home."""
import datetime
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("helper_metrics_tool", ROOT / "tools" / "helper_metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def _at(day, hour):
    return datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(hour)).timestamp()


def _home(tmp_path):
    base = tmp_path / ".decomp"
    (base / "project-databases").mkdir(parents=True)
    with sqlite3.connect(base / "decomp.db") as db:
        db.execute("CREATE TABLE agents(id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO agents VALUES(?,?)", [(1, "codex-a-1"), (2, "autopilot"), (3, "polish-1")])
    with sqlite3.connect(base / "project-databases" / "1.sqlite3") as db:
        db.execute("CREATE TABLE helper_sessions(id INTEGER PRIMARY KEY, project TEXT, agent TEXT, started_at REAL, "
                   "ended_at REAL, matches INTEGER, compiles INTEGER, usd REAL, task TEXT)")
        db.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, function_id INTEGER, agent_id INTEGER, score INTEGER, "
                   "score_before INTEGER, created_at REAL)")
        db.executemany("INSERT INTO helper_sessions(project,agent,started_at,ended_at,matches,compiles,usd,task) "
                       "VALUES(?,?,?,?,?,?,?,?)", [
                           ("game", "codex-a-1", _at("2026-11-20", 1), _at("2026-11-20", 3), 4, 40, 2.0, ""),
                           ("game", "polish-1", _at("2026-11-20", 1), _at("2026-11-20", 9), 9, 9, 0, "readable"),
                           ("game", "codex-a-1", _at("2026-11-23", 1), _at("2026-11-23", 2), 3, 9, 0, "")])
        db.executemany("INSERT INTO attempts(function_id,agent_id,score,score_before,created_at) VALUES(?,?,?,?,?)", [
            (10, 1, 12, 20, _at("2026-11-20", 2)), (10, 1, 0, 12, _at("2026-11-20", 2)),  # a near-match closed
            (11, 1, 30, 40, _at("2026-11-20", 2)),                                        # one left open
            (12, 2, 0, 10, _at("2026-11-20", 2)),                                         # autopilot: not a helper
            (13, 1, 900, 950, _at("2026-11-23", 1))])
    return tmp_path


def test_per_day_and_before_after(tmp_path):
    rows = {r["day"]: r for r in metrics.report(_home(tmp_path), "2026-11-19", "2026-11-24", split="2026-11-22")}
    day = rows["2026-11-20"]
    assert day["helper_hours"] == 2.0 and day["matches"] == 4 and day["matches_per_helper_hour"] == 2.0
    assert day["compiles_per_match"] == 10.0 and day["usd_per_match"] == 0.5
    assert (day["near_matches"], day["near_closed"], day["near_close_rate"]) == (2, 1, 0.5)
    assert rows["before 2026-11-22"]["matches"] == 4 and rows["from 2026-11-22"]["matches"] == 3
    assert rows["from 2026-11-22"]["near_close_rate"] is None
    assert "matches_per_helper_hour" in metrics.render(list(rows.values()))


def test_opens_every_database_read_only(tmp_path):
    home = _home(tmp_path)
    main, projects = metrics.databases(home)
    with metrics._ro(main) as db:
        try:
            db.execute("CREATE TABLE x(a)")
            raise AssertionError("wrote to a read-only database")
        except sqlite3.OperationalError:
            pass
    assert [p.name for p in projects] == ["1.sqlite3"]
