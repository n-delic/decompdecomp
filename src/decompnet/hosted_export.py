"""A project as a small hosted file, for `decomp-mcp-gateway hosted serve` (gateway/src/hosted.rs).

    decomp hosted-export SLUG DIR      # writes DIR/projects/SLUG.sqlite3; again to update it

The hosted file holds what an agent on another machine needs to decompile a function and judge its own compile:
- each function's name, unit, address, size, target bytes, relocations, assembly and effective compiler;
- the project's context headers (the latest of each unit);
- each function's status, best score and source, and matching C.
It holds no attempt history, transcripts, logs or saved-progress copies, which are most of a project database.

Exporting again updates the file in place, in one transaction, so a server can keep running:
- its claims, submissions and notes stay;
- a function matched on the server stays matched;
- functions gone from the project are marked removed.
"""
from __future__ import annotations

import json
import sqlite3
import time
import zlib
from pathlib import Path

FORMAT = "1"  # gateway/src/hosted.rs FORMAT
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contexts(unit TEXT PRIMARY KEY, version INTEGER NOT NULL, header BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS functions(id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT, vram INTEGER,
    rom_offset INTEGER, size INTEGER, n_insns INTEGER, target BLOB, relocs TEXT, asm BLOB, compiler TEXT,
    status TEXT NOT NULL, best_score INTEGER, best_source BLOB, matched_source BLOB, matched_by TEXT, matched_at REAL);
CREATE INDEX IF NOT EXISTS functions_name ON functions(name, unit);
CREATE INDEX IF NOT EXISTS functions_vram ON functions(vram);
CREATE INDEX IF NOT EXISTS functions_open ON functions(status, n_insns);
"""
COMPILER_KEYS = ("toolchain", "flags")  # (the rest of a profile names paths on the owner's computer)


def pack(text):
    """Assembly, C and headers are stored zlib-compressed (hosted.rs unpack): about a fifth of their size."""
    return None if text is None else zlib.compress(text.encode("utf-8"), 9)


def _status(row) -> str:
    if row["status"] == "open" and row["aside_mark"]:
        return "aside"  # no C makes it: not claimable
    return row["status"]


def export(net, slug: str, path: Path, log=print) -> dict:
    """Write or update the hosted file of one ROM project. Returns counts."""
    from .network import Network
    from .project import compiler_for
    proj = Network._project(net, slug)  # (not the engine's lookup, which rechecks saved progress)
    cfg = net.cfg(proj)
    if cfg.get("kind") == "fork":
        raise ValueError(f"{slug} builds from its own source tree; only ROM projects can be hosted yet")
    store = net.project_store(proj["id"])
    rows = store.all("SELECT id,name,unit,vram,rom_offset,size,n_insns,target,relocs,asm,status,best_score,best_source,"
                     "matched_source,matched_at,aside_mark,meta FROM functions WHERE project_id=? AND gone_mark IS NULL "
                     "ORDER BY id", proj["id"])
    units = [r["unit"] for r in store.all("SELECT DISTINCT unit FROM context_versions WHERE project_id=?", proj["id"])]
    contexts = []
    for unit in units:
        row = net._context(proj["id"], unit)
        if row is not None and (row["unit"] or "") == (unit or ""):
            contexts.append((unit or "", row["version"], pack(row["header"] or "")))
    default = {k: cfg.get("compiler", {}).get(k) for k in COMPILER_KEYS}
    meta = {"format": FORMAT, "slug": slug, "name": proj["name"], "platform": cfg.get("platform") or "",
            "arch": proj["arch"] or "", "big_endian": str(int(bool(proj["big_endian"]))),
            "compiler": json.dumps(default), "instructions": cfg.get("instructions") or "",
            "exported_at": repr(time.time())}

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        known = {r[0] for r in db.execute("SELECT id FROM functions")}
        db.execute("BEGIN IMMEDIATE")
        db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", meta.items())
        db.execute("DELETE FROM contexts")
        db.executemany("INSERT INTO contexts VALUES(?,?,?)", contexts)
        for r in rows:
            compiler = json.dumps({k: v for k, v in compiler_for(cfg, r["vram"], r).items() if k in COMPILER_KEYS})
            target = bytes(r["target"]) if r["target"] is not None else None
            values = (r["name"], r["unit"], r["vram"], r["rom_offset"], r["size"], r["n_insns"], target, r["relocs"],
                      pack(r["asm"]), compiler)
            matched = r["status"] == "matched"
            best = None if matched else pack(r["best_source"])  # (a match's best C is its matching C)
            db.execute(
                "INSERT INTO functions(id,name,unit,vram,rom_offset,size,n_insns,target,relocs,asm,compiler,status,"
                "best_score,best_source,matched_source,matched_by,matched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                # The project's facts are the owner's; a match or better score made on the server stays.
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,unit=excluded.unit,vram=excluded.vram,"
                "rom_offset=excluded.rom_offset,size=excluded.size,n_insns=excluded.n_insns,target=excluded.target,"
                "relocs=excluded.relocs,asm=excluded.asm,compiler=excluded.compiler,"
                "status=CASE WHEN functions.status='matched' THEN 'matched' ELSE excluded.status END,"
                "best_score=CASE WHEN functions.best_score IS NOT NULL AND (excluded.best_score IS NULL OR "
                "functions.best_score<=excluded.best_score) THEN functions.best_score ELSE excluded.best_score END,"
                "best_source=CASE WHEN functions.best_score IS NOT NULL AND (excluded.best_score IS NULL OR "
                "functions.best_score<=excluded.best_score) THEN functions.best_source ELSE excluded.best_source END,"
                "matched_source=COALESCE(functions.matched_source,excluded.matched_source),"
                "matched_by=COALESCE(functions.matched_by,excluded.matched_by),"
                "matched_at=COALESCE(functions.matched_at,excluded.matched_at)",
                (r["id"], *values, _status(r), r["best_score"], best,
                 pack(r["matched_source"]) if matched else None, "the project owner" if matched else None,
                 r["matched_at"] if matched else None))
        removed = known - {r["id"] for r in rows}
        db.executemany("UPDATE functions SET status='removed' WHERE id=? AND status!='matched'",
                       [(i,) for i in removed])
        db.execute("COMMIT")
        if new:
            db.execute("VACUUM")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    counts = {"functions": len(rows), "contexts": len(contexts), "removed": len(removed),
              "bytes": path.stat().st_size}
    log(f"{slug}: {counts['functions']} functions, {counts['contexts']} context headers, "
        f"{counts['bytes'] / 2**20:.1f} MiB -> {path}")
    return counts


def main(slug: str, out_dir: str, home: Path) -> int:
    """`decomp hosted-export`: read the home's databases as they are (store.ATTACH_ONLY), safe while the app runs."""
    from . import store as storage
    storage.ATTACH_ONLY = True
    from .app.engine import RomNetwork
    internal = Path(home) / ".decomp"
    net = RomNetwork(internal / "decomp.db", Path(home), project_databases=True)
    export(net, slug, Path(out_dir) / "projects" / f"{slug}.sqlite3")
    return 0
