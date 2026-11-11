"""A project as a small hosted file, for `decomp-mcp-gateway hosted serve` (gateway/src/hosted.rs).

    decomp hosted-export SLUG DIR      # writes DIR/projects/SLUG.sqlite3; again to update it
    decomp hosted-publish SLUG --server https://decomp.example --token dd_...   # the same, to a remote server

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


def write_fresh(net, slug: str, path: Path) -> dict:
    """A standalone export of one ROM project, to install with `merge` here or to publish to a server."""
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
    meta = {"format": FORMAT, "kind": "project", "slug": slug, "name": proj["name"], "platform": cfg.get("platform") or "",
            "arch": proj["arch"] or "", "big_endian": str(int(bool(proj["big_endian"]))),
            "compiler": json.dumps(default), "instructions": cfg.get("instructions") or "",
            "exported_at": repr(time.time())}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO meta VALUES(?,?)", meta.items())
        db.executemany("INSERT INTO contexts VALUES(?,?,?)", contexts)
        functions = []
        for r in rows:
            compiler = json.dumps({k: v for k, v in compiler_for(cfg, r["vram"], r).items() if k in COMPILER_KEYS})
            matched = r["status"] == "matched"
            functions.append((
                r["id"], r["name"], r["unit"], r["vram"], r["rom_offset"], r["size"], r["n_insns"],
                bytes(r["target"]) if r["target"] is not None else None, r["relocs"], pack(r["asm"]), compiler,
                _status(r), r["best_score"],
                None if matched else pack(r["best_source"]),  # (a match's best C is its matching C)
                pack(r["matched_source"]) if matched else None, "the project owner" if matched else None,
                r["matched_at"] if matched else None))
        db.executemany("INSERT INTO functions(id,name,unit,vram,rom_offset,size,n_insns,target,relocs,asm,compiler,"
                       "status,best_score,best_source,matched_source,matched_by,matched_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", functions)
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    return {"functions": len(rows), "contexts": len(contexts)}


def merge_sql() -> str:
    return (Path(__file__).with_name("hosted_merge.sql")).read_text(encoding="utf-8")


def merge(live: Path, fresh: Path) -> int:
    """Install a fresh export: as the project when there is none yet, else merged into it in one transaction
    (hosted_merge.sql), which a running server tolerates. Returns how many functions became 'removed'."""
    live, fresh = Path(live), Path(fresh)
    live.parent.mkdir(parents=True, exist_ok=True)
    if not live.exists():
        fresh.replace(live)
        db = sqlite3.connect(live)
        db.execute("PRAGMA journal_mode=WAL")
        db.close()
        return 0
    db = sqlite3.connect(live, timeout=30, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)  # (a file from before a column was added)
        db.execute("ATTACH DATABASE ? AS incoming", (str(fresh),))
        db.execute("BEGIN IMMEDIATE")
        removed = db.execute("SELECT COUNT(*) FROM main.functions WHERE status NOT IN ('matched','removed') "
                             "AND id NOT IN (SELECT id FROM incoming.functions)").fetchone()[0]
        for statement in _statements(merge_sql()):
            db.execute(statement)
        db.execute("COMMIT")
        db.execute("DETACH DATABASE incoming")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    fresh.unlink(missing_ok=True)
    return removed


def _statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def export(net, slug: str, path: Path, log=print) -> dict:
    """Write or update the hosted file of one ROM project. Returns counts."""
    path = Path(path)
    fresh = path.with_name(f".{path.name}.fresh")
    counts = write_fresh(net, slug, fresh)
    counts["removed"] = merge(path, fresh)
    counts["bytes"] = path.stat().st_size
    log(f"{slug}: {counts['functions']} functions, {counts['contexts']} context headers, "
        f"{counts['bytes'] / 2**20:.1f} MiB -> {path}")
    return counts


def publish(net, slug: str, server: str, token: str, log=print, post=None) -> dict:
    """Upload a fresh export and verification pack to a hosted server (an admin's token), which installs them as
    `merge` does here (POST /v1/p/<slug>/upload)."""
    import tempfile

    from .hosted_pack import export_pack
    if post is None:
        import httpx

        def post(url, data, token):
            reply = httpx.post(url, content=data, headers={"Authorization": f"Bearer {token}",
                                                           "Content-Type": "application/vnd.sqlite3"}, timeout=600)
            if reply.status_code >= 400:
                raise RuntimeError(f"{url}: HTTP {reply.status_code}: {reply.text[:500]}")
            return reply.json()
    server = server.rstrip("/")
    results = {}
    with tempfile.TemporaryDirectory() as work:
        project, pack = Path(work) / "project.sqlite3", Path(work) / "pack.sqlite3"
        counts = write_fresh(net, slug, project)
        export_pack(net, slug, pack)
        for kind, path in (("project", project), ("pack", pack)):
            results[kind] = post(f"{server}/v1/p/{slug}/upload?kind={kind}", path.read_bytes(), token)
            done = ("created" if results[kind].get("created") else "updated") if kind == "project" else "uploaded"
            log(f"{slug}: {kind} ({path.stat().st_size / 2**20:.1f} MiB) -> {server}: {done}")
    results["functions"] = counts["functions"]
    return results


def _open_home(home: Path):
    from . import store as storage
    storage.ATTACH_ONLY = True
    from .app.engine import RomNetwork
    return RomNetwork(Path(home) / ".decomp" / "decomp.db", Path(home), project_databases=True)


def publish_main(slug: str, server: str, token: str, home: Path) -> int:
    """`decomp hosted-publish`: read the home's databases as they are, safe while the app runs."""
    publish(_open_home(home), slug, server, token)
    return 0


def main(slug: str, out_dir: str, home: Path) -> int:
    """`decomp hosted-export`: read the home's databases as they are (store.ATTACH_ONLY), safe while the app runs."""
    net = _open_home(home)
    export(net, slug, Path(out_dir) / "projects" / f"{slug}.sqlite3")
    from .hosted_pack import export_pack
    counts = export_pack(net, slug, Path(out_dir) / "packs" / f"{slug}.sqlite3")
    print(f"{slug}: verification pack (for verifiers only) {counts['bytes'] / 2**20:.1f} MiB -> "
          f"{Path(out_dir) / 'packs' / (slug + '.sqlite3')}")
    return 0
