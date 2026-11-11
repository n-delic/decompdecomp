"""Definitions for the game's zeroed memory, so the port's C links (docs/PORTING.md, phase 3).

On the console, memory the game zeroes at boot (`.bss`: globals, buffers, state) has no bytes in the ROM; the C
reaches each piece by a name the split's linker script gave an address. A PC program has to define every one of
them. There's nothing to extract or judge: each needs only a definition the size the PC needs, and the size comes
from the type the game's C already declares it with (`extern Player *D_800A1234;` becomes `Player *D_800A1234;`,
8 bytes on a 64-bit PC where the console had 4). So the definitions are written from the declarations and
compiled by the same PC compiler as the game's C:

- one file includes the shared header and defines each name with the type it's declared with, `__typeof__(x) x;`
  (a declaration only a function's own C makes is copied in first);
- an array declared without a size gets as many elements as fit in twice the room it had on the console (the gap
  to the next symbol), since an element can grow on a PC but not shrink;
- a name whose declaration the PC compiler can't use there (a type only its function knows) goes in a second file
  with no header, as zeroed bytes: twice its room, aligned for anything.

What the definitions can't see: code that runs off the end of one variable into the next (the console placed them
side by side, the PC needn't), and a variable that holds a pointer but is declared as a 32-bit integer (the PC-fix
task's pointer warnings). Both show once the port runs.

The files hold names and types, no game bytes, and go in the project's `port/src` folder for the port's build.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

from . import pc_compile

FOLDER = "port/src"
TYPED, RAW = "zeroed_data.c", "zeroed_data_raw.c"
_LAST = 16  # room assumed for the last symbol when nothing after it says where it ends
SCHEMA = '''CREATE TABLE IF NOT EXISTS port_zeroed(project_id INTEGER PRIMARY KEY, defines TEXT, typed INTEGER,
            raw INTEGER, checked_at REAL)'''
_TENTATIVE = "tentative array definition assumed to have one element"


def _store(net, proj):
    store = pc_compile._store(net, proj)
    with store.tx() as db:
        db.execute(SCHEMA)
    return store


def candidates(net, proj, names) -> list[dict]:
    """Of the undefined data `names`, those in zeroed memory: [{"name", "address", "room"}], `room` being the bytes
    up to the next symbol the project knows."""
    cfg = net.cfg(proj)
    ends = [int(s["vram"]) + int(s["rom_end"]) - int(s["rom_start"]) for s in cfg.get("segments", [])
            if s.get("vram") is not None and s.get("rom_start") is not None and s.get("rom_end") is not None]
    if not ends:
        return []
    top = max(ends)
    low, high = net.console(proj).ram
    symbols = net.symbols(proj["id"])
    at = {name: symbols.resolve(name) for name in names}  # an automatic name has no entry, only its address
    known = sorted({s.address for s in symbols.by_name.values() if s.address is not None and s.address >= top}
                   | {a for a in at.values() if a is not None})
    out = []
    for name in sorted(names):
        address = at[name]
        if address is None or not (top <= address < high) or address < low:
            continue
        after = next((a for a in known if a > address), None)
        out.append({"name": name, "address": address, "room": (after - address) if after else _LAST})
    return out


def _declarations(sources: list[str], names: set[str]) -> dict[str, str]:
    """A declaration (`extern ...;`) of each name that the functions' own C makes, when the header doesn't."""
    found: dict[str, str] = {}
    pattern = re.compile(r"\bextern\b[^;{}]*;")
    for source in sources:
        for match in pattern.finditer(source):
            text = match.group(0)
            for name in set(re.findall(r"\b[A-Za-z_]\w*\b", text)) & names:
                if name not in found and not re.search(rf"\b{re.escape(name)}\s*\(", text):  # not a function
                    found[name] = " ".join(text.split())
    return found


def _typed(entries: list[dict], declared: dict[str, str], arrays: set[str]) -> tuple[str, dict[int, str]]:
    """The typed file, and which name each of its lines belongs to (to put a name the compiler refuses elsewhere)."""
    lines = ['#include "common.h"', "/* The game's zeroed memory, with the types its C declares (port/zeroed.py). */"]
    owner: dict[int, str] = {}
    for e in entries:
        name = e["name"]
        start = len(lines) + 1
        if name in declared:
            lines.append(declared[name])
        if name in arrays:  # declared without a size: room for twice the console's bytes, in PC-sized elements
            lines.append(f"__typeof__({name}[0]) {name}[(2 * {e['room']} + sizeof({name}[0]) - 1) / "
                         f"sizeof({name}[0])];")
        else:
            lines.append(f"__typeof__({name}) {name};")
        for n in range(start, len(lines) + 1):
            owner[n] = name
    return "\n".join(lines) + "\n", owner


def _raw(entries: list[dict]) -> str:
    lines = ["/* The game's zeroed memory whose declared type the PC can't use here: zeroed bytes, twice the room it",
             "   had on the console (port/zeroed.py). */"]
    lines += [f"_Alignas(16) unsigned char {e['name']}[{2 * e['room']}];" for e in entries]
    return "\n".join(lines) + "\n"


def build(net, proj, names, cc: list[str], log=print) -> dict:
    """Write and compile the definitions for the zeroed data among `names` (the link check's undefined data):
    {"defines", "typed", "raw", "files": {name: text}}; recorded for the link check."""
    entries = candidates(net, proj, names)
    header = pc_compile._header(net, proj, "")
    sources = [f["source"] for f in pc_compile.functions(net, proj)]
    want = {e["name"] for e in entries}
    declared = {n: d for n, d in _declarations(sources, want).items()
                if not re.search(rf"\b{re.escape(n)}\b", header)}
    arrays: set[str] = set()
    typed, raw = list(entries), []
    files: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="pc-zeroed-") as tmp:
        root = Path(tmp)
        for folder in ("include", "src", "obj"):
            (root / folder).mkdir()
        (root / "include" / "common.h").write_text(header, encoding="utf-8")
        env = pc_compile._environment()
        # every error, not the first 20; an unsized array would silently get one element
        strict = [*cc, "-ferror-limit=0", "-Werror=tentative-definition-array"]
        for attempt in range(4):  # each pass moves the names the compiler refused or sized wrongly
            text, owner = _typed(typed, declared, arrays)
            # _compile puts its own #include first, as the typed file's first line is, and numbers the lines after it
            found = [{**d, "line": d["line"] + 1} for d in pc_compile._compile(
                root, strict, env, attempt, "common.h", text.split("\n", 1)[1]) if d["level"] == "error"]
            mine = [d for d in found if not d["file"] and d["line"] in owner]
            single = {owner[d["line"]] for d in mine if _TENTATIVE in d["message"]} - arrays
            refused = {owner[d["line"]] for d in mine if _TENTATIVE not in d["message"]}
            if len(mine) < len(found) and not single and not refused:
                refused = {e["name"] for e in typed}  # an error outside the definitions (the header): all go raw
            if not single and not refused:
                break
            arrays |= single
            raw += [e for e in typed if e["name"] in refused]
            typed = [e for e in typed if e["name"] not in refused]
        text, _ = _typed(typed, declared, arrays)
        files[TYPED], files[RAW] = text, _raw(raw)
    result = {"defines": sorted(want), "typed": len(typed), "raw": len(raw), "files": files}
    with _store(net, proj).tx() as db:
        db.execute("INSERT OR REPLACE INTO port_zeroed(project_id, defines, typed, raw, checked_at) VALUES(?,?,?,?,?)",
                   (proj["id"], json.dumps(result["defines"]), len(typed), len(raw), time.time()))
    root = Path(proj["root"]) / FOLDER
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    log(f"Defined {len(want)} pieces of zeroed memory for the PC: {len(typed)} with their declared types, {len(raw)} "
        f"as zeroed bytes (in {FOLDER})")
    return result


def defined(net, proj) -> list[str]:
    """The names the last build defined (none before it)."""
    row = _store(net, proj).one("SELECT defines FROM port_zeroed WHERE project_id=?", proj["id"])
    return json.loads(row["defines"]) if row is not None else []


def summary(net, proj) -> dict | None:
    row = _store(net, proj).one("SELECT defines, typed, raw, checked_at FROM port_zeroed WHERE project_id=?",
                                proj["id"])
    if row is None:
        return None
    return {"symbols": len(json.loads(row["defines"])), "typed": row["typed"], "raw": row["raw"],
            "checked_at": row["checked_at"], "folder": FOLDER}
