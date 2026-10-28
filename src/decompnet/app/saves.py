"""A ROM project's work as a folder of plain files, and a project rebuilt from one: to move to another computer (the
owner changes Linux distributions now and then), to keep in git, or to start over after something broke.

The work lives in the app's database, not in the project's folder, so copying the folder kept none of it. A save
writes what people and agents made: the matched C, the best attempts, the shared headers, the names, the function
splits, the notes, the instructions, what agents learned, the compiler and its ranges. Never the game: the save
holds its fingerprint (sha1), and a restore asks for the user's own copy and checks it's the same game. Every
matched function is judged again on the way in: a match counts because it matches, not because a file says so.

A project-mode game (DS, GameCube, Wii, PC; a public decomp) keeps its matched C in its own source files and git;
what only the database has is each open function's closest attempt, and a save writes those (best/), with a README
(#43: on a DS game near-misses of 97-99.9% were locked away, wanted for a port while matching goes on).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import compiler, consoles, toolchains
from ..hostenv import NO_WINDOW, stop_process_tree
from ..network import NetworkError
from ..rom.symbols import AUTO_NAME
from ..scoring import percent_text
from . import save_readme

FORMAT = 1
MANIFEST = "decompdecomp.json"
SAVE_FOLDER = "progress"  # in the project's folder, unless the user picks another
AUTOSAVE_SECONDS = 300
SET_ASIDE_META = ("asm_code", "not_c", "asm_twin")  # a set-aside function's meta that says why (network.set_aside)
GIT_TIMEOUT = 300
log = logging.getLogger("decompdecomp")


def _header_file(unit: str) -> str:
    return "common.h" if not unit else f"common_{unit}.h"


def default_folder(net, slug: str) -> Path:
    return Path(net.project_row(slug)["root"]) / SAVE_FOLDER


def save(net, slug: str, folder: Path | None = None) -> dict:
    """Write the project's work to `folder` (default: progress/ in the project's folder). Returns what was saved."""
    proj = net._project(slug)
    folder = Path(folder) if folder else default_folder(net, slug)
    if not net.is_rom(proj):
        return _save_best(net, proj, folder)
    cfg = net.cfg(proj)
    pid = proj["id"]
    names = {r["id"]: r["name"] for r in net.store.all("SELECT id, name FROM agents")}
    rows = net.store.all("SELECT name, vram, size, status, matched_source, matched_agent_id, best_source, best_score, "
                         "best_agent_id, readable, meta FROM functions WHERE project_id=? ORDER BY vram", pid)
    for sub in ("src", "best", "include"):
        shutil.rmtree(folder / sub, ignore_errors=True)
        (folder / sub).mkdir(parents=True, exist_ok=True)
    functions = {}
    for r in rows:
        if r["status"] == "matched" and r["matched_source"]:
            (folder / "src" / f"{r['name']}.c").write_text(r["matched_source"].strip() + "\n", encoding="utf-8")
            functions[r["name"]] = {"vram": r["vram"], "matched_by": names.get(r["matched_agent_id"], ""),
                                    "readable": bool(r["readable"])}
        elif r["best_source"] and r["best_score"] is not None:
            (folder / "best" / f"{r['name']}.c").write_text(r["best_source"].strip() + "\n", encoding="utf-8")
            functions[r["name"]] = {"vram": r["vram"], "best_score": r["best_score"], "best_by": names.get(r["best_agent_id"], "")}
    units = {""} | net._unit_contexts(pid)
    for unit in sorted(units):
        (folder / "include" / _header_file(unit)).write_text(net._context(pid, unit)["header"], encoding="utf-8")
    notes = [{"function": r["fn"], "text": r["text"], "by": r["agent"] or ""} for r in net.store.all(
        "SELECT n.text, a.name AS agent, f.name AS fn FROM notes n LEFT JOIN agents a ON a.id=n.agent_id "
        "JOIN functions f ON f.id=n.function_id WHERE n.project_id=? ORDER BY n.id", pid)]
    knowledge = [r["text"] for r in net.store.all("SELECT text FROM knowledge WHERE project_id=? AND status='accepted' "
                                                  "ORDER BY id", pid)]
    manifest = {
        "format": FORMAT, "saved_at": time.time(), "title": proj["name"], "platform": cfg.get("platform"),
        "game": {"sha1": cfg.get("rom", {}).get("sha1"), "file": cfg.get("rom", {}).get("file")},
        "compiler": cfg.get("compiler"), "compiler_ranges": cfg.get("compiler_ranges") or [],
        "instructions": cfg.get("instructions") or "", "knowledge": knowledge,
        "knowledge_auto": bool(cfg.get("knowledge_auto")), "headers": {u: _header_file(u) for u in sorted(units)},
        "functions": functions,
        "layout": [{"name": r["name"], "vram": r["vram"], "size": r["size"]} for r in rows],
        "symbols": [dict(r) for r in net.store.all("SELECT name, address, size, kind FROM symbols WHERE project_id=?",
                                                    pid) if not AUTO_NAME.match(r["name"])],
        "notes": notes,
        # what's set aside and why: agents' verdicts that no C makes it, the user's, a boundary report's leftover
        # (the import sets aside again what it finds by itself)
        "set_aside": [{"name": r["name"], "vram": r["vram"],
                       **{k: v for k, v in json.loads(r["meta"] or "{}").items() if k in SET_ASIDE_META}}
                      for r in rows if r["status"] == "asm"],
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    compiler = cfg.get("compiler") or {}
    compiler_name = compiler.get("toolchain") if isinstance(compiler, dict) else str(compiler)
    compiler_name = compiler_name or "not recorded"
    details = (f"- **Game:** {proj['name']}\n- **Platform:** {cfg.get('platform') or 'not recorded'}\n"
               f"- **Compiler:** {compiler_name}")
    readme = f"""# {proj['name']}: decompdecomp progress

