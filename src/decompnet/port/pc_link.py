"""What stops the game's C from linking into one PC program (docs/PORTING.md, phase 3), from what Compile for PC
recorded: every function's object, the symbols it defines and the ones it needs (port/pc_compile.py). Nothing is
linked here: a symbol some function needs and none defines would be a link error, and so would one defined twice.

Each undefined symbol is sorted by what supplies it:
- the kit (libultraship and the host's C library replace libultra and libc functions);
- a function renamed since its callers were written (they still call `func_80012345`, or an earlier chosen name
  the symbol table keeps, and it's `draw_hud` now): the console build links by address and never notices, a PC
  link does;
- a function its own C defines as `static` (libultra's file-local helpers, split into functions of their own);
- the game's own functions with no C yet (assembly, or not matched);
- data the C reaches that exists only as an address in the split's assembly (phase 1 writes it as C);
- anything else.

A function that didn't compile has no object; it counts as defining itself (it will, once it compiles) and as
needing nothing yet, and the report says how many there are.
"""

from __future__ import annotations

import json
import re

from . import pc_compile

_AUTO_FUNCTION = re.compile(r"func_([0-9A-Fa-f]{8})$")
_AUTO_DATA = re.compile(r"[DB]_[0-9A-Fa-f]+$")
_LIMIT = 12  # examples per group
# what each group is, and what fixes it, in the order the report lists them
GROUPS = {
    "renamed": "functions renamed since their callers were written: the callers still use an old name",
    "static": "functions their own C defines as static: no other file can call them on a PC",
    "assembly": "the game's own functions still in assembly: each needs C for the PC",
    "not matched": "the game's own functions with no C yet",
    "data": "data that exists only as an address in the split's assembly: each needs to be C (phase 1)",
    "glue": "libultra's console plumbing the kit doesn't have (threads, RSP tasks, the cartridge and Controller Pak "
            "internals): the port's glue supplies each",
    "kit": "libultra and C library functions the kit (libultraship, the host's C library) provides",
    "other": "symbols nothing in the project names",
}


def undefined(net, proj, *, zeroed: bool = True) -> tuple[dict[str, list[str]], dict] | None:
    """({group: [names]}, facts) for the symbols some function needs and none defines, or None before Compile for
    PC has run. `zeroed`: count the generated definitions of zeroed memory (port/zeroed.py) as defining theirs."""
    rows = pc_compile._store(net, proj).all("SELECT name, symbols FROM port_compile WHERE project_id=?", proj["id"])
    if not rows:
        return None
    defined: dict[str, list[str]] = {}
    needed: dict[str, set[str]] = {}
    unread, compiled = 0, set()
    for row in rows:
        found = json.loads(row["symbols"]) if row["symbols"] else None
        if found is None:
            unread += 1
            defined.setdefault(row["name"], []).append(row["name"])
            continue
        compiled.add(row["name"])
        for name in found["defines"]:
            defined.setdefault(name, []).append(row["name"])
        for name in found["needs"]:
            needed.setdefault(name, set()).add(row["name"])
    generated = set()
    if zeroed:
        from .zeroed import defined as zeroed_names
        generated = set(zeroed_names(net, proj))
    groups: dict[str, list[str]] = {}
    for name in sorted(set(needed) - set(defined) - generated):
        if (kind := "static" if name in compiled else _group(net, proj, name)) is not None:
            groups.setdefault(kind, []).append(name)
    return groups, {"rows": len(rows), "unread": unread, "defined": defined, "needed": needed,
                    "zeroed": len(generated)}


