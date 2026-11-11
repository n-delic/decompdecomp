"""Compile a finished game's C for a PC, and keep what breaks (docs/PORTING.md, phase 3's first step).

Before a port is built, this says how far the game's C is from building at all: every matched function the port
keeps (the game's own and the library code it compiles as it is, not what the kit replaces) is compiled on its own,
with the project's shared header, by a current clang for a 64-bit PC. Nothing is linked or run. What fails, and what
compiles but keeps a pointer in a 32-bit integer (the casts clang warns about), is recorded per function in the
project's database, so the readiness report counts it and the port-fix agents of phase 4 can be told what to fix.

The compiler is the pinned Zig (toolchains.toml `zig`): its clang brings its own headers for every target, so the
target is the same on every computer (x86_64 Linux) and nothing comes from the system. The game's C names no system
header (its shared header defines its types), so `-ffreestanding` stands in for the C library.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import limits, toolchains
from ..hostenv import NO_WINDOW
from ..network import IN_BUILD, NetworkError

TOOL = "zig"
TARGET = "x86_64-linux-gnu"
# gnu17 so a newer clang's default doesn't move the counts; non-prototype calls are only refused by C23. PORT is
# defined, as the port's build defines it: a fix the console's compiler can't take goes behind #ifdef PORT.
FLAGS = ["-target", TARGET, "-std=gnu17", "-DPORT=1", "-ffreestanding", "-fno-color-diagnostics", "-fno-caret-diagnostics",
         "-fdiagnostics-show-option", "-fno-stack-protector", "-Wno-deprecated-non-prototype", "-Wpointer-to-int-cast",
         "-Wint-to-pointer-cast", "-Wvoid-pointer-to-int-cast"]
POINTER_SIZE = frozenset({"pointer-to-int-cast", "int-to-pointer-cast", "void-pointer-to-int-cast",
                          "int-to-void-pointer-cast"})
TIMEOUT = 120
# a function's last result, and its port-fix claim (app/port_agents.py): who holds it, until when, tries, a note
SCHEMA = '''CREATE TABLE IF NOT EXISTS port_compile(project_id INTEGER, name TEXT, status TEXT, kinds TEXT,
            diagnostics TEXT, checked_at REAL, agent_id INTEGER, lease REAL, attempts INTEGER DEFAULT 0, note TEXT,
            symbols TEXT, PRIMARY KEY(project_id, name))'''
# the symbols a function's object defines and needs, for the link check (port/pc_link.py); added after the table
_SYMBOLS = "ALTER TABLE port_compile ADD COLUMN symbols TEXT"
# what each kind of failure is, for the report (the order is the order it lists them in)
KINDS = {
    "assembly": "MIPS assembly in the C (asm statements)",
    "undeclared": "calls to functions with no declaration, or implicit int (refused since C99)",
    "integers as pointers": "integers and pointers mixed without a cast (refused by current compilers)",
    "size checks": "the C's own size checks fail (arrays of negative size)",
    "header": "errors in the shared header",
    "other": "other errors",
    "pointer size": "pointers cast to and from 32-bit integers (compiles, but loses half of a 64-bit pointer)",
}
_DIAGNOSTIC = re.compile(r"^(?P<file>[^\n]*?):(?P<line>\d+):(?:\d+:)? (?P<level>fatal error|error|warning): "
                         r"(?P<message>.*?)(?: \[-W(?P<flag>[\w+-]+)[^\]]*\])?$", re.M)


def _store(net, proj):
    store = net.project_store(proj["id"])
    ready = net.__dict__.setdefault("_port_compile_stores", set())
    if proj["id"] not in ready:
        with store.tx() as db:
            db.execute(SCHEMA)
            if "symbols" not in {r[1] for r in db.execute("PRAGMA table_info(port_compile)").fetchall()}:
                db.execute(_SYMBOLS)
        ready.add(proj["id"])
    return store


def kind(level: str, message: str, flag: str | None, in_header: bool) -> str | None:
    """Which of KINDS a diagnostic is; None for a warning a port needn't act on."""
    if level == "warning":
        return "pointer size" if flag in POINTER_SIZE else None
    if in_header:
        return "header"
    if " in asm" in message or "instruction mnemonic" in message or "invalid operand for instruction" in message \
            or "invalid output constraint" in message or "invalid input constraint" in message:
        return "assembly"
    if flag in ("implicit-function-declaration", "implicit-int"):
        return "undeclared"
    if flag == "int-conversion":
        return "integers as pointers"
    if "array with a negative size" in message or "static assertion failed" in message:
        return "size checks"
    return "other"


