"""A matched function's C fixed for the PC, judged twice (docs/PORTING.md, phase 4).

A port keeps one copy of the game's C, so a fix for the PC must not cost the console build: the fixed C is
compiled by the game's own compiler and must still be the game's bytes (the function judge, as polishing judges a
rewrite), and it's compiled for a 64-bit PC as the PC check compiles it (port/pc_compile.py, PORT defined) and must be no
worse there on either count (errors, pointer-size warnings) and better on one: a big function can be fixed a group
of lines at a time, and stays in the queue until nothing is left. Only then is
it the function's C. What the console's compiler can't take goes behind `#ifdef PORT`: the console build doesn't
define it, so the matching C stays as it was there.

The PC side judges the one function with the header as it is now: a declaration an agent adds to the header for
it is judged with it, and the next full check sees what that does to the rest. When the header alone fixed it (the
C submitted is the C it has), only the PC result is recorded: the header's edit was judged by edit_context.
"""

from __future__ import annotations

from .. import scoring
from ..network import NetworkError
from . import pc_compile

POINTER = "pointer size"


def _tally(found: list[dict]) -> tuple[int, int]:
    """(errors, pointer-size warnings)."""
    return (sum(d["level"] == "error" for d in found),
            sum(d["level"] == "warning" and d["kind"] == POINTER for d in found))


def render(found: list[dict]) -> str:
    """Diagnostics as lines an agent reads: `line 12: [undeclared] call to undeclared function 'osFoo'`."""
    if not found:
        return "(none)"
    return "\n".join(f"- {d['file'] + ' ' if d['file'] else ''}line {d['line']}: [{d['kind']}] {d['level']}: "
                     f"{d['message']}" for d in found)


def _function(net, slug: str, name: str):
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("Fixes for the PC are judged on projects decompdecomp split from a ROM; a project-mode "
                           "game's files are fixed in the project itself.")
    fn = net._function(name, slug)
    if fn["status"] != "matched" or not fn["matched_source"]:
        raise NetworkError(f"{fn['name']} isn't matched: only matched C is fixed for the PC.")
    return proj, fn


def _header(net, proj, fn) -> str:
    context = net._context(proj["id"], fn["unit"])
    return context["header"] if context is not None else ""


def check(net, slug: str, name: str, source: str, *, cc: list[str] | None = None) -> str:
    """What the PC compiler says of `source` for the function, judged by nothing and saved nowhere."""
    with net.progress.view():
        proj, fn = _function(net, slug, name)
        header = _header(net, proj, fn)
    found = pc_compile.compile_one(header, source, cc=cc)
    errors, sizes = _tally(found)
    return (f"For a 64-bit PC: {errors} errors, {sizes} pointer-size warnings (nothing saved; fix_for_pc judges it "
            f"on the console too).\n{render(found)}")


