"""Verification packs: what a compiler server needs to judge a project's submissions without the game.

The app's judge links a compiled function at its address. It resolves the function's references with the
project's symbols, and checks any data the C defines against the game's bytes at the addresses the original code
uses (rom/match.py). So beyond each function's target bytes it needs:
- the symbol table and the game's segment map;
- the program segments' bytes (the code and data, not the assets);
- the overlay data the project was given (memory_regions.json);
- the settings the consoles' judges read, and the context headers.
A pack holds exactly that, and none of the owner's paths or other settings.

`decomp hosted-export` writes it beside the agents' file, as DIR/packs/<slug>.sqlite3. A hosted server gives it only
to verifiers (GET /v1/p/<slug>/pack), never to agents: it holds game data. On a compiler server
`decomp judge-worker --packs DIR` builds a small scratch project from each pack (`scratch`) and judges with the app's
own judge, so a match there is a match in the app.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import zlib
from pathlib import Path

FORMAT = "1"
# The settings the judge reads (consoles/, rom/, project.py, judging.py); everything else stays with the owner.
JUDGE_KEYS = ("kind", "platform", "big_endian", "gp", "rom", "segments", "compiler", "compiler_ranges",
              "function_compilers", "units", "system_calls_named", "kernel_calls_named", "overlays",
              "object_reading", "modules", "main_module", "autoloads", "options")
PRIVATE_COMPILER_KEYS = ("cmd", "ro_paths")  # paths on the owner's computer
PROGRAM = "program.bin"

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE segments(name TEXT NOT NULL, rom_start INTEGER NOT NULL, data BLOB NOT NULL);
CREATE TABLE files(name TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE symbols(name TEXT NOT NULL, address INTEGER NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL);
CREATE TABLE functions(id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT, vram INTEGER, rom_offset INTEGER,
    size INTEGER, n_insns INTEGER, target BLOB, relocs TEXT, meta TEXT);
CREATE TABLE contexts(unit TEXT PRIMARY KEY, version INTEGER NOT NULL, header BLOB NOT NULL);
"""


def judge_settings(cfg: dict) -> dict:
    out = {k: cfg[k] for k in JUDGE_KEYS if k in cfg}
    if isinstance(out.get("compiler"), dict):
        out["compiler"] = {k: v for k, v in out["compiler"].items() if k not in PRIVATE_COMPILER_KEYS}
    if isinstance(out.get("rom"), dict):
        out["rom"] = {**out["rom"], "file": PROGRAM, "program": PROGRAM}
    return out


