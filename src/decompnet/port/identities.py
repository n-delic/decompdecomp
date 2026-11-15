"""Library functions the user confirms, for the port (docs/PORTING.md, phase 3).

The app names a library function only from a full-size signature match against a library it has (sdk_names,
sdk_identity): never from a familiar name. A game built with a library version the app has no signatures for keeps
those functions as its own: they're matched C like the rest, and a port would build them, with everything they
reach. One N64 game's copy of libultra's PI manager (`osCreatePiManager`, which libultraship provides) pulled in
the device manager, its queues and the 64DD's interrupt code, which no PC has.

A person who knows which function it is can say so here. That's recorded as what it is, a person's confirmation
(`meta.port_sdk`), and used only by the port: Compile for PC, the link check and the readiness report treat the
function as the library function, so the kit sorts it (libultraship has it, the game's math is built, the rest is
glue). It is never taken for a signature match: matching, naming and the agents' briefs don't read it. The
function is renamed to the library's name first, through the judged rename, so its callers call that name.
"""

from __future__ import annotations

import json
import time

from ..network import NetworkError


def confirm(net, agent, slug: str, function: str, name: str, note: str = "") -> str:
    """Record that `function` is the library's `name` (renaming it to that, judged), for the port."""
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("Library functions are confirmed for ports of projects decompdecomp split from a ROM.")
    kit = net.console(proj).port_kit
    if kit is None or kit.sdk_group(name) is None:
        raise NetworkError(f"`{name}` isn't a library function the {net.console(proj).name} port kit knows: "
                           "give the library's own name (osCreatePiManager, guMtxIdent, ...).")
    fn = net._function(function, slug)
    message = f"confirmed as {name} for the port" + (f": {note.strip()}" if note.strip() else "")
    if fn["name"] != name:
        result = net.rename_symbol(agent, slug, fn["name"], name, message)
        if not str(result).startswith("✅"):
            raise NetworkError(f"Renaming {fn['name']} to {name} was refused, so nothing is confirmed:\n{result}")
    store = net.project_store(proj["id"])
    with store.tx() as db:
        row = db.execute("SELECT id, meta FROM functions WHERE id=?", (fn["id"],)).fetchone()
        meta = json.loads(row["meta"] or "{}")
        meta["port_sdk"] = {"name": name, "by": agent.name, "note": note.strip()[:500], "at": time.time()}
        db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), fn["id"]))
        net._event(db, proj["id"], agent.id, "port_identity", f"{fn['name']} is {name}")
    return (f"✅ {fn['name']} is {name} for the port ({kit.sdk_group(name)}): Compile for PC and the link check treat "
            "it as the library's. Run Compile for PC to see what it no longer pulls in.")


def confirmed(net, proj) -> dict:
    """{function id: {"names", "corpora", "evidence"}} for the functions the user confirmed, shaped like the SDK
    identities the port already reads."""
    out = {}
    for row in net.project_store(proj["id"]).all(
            "SELECT id, meta FROM functions WHERE project_id=? AND json_extract(meta, '$.port_sdk') IS NOT NULL",
            proj["id"]):
        found = json.loads(row["meta"])["port_sdk"]
        out[row["id"]] = {"names": [found["name"]], "corpora": ["confirmed by the user"],
                          "evidence": f"confirmed by {found.get('by') or 'the user'} for the port"}
    return out