def diagnostics(output: str, source_file: str, offset: int) -> list[dict]:
    """clang's diagnostics as {"level", "kind", "line", "file", "message"}: lines of the function's own C are its
    own line numbers (`offset` lines of #include come first); a header's, and a file its C names with #line, keep
    that file's name."""
    out = []
    for m in _DIAGNOSTIC.finditer(output):
        level = "error" if m["level"] == "fatal error" else m["level"]
        path = Path(m["file"].replace("\\", "/"))
        header = path.parent.name == "include"  # the function's own C may name other files (#line)
        k = kind(level, m["message"], m["flag"], header)
        if k is None:
            continue
        out.append({"level": level, "kind": k, "line": int(m["line"]) - (offset if path.name == source_file else 0),
                    "file": path.name if path.name != source_file else "", "message": m["message"]})
    return out


def functions(net, proj) -> list[dict]:
    """The matched functions a port keeps, each {"name", "header", "source"}: the game's own and library code the
    kit doesn't replace (what the readiness report calls replaced is left out, as the port leaves it out)."""
    from ..app.function_names import canonical_source
    from ..app.sdk_identity import display_name, identify

    kit = net.console(proj).port_kit
    rows = net.project_store(proj["id"]).all(
        f"SELECT * FROM functions WHERE project_id=? AND {IN_BUILD} AND status='matched' AND matched_source IS NOT NULL "
        "ORDER BY vram, id", proj["id"])
    sdk = identify(net, proj, net.sdk_functions(proj), include_asm=True)
    units = net._unit_contexts(proj["id"])
    out = []
    for r in rows:
        hint = sdk.get(r["id"])
        if hint and kit and "replaced" in {kit.sdk_group(n) for n in hint.get("names") or [display_name(hint, r["name"])]}:
            continue
        r = dict(r)
        unit = net._effective_unit(r, units)
        out.append({"name": r["name"], "header": unit or "", "source": canonical_source(r, r["matched_source"])})
    return out


def _header(net, proj, unit: str) -> str:
    context = net._context(proj["id"], unit or None)
    return context["header"] if context is not None else ""  # none written yet


def compiler(log=print) -> list[str]:
    """The pinned compiler's command, installed the first time (it's free: no license to accept)."""
    toolchains.ensure(TOOL, log)
    return [str(toolchains.binary(TOOL)), "cc"]


def _environment() -> dict:
    """The environment compiles run in (each gets its own Zig cache in `_compile`)."""
    return dict(os.environ)