def export_pack(net, slug: str, path: Path) -> dict:
    """Write a project's verification pack (replacing an older one whole: a verifier downloads it whole)."""
    from .network import Network
    proj = Network._project(net, slug)
    cfg = net.cfg(proj)
    if cfg.get("kind") == "fork":
        raise ValueError(f"{slug} builds from its own source tree; only ROM projects can be hosted yet")
    root = Path(proj["root"])
    rom = (root / cfg["rom"]["file"]).read_bytes()
    if hashlib.sha1(rom).hexdigest() != cfg["rom"].get("sha1", hashlib.sha1(rom).hexdigest()):
        raise ValueError(f"{slug}'s game file isn't the one it was imported from")
    store = net.project_store(proj["id"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    db = sqlite3.connect(part)
    try:
        db.executescript(SCHEMA)
        meta = {"format": FORMAT, "kind": "pack", "slug": slug, "name": proj["name"], "arch": proj["arch"] or "",
                "big_endian": str(int(bool(proj["big_endian"]))), "config": json.dumps(judge_settings(cfg)),
                "rom_size": str(len(rom)), "exported_at": repr(time.time())}
        db.executemany("INSERT INTO meta VALUES(?,?)", meta.items())
        segments = cfg.get("segments") or []
        db.executemany("INSERT INTO segments VALUES(?,?,?)",
                       [(s["name"], s["rom_start"], zlib.compress(rom[s["rom_start"]:s["rom_end"]], 9)) for s in segments])
        regions = root / "memory_regions.json"
        if regions.is_file():  # overlay data the owner supplied (rom/memory.py), checked there by its digest
            spec = json.loads(regions.read_text(encoding="utf-8"))
            db.execute("INSERT INTO files VALUES(?,?)", ("memory_regions.json", zlib.compress(regions.read_bytes(), 9)))
            payload = (root / spec["file"]).resolve()
            if payload.is_relative_to(root.resolve()):
                db.execute("INSERT INTO files VALUES(?,?)", (spec["file"], zlib.compress(payload.read_bytes(), 9)))
        db.executemany("INSERT INTO symbols VALUES(?,?,?,?)",
                       [(r["name"], r["address"], r["size"], r["kind"])
                        for r in store.all("SELECT name,address,size,kind FROM symbols WHERE project_id=?", proj["id"])])
        rows = store.all("SELECT id,name,unit,vram,rom_offset,size,n_insns,target,relocs,meta FROM functions "
                         "WHERE project_id=? AND gone_mark IS NULL", proj["id"])
        db.executemany("INSERT INTO functions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       [(r["id"], r["name"], r["unit"], r["vram"], r["rom_offset"], r["size"], r["n_insns"],
                         bytes(r["target"]) if r["target"] is not None else None, r["relocs"], r["meta"]) for r in rows])
        contexts = []
        for unit in [r["unit"] for r in store.all("SELECT DISTINCT unit FROM context_versions WHERE project_id=?", proj["id"])]:
            row = net._context(proj["id"], unit)
            if row is not None and (row["unit"] or "") == (unit or ""):
                contexts.append((unit or "", row["version"], zlib.compress((row["header"] or "").encode(), 9)))
        db.executemany("INSERT INTO contexts VALUES(?,?,?)", contexts)
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    part.replace(path)
    return {"functions": len(rows), "symbols": len(set(net.symbols(proj["id"]).by_name)),
            "segments": len(segments), "bytes": path.stat().st_size}


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scratch(pack: Path, cache: Path):
    """A scratch project built from a pack, for the app's judge: (network, slug). Built once per pack version, in
    `cache`; a newer pack builds a new one."""
    pack = Path(pack)
    db = sqlite3.connect(f"file:{pack}?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        if meta.get("format") != FORMAT:
            raise ValueError(f"{pack.name} is pack format {meta.get('format')!r}; this reads format {FORMAT}")
        slug = meta["slug"]
        home = Path(cache) / f"{slug}-{digest(pack)[:16]}"
        ready = home / "ready"
        Path(cache).mkdir(parents=True, exist_ok=True)
        from .writer_gate import WriterGate
        # A verifier runs several judges: the first builds the scratch project, the others wait and use it.
        with WriterGate(Path(cache) / f"{home.name}.build").hold():
            return _built(db, meta, slug, home, ready)
    finally:
        db.close()


def _built(db, meta, slug, home, ready):
    """Build the scratch project once (`ready` marks it done); then open it."""
    from .app.engine import RomNetwork
    if not ready.exists():
        shutil.rmtree(home, ignore_errors=True)
        root = home / "root"
        root.mkdir(parents=True)
        image = bytearray(int(meta["rom_size"]))
        for _, start, data in db.execute("SELECT name,rom_start,data FROM segments"):
            block = zlib.decompress(data)
            image[start:start + len(block)] = block
        (root / PROGRAM).write_bytes(bytes(image))
        for name, data in db.execute("SELECT name,data FROM files"):
            target = (root / name).resolve()
            if target.is_relative_to(root.resolve()):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zlib.decompress(data))
        net = RomNetwork(home / "judge.db", home)
        config = json.loads(meta["config"])
        with net.store.tx() as tx:
            pid = tx.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                             (slug, meta["name"], "verification pack", meta["arch"], int(meta["big_endian"]),
                              str(root), json.dumps(config))).lastrowid
            for row in db.execute("SELECT id,name,unit,vram,rom_offset,size,n_insns,target,relocs,meta FROM functions"):
                tx.execute("INSERT INTO functions(id,project_id,name,unit,vram,rom_offset,size,n_insns,target,relocs,meta,status) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,'open')", (row[0], pid, *row[1:]))
            tx.executemany("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           [(pid, *row) for row in db.execute("SELECT name,address,size,kind FROM symbols")])
            for unit, version, header in db.execute("SELECT unit,version,header FROM contexts"):
                tx.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,NULL,'verification pack',0,?)", (pid, version, zlib.decompress(header).decode(), unit))
        ready.write_text(slug)
    else:
        net = RomNetwork(home / "judge.db", home)
    return net, slug
