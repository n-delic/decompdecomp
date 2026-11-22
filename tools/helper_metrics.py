#!/usr/bin/env python3
"""How well the app's own matching helpers do, per day, from a data home's databases (read only).

Measures the effect of a change to what helpers know or can call (guides, tools, the hand-off clock): run it for the
days before the change as the baseline and for the days after, with the same home. Every database is opened with
`?mode=ro`, so it is safe beside a running app.

    helper_metrics.py [--home DIR] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--split YYYY-MM-DD] [--project SLUG] [--json]

Per day (local time), for helper sessions of matching tasks (polishing sessions left out):
- helper-hours: the sessions' wall time; matches: the matches they recorded; matches per helper-hour;
- compiles per match: the sessions' compiles over their matches (the cost of a match);
- near-match close rate: of the functions a helper compiled while the function's best was 1-50 (a near-match), the
  share a helper took to 0 within the same range (from the attempts table, by the helpers' agent names);
- USD per match where the agents report spend.
`--split DATE` adds two summary rows, before DATE and from DATE on: the baseline and the after.
"""
import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

NEAR_MATCH = 50  # network.NEAR_MATCH_SCORE


def _ro(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def databases(home):
    """The main database and each project database of a data home (project-databases/<id>.sqlite3)."""
    base = Path(home).expanduser() / ".decomp"
    main = base / "decomp.db"
    projects = sorted(p for p in (base / "project-databases").glob("*.sqlite3")
                      if p.name.count(".") == 1) if (base / "project-databases").is_dir() else []
    return main, projects


def _day(at):
    return datetime.datetime.fromtimestamp(at).date().isoformat()


def _stamp(day, end=False):
    d = datetime.date.fromisoformat(day) + datetime.timedelta(days=1 if end else 0)
    return datetime.datetime.combine(d, datetime.time()).timestamp()


def collect(home, start, end, project=None):
    """{day: totals} for the helper sessions and attempts of [start, end] (dates, inclusive)."""
    lo, hi = _stamp(start), _stamp(end, end=True)
    main, projects = databases(home)
    names = {}
    if main.exists():
        with _ro(main) as db:
            names = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM agents")}
    days = {}

    def day(at):
        return days.setdefault(_day(at), {"sessions": 0, "seconds": 0.0, "matches": 0, "compiles": 0, "usd": 0.0,
                                          "near": set(), "closed": set()})
    for path in ([main] if main.exists() else []) + projects:
        with _ro(path) as db:
            if not _has(db, "helper_sessions"):
                continue
            where = "started_at>=? AND started_at<? AND task!='readable'" + (" AND project=?" if project else "")
            args = (lo, hi) + ((project,) if project else ())
            helpers = set()
            for r in db.execute(f"SELECT * FROM helper_sessions WHERE {where}", args):
                d = day(r["started_at"])
                d["sessions"] += 1
                d["seconds"] += max(0.0, (r["ended_at"] or r["started_at"]) - r["started_at"])
                d["matches"] += r["matches"] or 0
                d["compiles"] += r["compiles"] or 0
                d["usd"] += r["usd"] or 0
                helpers.add(r["agent"])
            ids = sorted(names[n] for n in helpers if n in names)
            if not ids or not _has(db, "attempts"):
                continue
            marks = ",".join("?" * len(ids))
            for r in db.execute(f"SELECT function_id,score,score_before,created_at FROM attempts WHERE created_at>=? AND "
                                f"created_at<? AND agent_id IN ({marks})", (lo, hi, *ids)):
                key = (path.name, r["function_id"])
                if r["score_before"] is not None and 0 < r["score_before"] <= NEAR_MATCH:
                    day(r["created_at"])["near"].add(key)
                if r["score"] == 0:
                    day(r["created_at"])["closed"].add(key)
    return days


def summarise(rows):
    """One line of metrics from a list of day totals."""
    seconds = sum(r["seconds"] for r in rows)
    matches = sum(r["matches"] for r in rows)
    compiles = sum(r["compiles"] for r in rows)
    usd = sum(r["usd"] for r in rows)
    near = set().union(*(r["near"] for r in rows)) if rows else set()
    closed = set().union(*(r["closed"] for r in rows)) if rows else set()
    hours = seconds / 3600
    return {"sessions": sum(r["sessions"] for r in rows), "helper_hours": round(hours, 1), "matches": matches,
            "matches_per_helper_hour": round(matches / hours, 2) if hours else None,
            "compiles_per_match": round(compiles / matches, 1) if matches else None,
            "near_matches": len(near), "near_closed": len(near & closed),
            "near_close_rate": round(len(near & closed) / len(near), 3) if near else None,
            "usd_per_match": round(usd / matches, 2) if matches and usd else None}


def report(home, start, end, split=None, project=None):
    days = collect(home, start, end, project)
    out = [dict(day=d, **summarise([days[d]])) for d in sorted(days)]
    if split:
        out.append(dict(day=f"before {split}", **summarise([v for d, v in days.items() if d < split])))
        out.append(dict(day=f"from {split}", **summarise([v for d, v in days.items() if d >= split])))
    out.append(dict(day="all", **summarise(list(days.values()))))
    return out


COLUMNS = ("day", "sessions", "helper_hours", "matches", "matches_per_helper_hour", "compiles_per_match",
           "near_matches", "near_closed", "near_close_rate", "usd_per_match")


def render(rows):
    width = {c: max(len(c), *(len(str(r[c] if r[c] is not None else "-")) for r in rows)) for c in COLUMNS}
    lines = ["  ".join(c.rjust(width[c]) for c in COLUMNS)]
    for r in rows:
        lines.append("  ".join(str(r[c] if r[c] is not None else "-").rjust(width[c]) for c in COLUMNS))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    today = datetime.date.today()
    ap.add_argument("--home", default=str(Path.home() / "Decomp-RetroReversing-dev"))
    ap.add_argument("--from", dest="start", default=(today - datetime.timedelta(days=7)).isoformat())
    ap.add_argument("--to", dest="end", default=today.isoformat())
    ap.add_argument("--split")
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = report(a.home, a.start, a.end, a.split, a.project)
    print(json.dumps(rows, indent=1) if a.json else render(rows))


if __name__ == "__main__":
    sys.exit(main())
