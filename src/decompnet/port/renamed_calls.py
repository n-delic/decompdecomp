"""Calls to a function by a name it no longer has, put right (docs/PORTING.md, phase 3).

When polishing renames a function, a caller can keep calling it by its old name: an automatic `func_80012345`, or
an earlier chosen name the symbol table still maps to the same address. The console build links by address and
never notices; a PC link refuses it. The link check (port/pc_link.py) knows each old name and the function's name
now, so the callers are rewritten here, whole words only, in the functions that use the old name and nowhere else,
and each rewritten function is judged twice like a port fix: its console build must still be the game's exact
bytes, and the PC compiler must find it no worse. A function that fails either keeps its C, and is listed.

The same pass puts right a base symbol the decompiler chose wrongly: `*(s32 *)((u8 *)&D_8009693C + 0x58F4)`, where
D_8009693C is a label inside code and the variable meant is the one at 0x8009C230. The link check lists such a base
as data nothing defines; each use, cast to a byte pointer or on a byte array, becomes the variable at that address
(`(u8 *)&D_8009C230`), declared where the function lacks it. The address is the same, so the console's bytes are.
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


# a comment or a string or character literal: the rewrites change code only
_NOT_CODE = re.compile(r'(//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', re.S)


def _in_code(source: str, change) -> str:
    """`source` with `change` applied to its code, and its comments and literals as they were."""
    parts = _NOT_CODE.split(source)
    return "".join(part if i % 2 else change(part) for i, part in enumerate(parts))


def _unstatic(source: str, name: str) -> str:
    """`source` with `static` taken off the definition (and prototypes) of `name`, so other files can call it."""
    return _in_code(source, lambda code: re.sub(rf"\bstatic\s+(?=[^;{{}}()]*\b{re.escape(name)}\s*\()", "", code))


_BYTE = r"(?:u8|s8|char|unsigned char|signed char)"
_NUMBER = r"(0x[0-9A-Fa-f]+|\d+)"


def bases(net, proj, rows, groups, facts) -> dict[str, list[tuple[str, int, str, int]]]:
    """{function: [(base symbol, offset, target, offset into the target)]} for the data the link check lists that the
    C uses as a byte-pointer base plus an offset reaching another variable (or a place inside one)."""
    symbols = net.symbols(proj["id"])
    out: dict[str, list[tuple[str, int, str]]] = {}
    for base in groups.get("data", []):
        address = symbols.resolve(base)
        if address is None:
            continue
        for function in facts["needed"].get(base, ()):
            row = rows.get(function)
            if row is None:
                continue
            for _, number in _uses(row["matched_source"], base):
                offset = int(number, 0)
                target = address + offset
                name, inside = symbols.name_at(target)
                if not name or symbols.resolve(name) is None or symbols.resolve(name) + inside != target:
                    name, inside = f"D_{target:08X}", 0
                out.setdefault(function, []).append((base, offset, name, inside))
    return out


def _uses(source: str, base: str):
    """(text, offset) of each `(T *)&base + N`, and `base + N` where base is declared a byte array."""
    b = re.escape(base)
    source = "".join(part if i % 2 == 0 else " " * len(part)  # comments and literals blanked: code only
                     for i, part in enumerate(_NOT_CODE.split(source)))
    found = [(m.group(0), m.group(2)) for m in re.finditer(
        rf"(\(\s*{_BYTE}\s*\*\s*\)\s*&\s*{b})\s*\+\s*{_NUMBER}", source)]
    if re.search(rf"\b{_BYTE}\s+{b}\s*\[\s*\]", source):
        found += [(m.group(0), m.group(1)) for m in re.finditer(rf"(?<![\w&)])\b{b}\s*\+\s*{_NUMBER}", source)]
    return found


def _rebase(source: str, base: str, offset: int, target: str, inside: int = 0, header: str = "") -> str:
    """`source` with each use of `base + offset` naming `target` (plus `inside`, a place within it), and `target`
    declared if it isn't."""
    b = re.escape(base)
    n = rf"(?:0x0*{offset:X}|0x0*{offset:x}|{offset})\b"
    more = f" + 0x{inside:X}" if inside else ""
    element = re.search(rf"\b({_BYTE})\s+{b}\s*\[\s*\]", source)

    def change(code: str) -> str:
        code = re.sub(rf"\((\s*{_BYTE}\s*\*\s*)\)\s*&\s*{b}\s*\+\s*{n}", rf"(\1)&{target}{more}", code)
        if element:
            code = re.sub(rf"(?<![\w&)])\b{b}\s*\+\s*{n}", f"(({element.group(1)} *)&{target}{more})", code)
        return code
    out = _in_code(source, change)
    if out != source and not re.search(rf"\b{re.escape(target)}\b", source + header):
        out = f"extern char {target}[];\n" + out
    return out


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
        groups, facts = pc_link.undefined(net, proj)
        rebased = bases(net, proj, rows, groups, facts)
        statics = {n for n in groups.get("static", []) if n in rows}
    if not names and not rebased and not statics:
        log("No call uses an old name, no base symbol needs putting right and no function is static.")
        return {"names": 0, "bases": 0, "statics": 0, "functions": 0, "kept": [], "refused": []}
    log(f"{len(names)} old names, called from {len(users)} functions; {len(rebased)} functions with a wrong base symbol; "
        f"{len(statics)} static functions others call: rewriting them and judging each function twice")

    def header(fn) -> str:
        context = net._context(proj["id"], net._effective_unit(fn, units))
        return context["header"] if context is not None else ""

    rewritten, refused = {}, []
    for function in sorted(set(users) | set(rebased) | statics):
        row = rows.get(function)
        if row is None:  # library code the kit replaces, or no longer matched: nothing of ours to rewrite
            continue
        source = row["matched_source"]
        for old in sorted(users.get(function, ())):
            source = _in_code(source, lambda code, old=old: re.sub(rf"\b{re.escape(old)}\b", names[old], code))
        if function in statics:
            source = _unstatic(source, function)
        for base, offset, target, inside in rebased.get(function, ()):
            source = _rebase(source, base, offset, target, inside, header(row))
        if source != row["matched_source"]:
            rewritten[row["id"]] = source

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
    log(f"{len(done)} functions rewritten and kept (old names, wrong bases, static)"
        + (f"; {len(refused)} kept their C: " + "; ".join(f"{r['function']} ({r['why']})" for r in refused[:8])
           if refused else ""))
    return {"names": len(names), "bases": len(rebased), "statics": len(statics),
            "functions": len(set(users) | set(rebased) | statics), "kept": done, "refused": refused}


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