def fix(net, agent, slug: str, name: str, source: str, *, cc: list[str] | None = None) -> dict:
    """Judge a fix both ways and keep it when it passes: {"kept", "text", "found"}."""
    with net.progress.judging(slug):
        proj, fn = _function(net, slug, name)
        before = pc_compile.function(net, proj, fn["name"])
        if before is None:
            raise NetworkError(f"{fn['name']} hasn't been compiled for a PC yet: run Compile for PC (Port readiness) "
                               "first.")
        same = " ".join(source.split()) == " ".join(fn["matched_source"].split())
        header = _header(net, proj, fn)
        found = pc_compile.compile_one(header, source, cc=cc)
        (errors, sizes), (had_errors, had_sizes) = _tally(found), _tally(before)
        if errors > had_errors or sizes > had_sizes or (errors, sizes) == (had_errors, had_sizes):
            why = ("it has more PC errors than before" if errors > had_errors else
                   "it casts more pointers to 32-bit integers than before" if sizes > had_sizes else
                   "the PC compiler says the same of it as before")
            if same and (errors, sizes) == (had_errors, had_sizes):
                raise NetworkError("That's the C it has already, and the PC compiler says the same of it as before.")
            return {"kept": False, "found": found,
                    "text": f"❌ Not kept: {why} ({errors} errors, {sizes} pointer-size warnings; it had {had_errors} "
                            f"and {had_sizes}).\n{render(found)}"}
        if same:  # the header changed under it (edit_context, judged there): only the PC's verdict is new
            _refresh(net, proj, fn, agent, found)
            return {"kept": True, "found": found, "text": _kept(fn["name"], errors, sizes, had_errors, had_sizes,
                                                                "its C is unchanged; the header's change fixed it")}
        context = net._context(proj["id"], fn["unit"])
        compiled = net._compile(proj, header, source, fn)
        version = context["version"] if context is not None else 0
        if not compiled.ok:
            net._record(fn, agent, None, source, version)
            return {"kept": False, "found": found,
                    "text": "❌ Not kept: the console's compiler refuses it. Put what only the PC needs behind "
                            f"#ifdef PORT (the console build doesn't define PORT).\n{compiled.log.strip()[-1500:]}"}
        candidate, verdict, problems = net._evaluate(fn, proj, compiled.obj)
        if candidate is None:
            net._record(fn, agent, None, source, version, compiled=True)
            return {"kept": False, "found": found, "text": f"❌ Not kept: no function named `{fn['name']}` in it."}
        diff = scoring.diff(net._target_obj(fn, proj), candidate)
        net._record(fn, agent, diff.score, source, version)
        if not (diff.bytes_match if verdict is None else verdict):
            note = "".join(f"\n- {p}" for p in problems)
            return {"kept": False, "found": found,
                    "text": f"❌ Not kept: the console build no longer matches (score {diff.score}). Keep the "
                            "console's C as it was outside #ifdef PORT, or change only types the compiler treats "
                            f"the same.\n{diff.render()}{note}"}
        _keep(net, proj, fn, agent, source, found)
    return {"kept": True, "found": found, "text": _kept(fn["name"], errors, sizes, had_errors, had_sizes,
                                                        "it still matches byte for byte on the console")}


def _kept(name: str, errors: int, sizes: int, had_errors: int, had_sizes: int, console: str) -> str:
    left = ("builds for a PC with no pointer-size warnings" if not errors and not sizes else
            f"builds for a PC ({sizes} pointer-size warnings left)" if not errors else
            f"is closer to building for a PC ({errors} errors and {sizes} pointer-size warnings left; it stays in "
            "the queue for the rest)")
    return f"✅ {name} {left}; it had {had_errors} errors and {had_sizes} warnings. Kept: {console}."


def _refresh(net, proj, fn, agent, found: list[dict]) -> None:
    """A fix the header made: the function's PC result is new, its C isn't."""
    store = net.project_store(proj["id"])
    with store.tx() as db:
        net._event(db, proj["id"], agent.id, "port_fix", fn["name"])
    with pc_compile._store(net, proj).tx() as db:
        pc_compile.record(db, proj["id"], fn["name"], found)
        db.execute("UPDATE port_compile SET agent_id=NULL, lease=NULL WHERE project_id=? AND name=?",
                   (proj["id"], fn["name"]))


def _keep(net, proj, fn, agent, source: str, found: list[dict]) -> None:
    """The fixed C is the function's C now, recorded as a verified match (saves and commits follow it)."""
    from ..app import match_commits
    from ..match_bundle import capture
    store = net.project_store(proj["id"])
    with net.progress._project_lock(proj["slug"]), store.tx() as db:
        net.progress.check_result()
        latest = db.execute("SELECT name, status, matched_source FROM functions WHERE id=?", (fn["id"],)).fetchone()
        if latest is None or latest["status"] != "matched" or latest["matched_source"] != fn["matched_source"]:
            raise NetworkError("The function's C changed while it was judged. Read it again and retry.")
        db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (source, source, fn["id"]))
        net._event(db, proj["id"], agent.id, "port_fix", fn["name"])
        attempt = db.execute("SELECT id, context_version FROM attempts WHERE function_id=? AND agent_id=? AND source=? "
                             "ORDER BY id DESC LIMIT 1", (fn["id"], agent.id, source)).fetchone()
        if attempt is not None:
            capture(db, fn, attempt["id"], source, attempt["context_version"], None)
            store.after_commit(lambda: match_commits.enqueue(net, net.project_row(proj["slug"]), fn["id"],
                                                             polished=True))
    with pc_compile._store(net, proj).tx() as db:
        pc_compile.record(db, proj["id"], fn["name"], found)
        db.execute("UPDATE port_compile SET agent_id=NULL, lease=NULL WHERE project_id=? AND name=?",
                   (proj["id"], fn["name"]))
