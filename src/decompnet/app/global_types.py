"""Types and sizes for the Globals tab, learned from C that already compiled.

A header's `extern s32 gScore;` or a matched function's `u8 gBuf[16];` says what a data symbol is, and the symbol
table splat made records neither (most data has no type, and a size of 0). When a function matches or is polished the
code it matched with is read for declarations of the game's data symbols, and each one gives the symbol its C type
and, where the type fixes it (`u8[16]` is 16 bytes), its size. The size lets the drawer in the Globals tab match uses
inside a table, and the type and size are shown to agents in a function's brief, which also helps functions that are
still open.

Who said so is kept (`type_source`): a type an emulator run saw or a person typed is never replaced, an agent's guess
is, because a declaration that compiled to the right bytes is better evidence than one.
"""
from __future__ import annotations

import logging
import re

from .globals_view import C_TYPE

log = logging.getLogger(__name__)

SOURCE = "match"
# What a C type takes in bytes on the 32-bit consoles; a struct, a typedef the table doesn't know or a macro size
# gives no size (the type is still recorded).
WIDTHS = {"char": 1, "s8": 1, "u8": 1, "i8": 1, "bool": 1, "short": 2, "s16": 2, "u16": 2, "i16": 2,
          "int": 4, "long": 4, "s32": 4, "u32": 4, "i32": 4, "f32": 4, "float": 4, "size_t": 4,
          "s64": 8, "u64": 8, "i64": 8, "f64": 8, "double": 8, "OSTime": 8}
NOT_A_TYPE = {"return", "goto", "else", "case", "typedef", "break", "continue", "do", "sizeof", "if", "while", "for",
              "switch", "default", "define", "include", "void"}
QUALIFIER = r"(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*"
DECLARATION = re.compile(
    rf"^[ \t]*(?:(?P<extern>extern)\s+)?(?:static\s+)?(?P<type>{QUALIFIER}[A-Za-z_]\w*)(?P<stars>(?:\s*\*+)?)\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<dims>(?:\[\s*\w*\s*\])*)\s*(?:=[^;]*)?;", re.M)
COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def declarations(text: str) -> dict[str, tuple[str, int | None]]:
    """{name: (C type, size in bytes or None)} for the variables `text` declares at its top level (an indented
    declaration is a local unless it says `extern`)."""
    found = {}
    for m in DECLARATION.finditer(COMMENTS.sub(" ", text)):
        line_start = m.group(0)
        if not m.group("extern") and line_start[:1] in " \t":
            continue
        base = " ".join(m.group("type").split())
        if base.split()[-1] in NOT_A_TYPE:
            continue
        stars = m.group("stars").count("*")
        dims = re.findall(r"\[\s*(\w*)\s*\]", m.group("dims"))
        c_type = base + (" " + "*" * stars if stars else "") + "".join(f"[{d}]" for d in dims)
        if not C_TYPE.match(c_type) or len(c_type) > 80:
            continue
        width = 4 if stars else WIDTHS.get(base.split()[-1])
        if base.split()[0] in ("unsigned", "signed") and len(base.split()) == 1:
            width = 4
        if width is not None and dims:
            counts = [int(d, 0) if re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", d) else None for d in dims]
            width = None if None in counts else width * _product(counts)
        found[m.group("name")] = (c_type, width)
    return found


def _product(counts):
    total = 1
    for c in counts:
        total *= c
    return total


def record(store, project_id: int, found: dict[str, tuple[str, int | None]]) -> int:
    """Give the data symbols among `found` their type and size; how many changed."""
    if not found:
        return 0
    names = list(found)
    changed = 0
    with store.tx() as db:
        for start in range(0, len(names), 400):
            rows = db.execute(
                "SELECT name, address, size, c_type, type_source FROM symbols WHERE project_id=? AND kind<>'func' AND name IN "
                f"({','.join('?' * len(names[start:start + 400]))})", (project_id, *names[start:start + 400])).fetchall()
            for r in rows:
                c_type, width = found[r["name"]]
                if r["type_source"] in ("user", "emulator") and r["c_type"]:
                    new_type, new_source = r["c_type"], r["type_source"]
                else:
                    new_type, new_source = c_type, SOURCE
                new_size = r["size"] or width or 0
                if (new_type, new_source, new_size) == (r["c_type"], r["type_source"], r["size"] or 0):
                    continue
                db.execute("UPDATE symbols SET c_type=?, type_source=?, size=? WHERE project_id=? AND address=? AND kind<>'func'",
                           (new_type, new_source, new_size, project_id, r["address"]))
                changed += 1
    return changed


def learn(net, proj, fn, source: str) -> int:
    """A function matched or was polished with `source`: read its declarations, and those of the header it compiled
    against. Never raises: the match is already saved."""
    try:
        if not net.is_rom(proj):
            return 0
        unit = net._effective_unit(fn, net._unit_contexts(proj["id"]))
        found = declarations(net._context(proj["id"], unit)["header"] or "")
        found.update(declarations(source or ""))
        return record(net.project_store(proj["id"]), proj["id"], found)
    except Exception:  # noqa: BLE001 - evidence for the Globals tab, not part of judging
        log.warning("could not learn global types from %s", fn["name"], exc_info=True)
        return 0


def learn_project(net, slug: str) -> dict:
    """The same, from the project's headers and every matched function: for a project whose matches came before this
    existed. Read through the project's own store, so it is a normal write like the other tools."""
    proj = net._project(slug)
    if net.fork_slug(slug) or not net.is_rom(proj):
        return {"changed": 0}
    store = net.project_store(proj["id"])
    found = {}
    for row in store.all("SELECT version, header FROM context_versions WHERE project_id=? ORDER BY version", proj["id"]):
        found.update(declarations(row["header"] or ""))
    for row in store.all("SELECT matched_source FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL",
                         proj["id"]):
        found.update(declarations(row["matched_source"]))
    return {"changed": record(store, proj["id"], found)}
