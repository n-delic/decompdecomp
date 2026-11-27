"""The closest attempt at an open function built with another compiler profile than its own, kept for the next worker.

Submissions are scored under the function's current profile, so on a game where many functions were built at another
-O level than the project's (-O1 in an -O2 project), a nearly exact attempt scored badly there, never became the saved
best, and was lost when the agent gave up: the next one started again from a poor best tuned to the wrong level. Every
compile under another profile (try_compiler, compiler_experiments, search_flags, a suggestion's check) now keeps the
closest one per function, with its profile, and the briefs hand it on.

It is a diagnostic only. Nothing here changes a score, the saved best, a match or any compiler setting: those still go
through the judge and the existing paths (a function-only suggestion; in project mode, try_compiler and search_flags).
"""
from __future__ import annotations

import hashlib
import time

from .. import consoles
from ..project import compiler_for

SOURCE_LIMIT = 64 * 1024


def _fork(net, proj) -> bool:
    return net.cfg(proj).get("kind") == "fork"


def _target(fn) -> str:
    """What the function was when it was compiled: a new boundary or release makes the old score meaningless."""
    return hashlib.sha256(bytes(fn["target"] or b"") + f":{fn['size']}".encode()).hexdigest()[:20]


def _exists(store) -> bool:
    return bool(store.one("SELECT 1 FROM sqlite_master WHERE name='profile_attempts'"))


def _row(store, fn):
    if not _exists(store):
        return None
    row = store.one("SELECT * FROM profile_attempts WHERE function_id=?", fn["id"])
    return row if row and row["target"] == _target(fn) else None


def keep(net, proj, fn, toolchain: str, flags, score: int | None, source: str) -> None:
    """Keep this compile when it is the closest one yet of an open function under a profile that isn't its own. In
    project mode the caller, which knows the file's compiler and flags, leaves its own profile out."""
    if score is None or fn["status"] != "open" or not source.strip() or len(source) > SOURCE_LIMIT:
        return
    flags = list(flags or [])
    if not _fork(net, proj):
        own = compiler_for(net.cfg(proj), fn["vram"], fn)
        if (toolchain, flags) == (own.get("toolchain"), list(own.get("flags") or [])):
            return
    store = net.project_store(fn["project_id"])
    with store.tx() as db:
        db.execute("CREATE TABLE IF NOT EXISTS profile_attempts("
                   "function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE,"
                   "project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,"
                   "toolchain TEXT NOT NULL,flags TEXT NOT NULL,score INTEGER NOT NULL,source TEXT NOT NULL,"
                   "target TEXT NOT NULL,created_at REAL NOT NULL)")
        old = db.execute("SELECT score,target FROM profile_attempts WHERE function_id=?", (fn["id"],)).fetchone()
        if old and old["target"] == _target(fn) and old["score"] <= score:
            return
        db.execute("INSERT OR REPLACE INTO profile_attempts VALUES(?,?,?,?,?,?,?,?)",
                   (fn["id"], fn["project_id"], toolchain, " ".join(flags), score, source, _target(fn), time.time()))


def kept(net, fn) -> dict | None:
    """The stored attempt: {score, toolchain, flags, source}."""
    row = _row(net.project_store(fn["project_id"]), fn)
    return row and {"score": row["score"], "toolchain": row["toolchain"], "flags": row["flags"].split(),
                    "source": row["source"]}


def closer(net, proj, fn) -> dict | None:
    """The kept attempt worth handing on: one that scores better than the saved best of an open function. ROM projects
    also look at compiler_experiments' durable trials, where near-misses were recorded before this."""
    if fn["status"] != "open":
        return None
    found = [kept(net, fn)]
    if not _fork(net, proj):
        from .match_brief import trials
        found += trials(net, proj, fn)[:1]
    best = min((item for item in found if item), key=lambda item: item["score"], default=None)
    if best is None or fn["best_score"] is not None and best["score"] >= fn["best_score"]:
        return None
    return best


def _file_compiler(net, proj, fn) -> str | None:
    _, fp, cfg = net.forks.get(proj["slug"])
    source = fp.unit(fn["unit"]).source
    return consoles.get(cfg.get("platform")).file_compiler(fp, source) if source else None


def _how(net, proj, fn, item) -> str:
    """The calls that check the attempt and, when it is exact, make it the match."""
    name, tool, flags = fn["name"], item["toolchain"], " ".join(item["flags"])
    if _fork(net, proj):
        if tool == _file_compiler(net, proj, fn):
            return (f'It used other flags for the file: search_flags(function="{name}", source=<this C>) checks them, '
                    "and keeps them for the file when it matches and the file's other matches still do.")
        return (f'Check it with try_compiler(function="{name}", compiler="{tool}", source=<this C>): it switches the '
                "file's compiler only when the function matches and the file's other matches still do (split_file "
                "gives the function a file of its own when they don't).")
    own = compiler_for(net.cfg(proj), fn["vram"], fn)
    if (tool, item["flags"]) == (own.get("toolchain"), list(own.get("flags") or [])):
        return "That is the function's own profile now: send it with compile_and_diff."
    return (f'Work on it with try_compiler(function="{name}", compiler="{tool}", flags="{flags}", source=<this C>); '
            "compile_and_diff judges under the function's own profile, so a worse score there is expected. When it is "
            f'exact, suggest(function="{name}", compiler="{tool}", flags="{flags}", source=<that C>, text=<why>) '
            "rechecks it and saves it for this function only.")


def brief_lines(net, proj, fn) -> list[str]:
    """The brief's section with the attempt's profile and C."""
    item = closer(net, proj, fn)
    if item is None:
        return []
    best = "none saved" if fn["best_score"] is None else f"score {fn['best_score']}"
    return ["", f"### Closer attempt under another compiler profile: score {item['score']} with "
                f"{item['toolchain']} {' '.join(item['flags'])}".rstrip(),
            f"Recorded from an earlier compile and applied to nothing (best under the function's own profile: {best}). "
            "Its score is from then. " + _how(net, proj, fn, item),
            "```c", item["source"].strip(), "```"]


def hint(net, proj, fn) -> str:
    """One line for the short views, which leave the C to the brief."""
    item = closer(net, proj, fn)
    if item is None:
        return ""
    return (f"A closer attempt (score {item['score']}) is kept under {item['toolchain']} {' '.join(item['flags'])}: "
            'get_function(section="brief") shows its C and how to use it.')


def rewrite(db, project_id: int, swap) -> None:
    """A rename reaches the kept C too, so it still compiles."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='profile_attempts'").fetchone():
        return
    for row in db.execute("SELECT function_id,source FROM profile_attempts WHERE project_id=?",
                          (project_id,)).fetchall():
        new = swap(row["source"])
        if new != row["source"]:
            db.execute("UPDATE profile_attempts SET source=? WHERE function_id=?", (new, row["function_id"]))
