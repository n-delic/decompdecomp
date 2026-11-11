"""Calls to a function by a name it no longer has, put right (docs/PORTING.md, phase 3).

When polishing renames a function, a caller can keep calling it by its old name: an automatic `func_80012345`, or
an earlier chosen name the symbol table still maps to the same address. The console build links by address and
never notices; a PC link refuses it. The link check (port/pc_link.py) knows each old name and the function's name
now, so the callers are rewritten here, whole words only, in the functions that use the old name and nowhere else,
and each rewritten function is judged twice like a port fix: its console build must still be the game's exact
bytes, and the PC compiler must find it no worse. A function that fails either keeps its C, and is listed.
"""

from __future__ import annotations

import re
import time

from ..network import NetworkError
from . import pc_compile, pc_link, port_fix


def plan(net, proj) -> tuple[dict[str, str], dict[str, set[str]]]:
    """({old name: name now}, {function: the old names it uses}) from the last Compile for PC."""
    found = pc_link.undefined(net, proj)
    if found is None:
        raise NetworkError("Compile the game's C for a PC first (Port readiness → Compile for PC): the link check "
                           "it records says which calls use an old name.")
    groups, facts = found
    names = {old: pc_link.current_name(net, proj, old) for old in groups.get("renamed", [])}
    users: dict[str, set[str]] = {}
    for old in names:
        for function in facts["needed"].get(old, ()):
            users.setdefault(function, set()).add(old)
    return names, users


def fix(net, slug: str, log=print, *, cc: list[str] | None = None) -> dict:
    """Rewrite every call by an old name, judged: {"names", "functions", "kept", "refused": [{"function", "why"}]}."""
    with net.progress.view():
        proj = net.project_row(slug)
        if not net.is_rom(proj):
            raise NetworkError("Calls by old names are put right in projects decompdecomp split from a ROM.")
        names, users = plan(net, proj)
        store = net.project_store(proj["id"])
        rows = {r["name"]: r for r in store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='matched' AND gone_mark IS NULL", proj["id"])}
        units = net._unit_contexts(proj["id"])
    if not names:
        log("No call uses an old name.")
        return {"names": 0, "functions": 0, "kept": [], "refused": []}
    log(f"{len(names)} old names, called from {len(users)} functions: rewriting them and judging each function twice")
    rewritten, refused = {}, []
    for function, olds in sorted(users.items()):
        row = rows.get(function)
        if row is None:  # library code the kit replaces, or no longer matched: nothing of ours to rewrite
            continue
        source = row["matched_source"]
        for old in sorted(olds):
            source = re.sub(rf"\b{re.escape(old)}\b", names[old], source)
        if source != row["matched_source"]:
            rewritten[row["id"]] = source

    def header(fn) -> str:
        context = net._context(proj["id"], net._effective_unit(fn, units))
        return context["header"] if context is not None else ""

    judged = [r for r in rows.values() if r["id"] in rewritten]
    broken = dict(net.regression_test(proj, None, sources=rewritten, only=judged, header_for=header))
    refused += [{"function": name, "why": f"the console build {why}"} for name, why in sorted(broken.items())]
    cc = cc or pc_compile.compiler(log)
    kept = []
    for fn in judged:
        if fn["name"] in broken:
            continue
        with net.progress.view():
            before = pc_compile.function(net, proj, fn["name"]) or []
        linked: dict = {}
        found = pc_compile.compile_one(header(fn), rewritten[fn["id"]], cc=cc, linked=linked)
        if port_fix._tally(found)[0] > port_fix._tally(before)[0]:
            refused.append({"function": fn["name"], "why": "the PC compiler finds more errors (a declaration under "
                                                            "the new name conflicts with another)"})
            continue
        symbols = linked.get("symbols")
        if symbols is None:  # no object to read: what it needed, under the names it calls now
            with net.progress.view():
                row = pc_compile._store(net, proj).one(
                    "SELECT symbols FROM port_compile WHERE project_id=? AND name=?", proj["id"], fn["name"])
            if row is not None and row["symbols"]:
                import json
                last = json.loads(row["symbols"])
                symbols = {"defines": last["defines"],
                           "needs": sorted({names.get(n, n) for n in last["needs"]} - set(last["defines"]))}
        kept.append((fn, found, symbols))
    done = _keep(net, proj, kept, rewritten)
    log(f"{len(done)} functions now call {len(names)} functions by their names"
        + (f"; {len(refused)} kept their C: " + "; ".join(f"{r['function']} ({r['why']})" for r in refused[:8])
           if refused else ""))
    return {"names": len(names), "functions": len(users), "kept": done, "refused": refused}


def _keep(net, proj, kept, rewritten) -> list[str]:
    """Write the rewritten C of the functions that passed, if nobody changed them meanwhile; saves and commits follow
    it as they follow any matched C."""
    done = []
    store = net.project_store(proj["id"])
    with net.progress._project_lock(proj["slug"]), store.tx() as db:
        net.progress.check_result()
        for fn, _, _ in kept:
            latest = db.execute("SELECT status, matched_source FROM functions WHERE id=?", (fn["id"],)).fetchone()
            if latest is None or latest["status"] != "matched" or latest["matched_source"] != fn["matched_source"]:
                continue  # changed while it was judged: the next run takes it
            db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?",
                       (rewritten[fn["id"]], rewritten[fn["id"]], fn["id"]))
            done.append(fn["name"])
        if done:
            net._event(db, proj["id"], None, "port_rename_calls", f"{len(done)} functions call by current names")
    now = time.time()
    with pc_compile._store(net, proj).tx() as db:
        for fn, found, symbols in kept:
            if fn["name"] in done:
                pc_compile.record(db, proj["id"], fn["name"], found, now, symbols)
    return sorted(done)