This is a saved decompilation project created with [decompdecomp](https://github.com/n-delic/decompdecomp).
AI agents were used during the decompilation; their C attempts were kept only when decompdecomp's byte-for-byte
judge accepted them as matches.

## Project details

{save_readme.block('details', details)}
- **Progress:** {matched} matching function(s), {len(functions) - matched} best attempt(s)

## What's in this folder

This folder is the whole of the work, and the only part of the project to share or keep in git. It holds no game
data: everything else in the project's folder was made from the game (the ROM, `asm/`, `assets/`, the build files)
and stays on your computer.

```
src/<function>.c      each matched function's C, byte for byte
best/<function>.c     the closest attempt so far at each function that doesn't match yet
include/common.h      the shared header every function is compiled with (common_<part>.h: a part's own)
{MANIFEST}     the game's fingerprint (its SHA-1, never the game), the compiler and its flags, the
                      functions' layout and names, notes, instructions, what agents learned, who matched what
README.md             this file
```

## Continue on another computer

Open decompdecomp, choose **New project → Open a saved project**, then pick this folder (or paste its git
repository's address) and your own copy of the game. decompdecomp checks it's the same game and judges every saved
function again: a match counts because it matches, not because a file says so.
"""
    save_readme.update(net, proj, folder, readme, len(functions) - matched, details)
    return {"folder": str(folder), "matched": matched, "best": len(functions) - matched}


def _save_best(net, proj, folder: Path) -> dict:
    """A project-mode game's save: each open function's closest attempt as best/<function>.c, with how close it is,
    who wrote it and its source file in a comment at its top. Its matched C is in the project's own files."""
    names = {r["id"]: r["name"] for r in net.store.all("SELECT id, name FROM agents")}
    rows = net.store.all("SELECT name, unit, n_insns, best_source, best_score, best_agent_id, meta FROM functions WHERE "
                         "project_id=? AND status='open' AND best_source IS NOT NULL AND best_score IS NOT NULL "
                         "ORDER BY unit, name", proj["id"])
    shutil.rmtree(folder / "best", ignore_errors=True)
    (folder / "best").mkdir(parents=True, exist_ok=True)
    for r in rows:
        pct = 100 * (1 - r["best_score"] / max(1, (r["n_insns"] or 1) * 100))
        source = json.loads(r["meta"] or "{}").get("source") or r["unit"]
        (folder / "best" / f"{r['name']}.c").write_text(
            f"/* {r['name']}: the closest attempt so far, {percent_text(pct)} match, by "
            f"{names.get(r['best_agent_id'], '?')}; it belongs in {source} */\n\n" + r["best_source"].strip() + "\n",
            encoding="utf-8")
    readme = f"""# {proj['name']}: the closest attempts so far

Saved by [decompdecomp](https://github.com/n-delic/decompdecomp). Each file in `best/` is the closest C an agent wrote
for a function of the game that doesn't match byte for byte yet: its comment says how close,
who wrote it and which source file it belongs in. They compile, but they're not the original's code.

The matched functions aren't here: they're in the project's own source files (and its git history), where every
match was judged byte for byte. No game data is in this folder.
"""
    save_readme.update(net, proj, folder, readme, len(rows))
    return {"folder": str(folder), "matched": 0, "best": len(rows)}


def fetch(source: str, into: Path, log=print) -> Path:
    """A saved project from a folder, or cloned from a git repository's address into `into`."""
    path = Path(source).expanduser()
    if path.is_dir():
        return path
    if not source.startswith(("https://", "git@", "ssh://")):
        raise NetworkError(f"no folder at {source}, and it isn't a git repository's address")
    git = shutil.which("git")
    if not git:
        raise NetworkError("restoring from a git repository needs git: install it, or clone it yourself and pick the folder")
    log(f"cloning {source}")
    started = time.monotonic()
    proc = subprocess.Popen([git, "clone", "--depth", "1", source, str(into)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    check_cancel = getattr(log, "check_cancel", lambda: None)
    try:
        while True:
            check_cancel()
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started > GIT_TIMEOUT:
                    proc.kill()
                    proc.communicate()
                    raise NetworkError("git clone took too long; check the repository address and try again")
    except BaseException:
        stop_process_tree(proc)
        raise
    res = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    if res.returncode != 0:
        raise NetworkError(f"git couldn't clone it: {(res.stderr or res.stdout).strip()[-300:]}")
    return into


def restore(net, source: str, game: Path, log=print) -> dict:
    """Rebuild a project from a save (a folder or a git repository) and the user's own copy of the game: import it
    (or find it already imported), check it's the same game, then put back the layout, names, headers, settings and
    notes, and judge every saved function again. Returns the project and what came back."""
    log("checking the saved project and your game copy")
    with tempfile.TemporaryDirectory(prefix="decomp-restore-") as tmp:
        folder = fetch(source, Path(tmp) / "save", log)
        try:
            manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise NetworkError(f"{folder} isn't a decompdecomp save (no {MANIFEST} in it)") from None
        if manifest.get("format") != FORMAT:
            raise NetworkError("this save was written by another version of decompdecomp: update it, then try again")
        data = Path(game).expanduser()
        getattr(log, "check_cancel", lambda: None)()
        if consoles.identify(data).read_game(data, data.name).info["sha1"] != manifest["game"]["sha1"]:
            raise NetworkError(f"that's not the same game: this save is for {manifest['title']} "
                               f"({manifest['game'].get('file') or 'its original file'}); pick your copy of that one")
        log("importing and splitting your game copy")
        with compiler.cancellable(getattr(log, "check_cancel", lambda: None)):
            slug = net.import_rom(data, confirmed=True, name=data.name, log=log)
            getattr(log, "check_cancel", lambda: None)()
            return _apply(net, slug, folder, manifest, log) | {"slug": slug}


def _apply(net, slug: str, folder: Path, manifest: dict, log) -> dict:
    check_cancel = getattr(log, "check_cancel", lambda: None)
    proj = net._project(slug)
    pid = proj["id"]
    log("putting back the function splits and names")
    net.apply_remote_layout(slug, manifest.get("layout") or [], manifest.get("symbols") or [])

    def setup(cfg):
        if manifest.get("compiler") and toolchains.installed(manifest["compiler"].get("toolchain") or "") is not None:
            cfg["compiler"] = manifest["compiler"]
        ranges = manifest.get("compiler_ranges") or cfg.get("compiler_ranges") or []
        # a save can come from a computer whose project used a compiler decompdecomp doesn't have: keeping that range
        # would make every function in it fail to compile with the registry's "unknown toolchain" error (#118)
        kept, gone = [], []
        for r in ranges:
            if toolchains.known(str(r.get("toolchain") or "")):
                kept.append(r)
            else:
                gone.append(r)
        if gone:
            log("left out saved compiler ranges whose compiler decompdecomp doesn't have: " + ", ".join(
                f"0x{int(r.get('start') or 0):X}-0x{int(r.get('end') or 0):X} {r.get('toolchain')}" for r in gone))
        cfg["compiler_ranges"] = kept
        cfg["instructions"] = manifest.get("instructions") or cfg.get("instructions") or ""
        cfg["knowledge_auto"] = bool(manifest.get("knowledge_auto"))
    net._update_cfg(slug, setup)
    restorer, _ = net.local_agent("restored")
    with net.store.tx() as db:
        for unit, file in (manifest.get("headers") or {}).items():
            check_cancel()
            header = (folder / "include" / file).read_text(encoding="utf-8") if (folder / "include" / file).exists() else None
            if header is not None and header != net._context(pid, unit)["header"]:
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (pid, net._next_version(db, pid), header, restorer.id,
                                                     "restored from a save", time.time(), unit))
        for text in manifest.get("knowledge") or []:
            check_cancel()
            if not db.execute("SELECT 1 FROM knowledge WHERE project_id=? AND text=?", (pid, text)).fetchone():
                db.execute("INSERT INTO knowledge(project_id, text, status, created_at, decided_at) VALUES(?,?,?,?,?)",
                           (pid, text, "accepted", time.time(), time.time()))
    for note in manifest.get("notes") or []:
        check_cancel()
        try:
            net.add_note(net.local_agent(note["by"] or "restored")[0], slug, note["text"], note["function"])
        except NetworkError:
            continue  # its function is gone (a split the layout couldn't make again)
    with net.store.tx() as db:
        for item in manifest.get("set_aside") or []:
            row = db.execute("SELECT id, meta FROM functions WHERE project_id=? AND vram=? AND status='open'",
                             (pid, item["vram"])).fetchone()
            if row is not None:
                why = {k: v for k, v in item.items() if k in SET_ASIDE_META}
                db.execute("UPDATE functions SET status='asm', meta=? WHERE id=?",
                           (json.dumps({**json.loads(row["meta"] or "{}"), **why}), row["id"]))
    by_vram = {r["vram"]: r["name"] for r in net.store.all("SELECT vram, name FROM functions WHERE project_id=?", pid)}
    saved = manifest.get("functions") or {}
    matched, lost, best = 0, [], 0
    log(f"judging {len(saved)} saved functions again")
    for old_name, info in saved.items():
        getattr(log, "check_cancel", lambda: None)()
        log(f"checking {old_name}")
        kind = "src" if "matched_by" in info else "best"
        path = folder / kind / f"{old_name}.c"
        name = by_vram.get(info["vram"])
        if name is None or not path.exists():
            lost.append(old_name) if kind == "src" else None
            continue
        if net.store.one("SELECT status FROM functions WHERE project_id=? AND name=?", pid, name)["status"] == "matched":
            matched += kind == "src"
            continue
        agent, _ = net.local_agent((info.get("matched_by") or info.get("best_by") or "restored"))
        try:
            result = net.attempt(agent, name, path.read_text(encoding="utf-8"), slug)
        except NetworkError as e:
            log(f"{name}: {e}")
            result = {"matched": False}
        finally:
            with net.store.tx() as db:  # a saved attempt that didn't match leaves no claim behind
                db.execute("DELETE FROM claims WHERE agent_id=?", (agent.id,))
        if kind == "src":
            if result.get("matched") and info.get("readable"):  # its polished C matched again: it stays polished
                with net.store.tx() as db:
                    db.execute("UPDATE functions SET readable=1 WHERE project_id=? AND name=?", (pid, name))
            matched += bool(result.get("matched"))
            if not result.get("matched"):
                lost.append(name)
        else:
            best += 1
    log(f"restored: {matched} matched functions, {best} best attempts" + (f"; {len(lost)} no longer match" if lost else ""))
    return {"matched": matched, "best": best, "lost": lost}


class Autosave:
    """Every few minutes, each ROM project with something new since its last save is saved to its progress/ folder:
    copying ~/Decomp kept none of the work (it's in the app's database)."""

    def __init__(self, net):
        self.net = net
        self.last: dict[str, int] = {}
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="autosave").start()

    def _loop(self) -> None:
        while not self.stop.wait(AUTOSAVE_SECONDS):
            self.run_once()

    def run_once(self) -> list[str]:
        """Save every ROM project that changed since its last save (a new event). Returns the slugs saved."""
        saved = []
        for row in self.net.store.all("SELECT p.slug, (SELECT MAX(id) FROM events e WHERE e.project_id=p.id) AS last "
                                      "FROM projects p"):
            if row["last"] is None or self.last.get(row["slug"]) == row["last"]:
                continue
            try:
                if self.net.is_rom(self.net._project(row["slug"])):
                    save(self.net, row["slug"])
                    saved.append(row["slug"])
                self.last[row["slug"]] = row["last"]
            except Exception:  # never stands between the user and their work
                log.exception("saving %s failed", row["slug"])
        return saved