def _compile(root: Path, cc: list[str], env: dict, i: int, header: str, source: str) -> list[dict]:
    """One function's C, compiled in `root` (its include/ holds the headers) for the PC: its diagnostics."""
    file = f"f{i}.c"  # names are the project's; files are numbered, so any name is a safe path
    # only trailing space goes: leading blank lines are lines of the C, and the diagnostics count them
    (root / "src" / file).write_text(f'#include "{header}"\n{source.rstrip()}\n', encoding="utf-8")
    argv = [*cc, *FLAGS, "-Iinclude", "-c", "-o", f"obj/f{i}.o", f"src/{file}"]
    # An empty Zig cache for each compile: when Zig has built the same C before it answers from its cache and prints
    # none of the warnings, so a second check (or check_for_pc then fix_for_pc of the same C) lost every pointer-size
    # warning. Errors were unaffected (a failed build isn't cached). A fresh one costs about 0.1 s and 2 KB.
    cache = str(root / "zig-cache" / str(i))
    env = {**env, "ZIG_GLOBAL_CACHE_DIR": cache, "ZIG_LOCAL_CACHE_DIR": cache}
    try:
        done = subprocess.run(limits.capped(argv), cwd=root, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [{"level": "error", "kind": "other", "line": 0, "file": "",
                 "message": f"the compiler took longer than {TIMEOUT} s"}]
    found = diagnostics(done.stderr + done.stdout, file, 1)
    if done.returncode and not any(d["level"] == "error" for d in found):
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [f"exit code {done.returncode}"]
        found.append({"level": "error", "kind": "other", "line": 0, "file": "", "message": tail[0][:300]})
    return found


def compile_one(header: str, source: str, *, cc: list[str] | None = None, log=print, linked: dict | None = None
                ) -> list[dict]:
    """A function's C (with the shared header it compiles with) for the PC, as `check` compiles it. `linked`, when
    given, gets the object's symbols under "symbols" (None when it didn't compile)."""
    cc = cc or compiler(log)
    with tempfile.TemporaryDirectory(prefix="pc-compile-") as tmp:
        root = Path(tmp)
        for folder in ("include", "src", "obj"):
            (root / folder).mkdir()
        (root / "include" / "common.h").write_text(header, encoding="utf-8")
        found = _compile(root, cc, _environment(), 0, "common.h", source)
        if linked is not None:
            linked["symbols"] = symbols(root / "obj" / "f0.o")
        return found


def record(db, project_id: int, name: str, found: list[dict], now: float | None = None,
           symbols: dict | None = None) -> None:
    """Keep a function's result, and what agents left on it (its claim, tries, note). `symbols` (what its object
    defines and needs) replaces the last when given; a failed compile has none, so the last is kept."""
    db.execute("INSERT INTO port_compile(project_id, name, status, kinds, diagnostics, checked_at, symbols) "
               "VALUES(?,?,?,?,?,?,?) "
               "ON CONFLICT(project_id, name) DO UPDATE SET status=excluded.status, kinds=excluded.kinds, "
               "diagnostics=excluded.diagnostics, checked_at=excluded.checked_at, "
               "symbols=COALESCE(excluded.symbols, symbols)",
               (project_id, name, _status(found), json.dumps(sorted({d["kind"] for d in found})), json.dumps(found[:20]),
                now or time.time(), json.dumps(symbols) if symbols is not None else None))


def symbols(obj: Path) -> dict | None:
    """{"defines": [...], "needs": [...]}: the global symbols an object defines and the ones it needs from others;
    None when there's no object (the compile failed)."""
    if not obj.is_file():
        return None
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    defines, needs = set(), set()
    try:
        with obj.open("rb") as f:
            table = ELFFile(f).get_section_by_name(".symtab")
            for sym in table.iter_symbols() if table is not None else ():
                if (not sym.name or sym["st_info"]["bind"] == "STB_LOCAL"
                        or sym["st_info"]["type"] in ("STT_FILE", "STT_SECTION")):
                    continue
                (needs if sym["st_shndx"] == "SHN_UNDEF" else defines).add(sym.name)
    except (ELFError, OSError):
        return None
    return {"defines": sorted(defines), "needs": sorted(needs - defines)}


def check(net, slug: str, log=print, *, cc: list[str] | None = None) -> dict:
    """Compile every function the port keeps for a 64-bit PC, record each one's result, and return the summary."""
    with net.progress.view():
        proj = net.project_row(slug)
        if not net.is_rom(proj):
            raise NetworkError("Compiling for a PC reads projects decompdecomp split from a ROM; a project-mode game "
                               "builds with its own project files (ROADMAP.md, Ports).")
        todo = functions(net, proj)
        headers = {u: _header(net, proj, u) for u in {f["header"] for f in todo}}
    if not todo:
        raise NetworkError("No function of this game is matched yet: there's no C to compile for a PC.")
    cc = cc or compiler(log)
    env = _environment()
    progress = getattr(log, "progress", None)
    results: dict[str, list[dict]] = {}
    linked: dict[str, dict | None] = {}
    with tempfile.TemporaryDirectory(prefix="pc-compile-") as tmp:
        root = Path(tmp)
        (root / "include").mkdir()
        files = {unit: f"common_{n}.h" if unit else "common.h" for n, unit in enumerate(sorted(headers))}
        for unit, text in headers.items():  # numbered: a unit's name needn't be a safe file name
            (root / "include" / files[unit]).write_text(text, encoding="utf-8")
        (root / "src").mkdir()
        (root / "obj").mkdir()

        def one(i: int, f: dict) -> tuple[str, list[dict], dict | None]:
            found = _compile(root, cc, env, i, files[f["header"]], f["source"])
            return f["name"], found, symbols(root / "obj" / f"f{i}.o")

        workers = max(1, min(16, (os.cpu_count() or 2) - 1))
        with ThreadPoolExecutor(workers) as pool:
            futures = [pool.submit(one, i, f) for i, f in enumerate(todo)]
            for n, future in enumerate(futures):
                name, found, defined = future.result()
                results[name], linked[name] = found, defined
                if progress and (n % 25 == 0 or n == len(futures) - 1):
                    progress({"phase": "Compiling the game's C for a PC", "done": n + 1, "total": len(futures)})
    now = time.time()
    with net.progress.view():
        with _store(net, proj).tx() as db:
            for name in {r["name"] for r in db.execute("SELECT name FROM port_compile WHERE project_id=?",
                                                       (proj["id"],)).fetchall()} - set(results):
                db.execute("DELETE FROM port_compile WHERE project_id=? AND name=?", (proj["id"], name))  # gone
            for name, found in results.items():
                record(db, proj["id"], name, found, now, linked[name])
        result = summary(net, proj)
    sizes = next((k["functions"] for k in result["kinds"] if k["kind"] == "pointer size"), 0)
    log(f"{result['ok'] + result['warned']} of {result['total']} functions compile for a 64-bit PC; "
        f"{result['failed']} don't, and {sizes} cast pointers to and from 32-bit integers")
    return result


def _status(found: list[dict]) -> str:
    return "failed" if any(d["level"] == "error" for d in found) else "warned" if found else "ok"


def summary(net, proj) -> dict | None:
    """The last check: {"checked_at", "target", "total", "ok", "warned", "failed", "kinds": [{"kind", "about",
    "functions", "examples"}]}, or None before the first."""
    rows = _store(net, proj).all("SELECT name, status, kinds, checked_at FROM port_compile WHERE project_id=? "
                                 "ORDER BY name", proj["id"])
    if not rows:
        return None
    count = {s: sum(r["status"] == s for r in rows) for s in ("ok", "warned", "failed")}
    by_kind: dict[str, list[str]] = {}
    for r in rows:
        for k in json.loads(r["kinds"] or "[]"):
            by_kind.setdefault(k, []).append(r["name"])
    kinds = [{"kind": k, "about": about, "functions": len(by_kind[k]), "examples": by_kind[k][:12]}
             for k, about in KINDS.items() if k in by_kind]
    return {"checked_at": max(r["checked_at"] for r in rows), "target": TARGET, "total": len(rows), **count,
            "kinds": kinds}


def function(net, proj, name: str) -> list[dict] | None:
    """One function's diagnostics from the last check (None: it wasn't checked)."""
    row = _store(net, proj).one("SELECT diagnostics FROM port_compile WHERE project_id=? AND name=?",
                                proj["id"], name)
    return None if row is None else json.loads(row["diagnostics"] or "[]")
