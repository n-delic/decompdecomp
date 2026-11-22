"""The Globals tab: a game's data symbols (variables, tables, strings), the way the Functions tab lists its code.

A ROM project's `symbols` table holds every name splat found or an agent gave: functions and data. The tab shows the
data ones, with the address-style name each started as (`D_800A4DFC`) beside the name it has now (`sDirectKey`), so
someone reading a function can look a variable up by either name. Project mode (a fork, a PC/GC/DS project) keeps its
data in the project's own files, not in this table: it answers `supported: false` with the reason, and ROADMAP.md
lists it.
"""
from __future__ import annotations

KEYS = ["name", "address", "size", "kind", "preview", "original"]


def globals_list(net, slug: str) -> dict:
    if net.fork_slug(slug):
        return {"supported": False, "keys": KEYS, "rows": [],
                "reason": "Globals are listed for ROM projects. This project keeps its data in its own source and "
                          "header files: search them from the function view."}
    proj = net._project(slug)
    rows = net.project_store(proj["id"]).all(
        "SELECT s.name, s.address, s.size, s.kind, s.preview FROM symbols s WHERE s.project_id=? AND s.kind<>'func' "
        "AND NOT EXISTS (SELECT 1 FROM functions f WHERE f.project_id=s.project_id AND f.name=s.name) "
        "ORDER BY s.address, s.name", proj["id"])
    out = [[r["name"], r["address"], r["size"], r["kind"], r["preview"] or "", f"D_{r['address']:08X}"] for r in rows]
    return {"supported": True, "keys": KEYS, "rows": out,
            "named": sum(1 for r in out if r[0] != r[5])}