def report(net, proj) -> dict | None:
    """{"functions", "unread", "zeroed", "undefined" (what nothing will define: not the kit's), "provided" (what the
    kit provides), "groups": [{"kind", "about", "symbols", "examples", "users"}],
    "duplicates": [{"name", "in"}]}, or None before Compile for PC has run."""
    found = undefined(net, proj)
    if found is None:
        return None
    groups, facts = found
    needed = facts["needed"]
    duplicates = [{"name": name, "in": sorted(where)} for name, where in sorted(facts["defined"].items())
                  if len(where) > 1]
    return {"functions": facts["rows"], "unread": facts["unread"], "zeroed": facts["zeroed"],
            "undefined": sum(len(v) for k, v in groups.items() if k != "kit"),  # the kit's resolve at link time
            "provided": len(groups.get("kit", [])),
            "groups": [{"kind": kind, "about": GROUPS[kind], "symbols": len(groups[kind]),
                        "examples": [_example(net, proj, kind, n) for n in groups[kind][:_LIMIT]],
                        "users": len(set().union(*(needed[n] for n in groups[kind])))}
                       for kind in GROUPS if groups.get(kind)],
            "duplicates": duplicates[:40]}


def _facts(net, proj) -> dict:
    """The project's function names by status and by address, its symbols, and the names the kit replaces."""
    cache = net.__dict__.setdefault("_pc_link_facts", {})
    store = net.project_store(proj["id"])
    key = (proj["id"], store.read_version("inputs", proj["id"]) if hasattr(store, "read_version") else 0)
    if key in cache:
        return cache[key]
    from ..app.data_units import matched
    from ..app.sdk_identity import display_name, identify
    from ..network import IN_BUILD
    rows = store.all(f"SELECT id, name, vram, status FROM functions WHERE project_id=? AND {IN_BUILD}", proj["id"])
    kit = net.console(proj).port_kit
    replaced, glue = set(), set()
    from .identities import confirmed
    sdk = identify(net, proj, net.sdk_functions(proj), include_asm=True) | confirmed(net, proj)
    for r in rows:
        hint = sdk.get(r["id"])
        names = (hint.get("names") or [display_name(hint, r["name"])]) if hint else []
        groups = {kit.sdk_group(n) for n in names} if kit else set()
        if "replaced" in groups:
            replaced.update([r["name"], *names])
        elif "glue" in groups:
            glue.update([r["name"], *names])
    facts = {"status": {r["name"]: r["status"] for r in rows}, "at": {r["vram"]: r["name"] for r in rows},
             "symbols": net.symbols(proj["id"]), "replaced": replaced, "glue": glue, "kit": kit,
             "data_c": sorted((a, a + (u["span"] or u["size"] or 0)) for a, u in matched(net, proj).items())}
    cache.clear()
    cache[key] = facts
    return facts


def _group(net, proj, name: str) -> str | None:
    """The group an undefined symbol is in, or None for data a matched data unit defines in C (phase 1)."""
    facts = _facts(net, proj)
    # a name the kit sorts, with no matched C of the game's (none, or its assembly copy, which a port never builds)
    unproven = (facts["kit"].sdk_group(name) if facts["kit"] and facts["status"].get(name) != "matched"
                else None)
    if name in facts["replaced"] or unproven == "replaced":
        return "kit"
    if name in facts["glue"] or unproven == "glue":
        return "glue"
    status = facts["status"].get(name)
    if status is not None:
        return "assembly" if status == "asm" else "not matched" if status != "matched" else "other"
    automatic = _AUTO_FUNCTION.match(name)
    if automatic and int(automatic.group(1), 16) in facts["at"]:
        return "renamed"
    symbol = facts["symbols"].by_name.get(name)
    if symbol is not None and symbol.kind == "func" and facts["at"].get(symbol.address, name) != name:
        return "renamed"  # an earlier chosen name the symbol table still has (renamed twice)
    if _AUTO_DATA.match(name) or (symbol is not None and symbol.kind != "func"):
        address = facts["symbols"].resolve(name)
        if address is not None and any(lo <= address < hi for lo, hi in facts["data_c"]):
            return None
        return "data"
    return "other"


def current_name(net, proj, old: str) -> str:
    """The name a function renamed since `old` (its automatic name, or an earlier chosen one) has now."""
    facts = _facts(net, proj)
    automatic = _AUTO_FUNCTION.match(old)
    address = int(automatic.group(1), 16) if automatic else facts["symbols"].by_name[old].address
    return facts["at"][address]


def _example(net, proj, kind: str, name: str) -> str:
    return f"{name} (now {current_name(net, proj, name)})" if kind == "renamed" else name
