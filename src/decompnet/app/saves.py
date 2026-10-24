"""A ROM project's work as a folder of plain files, and a project rebuilt from one: to move to another computer (the
owner changes Linux distributions now and then), to keep in git, or to start over after something broke.

The progress folder is authoritative; progress.py keeps the engine's database in step with edits and git merges.
It contains what people and agents made: the matched C, the best attempts, the shared headers, the names, the function
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
from contextlib import nullcontext
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
GIT_TIMEOUT = 300
log = logging.getLogger("decompdecomp")


def _header_file(unit: str) -> str:
    return "common.h" if not unit else f"common_{unit}.h"


def _source_text(text: str) -> str:
    # Leading blank lines can affect __LINE__, so saving must preserve them too.
    return text if text.endswith("\n") else text + "\n"


def default_folder(net, slug: str) -> Path:
    proj = net.project_row(slug)
    return Path(net.cfg(proj).get("progress_folder") or Path(proj["root"]) / SAVE_FOLDER)


def save(net, slug: str, folder: Path | None = None) -> dict:
    """Write the project's work to `folder` (default: progress/ in the project's folder). Returns what was saved."""
    proj = net._project(slug)
    folder = Path(folder) if folder else default_folder(net, slug)
    if not net.is_rom(proj):
        return _save_best(net, proj, folder)
    net.progress.sync(slug, force=True)
    files, result = _render(net, slug, folder)
    if folder == default_folder(net, slug):
        net.progress.sync(slug, force=True)
    else:
        _publish(folder, files)
    return result | {"folder": str(folder)}


def _publish(folder: Path, files: dict[str, str], previous: dict[str, str] | None = None,
             expected: dict[str, str] | None = None) -> bool:
    """Only change owned files whose contents changed; keep git and contributors' other files intact."""
    if expected is not None:
        for name in files.keys() | (previous or {}).keys():
            if name == "README.md":
                continue
            path = folder / name
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != expected.get(name):
                return False  # git or an editor wrote while the database was being rendered: read it next time
    for name in (previous or {}).keys() - files.keys():
        (folder / name).unlink(missing_ok=True)
    for name, text in files.items():
        path = folder / name
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    return True


def _render(net, slug: str, folder: Path | None = None) -> tuple[dict[str, str], dict]:
    proj = net._project(slug)
    cfg = net.cfg(proj)
    pid = proj["id"]
    names = {r["id"]: r["name"] for r in net.store.all("SELECT id, name FROM agents")}
    rows = net.store.all("SELECT name, vram, size, status, matched_source, matched_agent_id, best_source, best_score, "
                         "best_agent_id, readable FROM functions WHERE project_id=? ORDER BY vram", pid)
    files = {}
    functions = {}
    for r in rows:
        if r["status"] == "matched" and r["matched_source"]:
            files[f"src/{r['name']}.c"] = _source_text(r["matched_source"])
            functions[r["name"]] = {"vram": r["vram"], "matched_by": names.get(r["matched_agent_id"], ""),
                                    "readable": bool(r["readable"])}
        elif r["best_source"] and r["best_score"] is not None:
            files[f"best/{r['name']}.c"] = _source_text(r["best_source"])
            functions[r["name"]] = {"vram": r["vram"], "best_score": r["best_score"], "best_by": names.get(r["best_agent_id"], "")}
    units = {""} | net._unit_contexts(pid)
    for unit in sorted(units):
        files["include/" + _header_file(unit)] = net._context(pid, unit)["header"]
    notes = [{"function": r["fn"], "text": r["text"], "by": r["agent"] or ""} for r in net.store.all(
        "SELECT n.text, a.name AS agent, f.name AS fn FROM notes n LEFT JOIN agents a ON a.id=n.agent_id "
        "LEFT JOIN functions f ON f.id=n.function_id WHERE n.project_id=? ORDER BY n.id", pid)]
    knowledge = [r["text"] for r in net.store.all("SELECT text FROM knowledge WHERE project_id=? AND status='accepted' "
                                                  "ORDER BY id", pid)]
    manifest = {
        "format": FORMAT, "title": proj["name"], "platform": cfg.get("platform"),
        "game": {"sha1": cfg.get("rom", {}).get("sha1"), "file": cfg.get("rom", {}).get("file")},
        "compiler": cfg.get("compiler"), "compiler_ranges": cfg.get("compiler_ranges") or [],
        "instructions": cfg.get("instructions") or "", "knowledge": knowledge,
        "priority": cfg.get("priority") or [],
        "auto_compiler": cfg.get("auto_compiler"),
        "knowledge_auto": bool(cfg.get("knowledge_auto")), "headers": {u: _header_file(u) for u in sorted(units)},
        "functions": functions,
        "layout": [{"name": r["name"], "vram": r["vram"], "size": r["size"],
                    "status": "asm" if r["status"] == "asm" else "open"} for r in rows],
        "symbols": [dict(r) for r in net.store.all("SELECT name, address, size, kind FROM symbols WHERE project_id=?",
                                                    pid) if not AUTO_NAME.match(r["name"])],
        "notes": notes,
    }
    files[MANIFEST] = json.dumps(manifest, indent=1) + "\n"
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

