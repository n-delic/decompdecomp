"""The Globals tab: a game's data symbols (variables, tables, strings), the way the Functions tab lists its code.

A ROM project's `symbols` table holds every name splat found or an agent gave: functions and data. The tab shows the
data ones, with the address-style name each started as (`D_800A4DFC`) beside the name it has now (`sDirectKey`), so
someone reading a function can look a variable up by either name. Project mode (a fork, a PC/GC/DS project) keeps its
data in the project's own files, not in this table: it answers `supported: false` with the reason, and ROADMAP.md
lists it.
"""
from __future__ import annotations

import json

KEYS = ["name", "address", "size", "kind", "preview", "original", "type", "type_source"]


def globals_list(net, slug: str) -> dict:
    if net.fork_slug(slug):
        return {"supported": False, "keys": KEYS, "rows": [],
                "reason": "Globals are listed for ROM projects. This project keeps its data in its own source and "
                          "header files: search them from the function view."}
    proj = net._project(slug)
    rows = net.project_store(proj["id"]).all(
        "SELECT s.name, s.address, s.size, s.kind, s.preview, s.c_type, s.type_source FROM symbols s WHERE s.project_id=? AND s.kind<>'func' "
        "AND NOT EXISTS (SELECT 1 FROM functions f WHERE f.project_id=s.project_id AND f.name=s.name) "
        "ORDER BY s.address, s.name", proj["id"])
    out = [[r["name"], r["address"], r["size"], r["kind"], r["preview"] or "", f"D_{r['address']:08X}", r["c_type"] or "", r["type_source"] or ""] for r in rows]
    return {"supported": True, "keys": KEYS, "rows": out,
            "named": sum(1 for r in out if r[0] != r[5])}


import re

from ..network import NetworkError

SOURCES = ("agent", "emulator", "user")
# A C type as it would be written in a declaration: qualifiers, a name, pointers, array sizes. No statements, no braces.
C_TYPE = re.compile(r"^(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*[A-Za-z_]\w*(?:\s*\*+)*(?:\s*\[\s*\w*\s*\])*$")


def set_type(net, slug: str, name: str, c_type: str, source: str = "agent") -> str:
    """Record the C type of a data symbol (`name`: its current name, its D_ name or a 0x address). An empty type
    clears it. Who said so is kept (`source`), so an agent's guess can be told from a type an emulator run saw."""
    c_type = " ".join(c_type.split())
    if source not in SOURCES:
        raise NetworkError(f"source must be one of {', '.join(SOURCES)}")
    if c_type and (len(c_type) > 80 or not C_TYPE.match(c_type)):
        raise NetworkError(f"`{c_type}` is not a C type: write it as in a declaration, like `s32`, `f32`, `u8[16]`, `struct Actor *`")
    if net.fork_slug(slug):
        raise NetworkError("Global types are recorded for ROM projects; this project's headers hold its types: use edit_file")
    proj = net._project(slug)
    store = net.project_store(proj["id"])
    name = name.strip()
    row = None
    if re.fullmatch(r"(?:0[xX])?[0-9A-Fa-f]{8}", name):
        address = int(name, 16)
        row = store.one("SELECT name, address FROM symbols WHERE project_id=? AND address=? AND kind<>'func' ORDER BY name LIMIT 1",
                        proj["id"], address)
    if row is None:
        row = store.one("SELECT name, address FROM symbols WHERE project_id=? AND name=? AND kind<>'func'", proj["id"], name)
    if row is None and (m := re.fullmatch(r"D_([0-9A-Fa-f]{8})", name)):
        row = store.one("SELECT name, address FROM symbols WHERE project_id=? AND address=? AND kind<>'func' ORDER BY name LIMIT 1",
                        proj["id"], int(m.group(1), 16))
    if row is None:
        raise NetworkError(f"`{name}` is not a data symbol of this game: use a name or address from the Globals tab")
    with store.tx() as db:
        db.execute("UPDATE symbols SET c_type=?, type_source=? WHERE project_id=? AND address=? AND kind<>'func'",
                   (c_type or None, source if c_type else None, proj["id"], row["address"]))
    return (f"✅ {row['name']} (0x{row['address']:08X}) is now typed `{c_type}`" if c_type
            else f"✅ cleared the type of {row['name']}")


def global_users(net, slug: str, name: str) -> dict:
    """The functions that reference one data symbol: their relocations name it (by its current or its D_ name), the
    way the function view's assembly does. Read from the stored relocations, so it needs no compile."""
    if net.fork_slug(slug):
        raise NetworkError("Global users are listed for ROM projects; search this project's source files instead")
    proj = net._project(slug)
    store = net.project_store(proj["id"])
    sym = store.one("SELECT name, address FROM symbols WHERE project_id=? AND name=? AND kind<>'func'", proj["id"], name)
    if sym is None:
        raise NetworkError(f"`{name}` is not a data symbol of this game")
    names = {sym["name"], f"D_{sym['address']:08X}"}
    users = []
    for f in store.all("SELECT name, vram, status, relocs FROM functions WHERE project_id=?", proj["id"]):
        try:
            relocs = json.loads(f["relocs"] or "[]")
        except ValueError:
            continue
        count = sum(1 for r in relocs if len(r) > 2 and r[2] in names)
        if count:
            users.append({"name": f["name"], "address": f["vram"], "status": f["status"], "references": count})
    users.sort(key=lambda u: (u["address"] or 0, u["name"]))
    return {"name": sym["name"], "address": sym["address"], "users": users}