## Collaborate

This folder is the source of truth. Edit its C, headers, names, notes or settings and pull or merge contributions
here. decompdecomp picks up changes automatically and judges changed code again. Deleted sources reopen their
functions. Keep git conflict markers out of these files; unresolved conflicts pause synchronization.
"""
    readme_folder = folder or default_folder(net, slug)
    readme_folder.mkdir(parents=True, exist_ok=True)
    save_readme.update(net, proj, readme_folder, readme, len(functions) - matched, details)
    return files, {"matched": matched, "best": len(functions) - matched}


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
        progress = getattr(net, "progress", None)
        with compiler.cancellable(getattr(log, "check_cancel", lambda: None)), \
                (progress.lock if progress is not None else nullcontext()):
            if progress is not None:
                progress.local.busy = True  # importing must not create a second progress folder before binding this one
            try:
                slug = net.import_rom(data, confirmed=True, name=data.name, log=log)
                getattr(log, "check_cancel", lambda: None)()
                # Keep a local checkout in place; keep a clone (including .git) after the temporary directory ends.
                if Path(source).expanduser().is_dir():
                    live = folder.resolve()
                else:
                    live = Path(net._project(slug)["root"]) / SAVE_FOLDER
                    if (live / ".git").exists():
                        raise NetworkError("This project already has a progress checkout. Pull changes there, or open that local folder.")
                    from .progress import _read
                    _publish(live, {}, _read(live))
                    shutil.copytree(folder, live, dirs_exist_ok=True)
                net._update_cfg(slug, lambda cfg: cfg.__setitem__("progress_folder", str(live)))
                if progress is None:
                    raise RuntimeError("restoring a saved project requires progress synchronization")
                progress.disk.pop(slug, None)
                progress.projected.pop(slug, None)
            finally:
                if progress is not None:
                    progress.local.busy = False
            progress.sync(slug, force=True, logger=log)
            return progress.results[slug] | {"slug": slug}


def _apply(net, slug: str, folder: Path, manifest: dict, log, files: dict[str, str] | None = None) -> dict:
    check_cancel = getattr(log, "check_cancel", lambda: None)
    proj = net._project(slug)
    pid = proj["id"]
    log("putting back the function splits and names")
    layout = manifest.get("layout") or []
    symbols = manifest.get("symbols") or []
    symbols = symbols + [{"name": f["name"], "address": f["vram"], "size": f["size"], "kind": "func"}
                         for f in layout if not any(s["address"] == f["vram"] for s in symbols)]
    _join_saved_layout(net, proj, layout)
    net.apply_remote_layout(slug, layout, symbols)

    def source(name):
        if files is not None:
            return files.get(name)
        path = folder / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def setup(cfg):
        if manifest.get("compiler") and toolchains.installed(manifest["compiler"].get("toolchain") or "") is not None:
            cfg["compiler"] = manifest["compiler"]
        cfg["compiler_ranges"] = manifest.get("compiler_ranges") or []
        cfg["instructions"] = manifest.get("instructions") or ""
        cfg["priority"] = manifest.get("priority") or []
        cfg["auto_compiler"] = manifest.get("auto_compiler")
        cfg["knowledge_auto"] = bool(manifest.get("knowledge_auto"))
    net._update_cfg(slug, setup)
    if manifest.get("title"):
        with net.store.tx() as db:
            db.execute("UPDATE projects SET name=? WHERE id=?", (manifest["title"], pid))
    restorer, _ = net.local_agent("restored")
    with net.store.tx() as db:
        for unit, file in (manifest.get("headers") or {}).items():
            check_cancel()
            header = source("include/" + file)
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
    by_vram = {r["vram"]: r["name"] for r in net.store.all("SELECT vram, name FROM functions WHERE project_id=?", pid)}
    saved = manifest.get("functions") or {}
    matched, lost, best = 0, [], 0
    log(f"judging {len(saved)} saved functions again")
    for old_name, info in saved.items():
        getattr(log, "check_cancel", lambda: None)()
        log(f"checking {old_name}")
        kind = "src" if "matched_by" in info else "best"
        text = source(f"{kind}/{old_name}.c")
        name = by_vram.get(info["vram"])
        if name is None or text is None:
            lost.append(old_name) if kind == "src" else None
            continue
        current = net.store.one("SELECT status,best_source FROM functions WHERE project_id=? AND name=?", pid, name)
        if current["status"] == "matched":
            matched += kind == "src"
            continue
        if kind == "best" and current["best_source"] == text:
            best += 1
            continue
        credit, _ = net.local_agent((info.get("matched_by") or info.get("best_by") or "restored"))
        agent, _ = net.local_agent("progress-judge")
        try:
            result = net.attempt(agent, name, text, slug)
        except NetworkError as e:
            log(f"{name}: {e}")
            result = {"matched": False}
        finally:
            with net.store.tx() as db:  # a saved attempt that didn't match leaves no claim behind
                db.execute("DELETE FROM claims WHERE agent_id=? AND function_id IN "
                           "(SELECT id FROM functions WHERE project_id=? AND name=?)", (agent.id, pid, name))
        with net.store.tx() as db:
            db.execute("UPDATE functions SET matched_agent_id=? WHERE project_id=? AND name=? AND matched_agent_id=?",
                       (credit.id, pid, name, agent.id))
            db.execute("UPDATE functions SET best_agent_id=? WHERE project_id=? AND name=? AND best_agent_id=?",
                       (credit.id, pid, name, agent.id))
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


def _join_saved_layout(net, proj, layout: list[dict]) -> None:
    """Switching git branches can undo a split. Join its stored assembly before applying the new splits."""
    from dataclasses import replace

    pid = proj["id"]
    for entry in layout:
        start, end = entry["vram"], entry["vram"] + entry["size"]
        rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND vram>=? AND vram<? ORDER BY vram",
                             pid, start, end)
        if len(rows) < 2 or rows[0]["vram"] != start:
            continue
        parts = [net._parse_asm(r["name"], r["asm"], r["unit"]) for r in rows]
        lines = {line.vram: line for part in parts for line in part.lines if line.vram < end}
        missing = set(range(start, end, 4)) - lines.keys()
        if missing:
            from ..rom.split import AsmLine
            read = net.reader(proj)
            for address in sorted(missing):
                # split_function trims padding from the head; undoing that split must put its original NOPs back.
                if read(address, 4) != b"\0\0\0\0":
                    break
                previous = lines[max(at for at in lines if at < address)]
                lines[address] = AsmLine(previous.rom + address - previous.vram, address, 0, "nop", "")
        if sorted(lines) != list(range(start, end, 4)):
            raise NetworkError("The saved layout crosses a gap in this analysis. Reimport the game before continuing.")
        joined = replace(parts[0], lines=[lines[address] for address in sorted(lines)],
                         labels=set().union(*(part.labels for part in parts)))
        target = net._make_target(proj, joined, net.symbols(pid))
        with net.store.tx() as db:
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,seed=NULL WHERE id=?",
                       (joined.size, len(joined.lines), target.code, json.dumps(target.to_json()),
                        net._asm_source(joined), rows[0]["id"]))
            for tail in rows[1:]:
                for table in ("claims", "attempts", "twin_misses"):
                    db.execute(f"DELETE FROM {table} WHERE function_id=?", (tail["id"],))
                db.execute("DELETE FROM waits WHERE function_id=? OR needs_id=?", (tail["id"], tail["id"]))
                db.execute("UPDATE notes SET function_id=? WHERE function_id=?", (rows[0]["id"], tail["id"]))
                db.execute("DELETE FROM functions WHERE id=?", (tail["id"],))
                db.execute("DELETE FROM symbols WHERE project_id=? AND address=? AND kind='func'", (pid, tail["vram"]))
        net._syms.pop(pid, None)


class Autosave:
    """Follow local work and contributions in progress/, including while no project window is open."""

    def __init__(self, net):
        self.net = net
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="autosave").start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            self.run_once()
            self.stop.wait(1)

    def run_once(self) -> list[str]:
        """Synchronize every ROM project, including file changes that have no database event."""
        saved = []
        for row in self.net.store.all("SELECT slug FROM projects"):
            try:
                if self.net.progress.sync(row["slug"], force=True):
                    saved.append(row["slug"])
            except Exception:  # never stands between the user and their work
                log.exception("saving %s failed", row["slug"])
        return saved
