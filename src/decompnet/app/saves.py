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
SET_ASIDE_META = ("asm_code", "not_c", "asm_twin")  # why an agent or user set a function aside
GIT_TIMEOUT = 300
log = logging.getLogger("decompdecomp")


def _header_file(unit: str) -> str:
    return "common.h" if not unit else f"common_{unit}.h"


def _source_text(text: str) -> str:
    # Leading blank lines can affect __LINE__, so saving must preserve them too.
    return text if text.endswith("\n") else text + "\n"


def default_folder(net, slug: str) -> Path:
    """The project's progress folder. Reads the project row rather than `net._project`, which synchronizes: a save to
    another folder must not be stopped by the default folder's unresolved git merge (#101 review)."""
    proj = net.store.one("SELECT * FROM projects WHERE slug=?", slug)
    if proj is None:
        raise NetworkError(f"no project {slug!r} here: check its name, or whether it was removed")
    return Path(net.cfg(proj).get("progress_folder") or Path(proj["root"]) / SAVE_FOLDER)


def save(net, slug: str, folder: Path | None = None) -> dict:
    """Write the project's work to `folder` (default: progress/ in the project's folder). Returns what was saved.

    A folder chosen here is written from the database alone: the default progress folder can be in the middle of a
    git merge or a failed write, and "Save now" elsewhere must still get the work out."""
    default = default_folder(net, slug)
    target = Path(folder) if folder is not None else default
    try:
        chosen = target.resolve() != default.resolve()
    except OSError:  # a path that can't be resolved (a broken link): compare it as written
        chosen = target != default
    progress = getattr(net, "progress", None)
    was_busy = getattr(progress.local, "busy", False) if progress is not None else False
    if chosen and progress is not None:
        progress.local.busy = True  # reading the project must not apply the default folder
    try:
        proj = net._project(slug)
        _check_save_paths(target, chosen=chosen or bool(net.cfg(proj).get("progress_folder")))
        if not net.is_rom(proj):
            return _save_best(net, proj, target)
        if chosen:
            files, result = _render(net, slug, target, chosen=True)
            previous = _read_owned(target)
            if not _publish(target, files, previous, chosen=True):
                raise NetworkError("The save folder changed while it was being written. Save again after the change finishes.")
            _remember_revision(net, slug, json.loads(files[MANIFEST]))
        else:
            net.progress.sync(slug, force=True)
            manifest_path = target / MANIFEST
            manifest_before = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None
            files, result = _render(net, slug, target)
            net.progress.sync(slug, force=True)
            # A sync writes the manifest only when the database changed. A save also records when it was saved, so a
            # first sync after a database backup can tell this snapshot's age (#101 review).
            current = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None
            if current == manifest_before:
                if not _publish(target, {MANIFEST: files[MANIFEST]}, expected={MANIFEST: manifest_before},
                               chosen=bool(net.cfg(proj).get("progress_folder"))):
                    raise NetworkError("The save folder changed while it was being written. Save again after the change finishes.")
                _remember_revision(net, slug, json.loads(files[MANIFEST]))
            else:
                net.progress.sync(slug, force=True)  # a contributor wrote meanwhile: apply it, don't overwrite it
        return result | {"folder": str(target)}
    finally:
        if chosen and progress is not None:
            progress.local.busy = was_busy


def _publish(folder: Path, files: dict[str, str], previous: dict[str, str] | None = None,
             expected: dict[str, str] | None = None, *, chosen: bool = False) -> bool:
    """Only change owned files whose contents changed; keep git and contributors' other files intact.

    `chosen`: the progress folder is the one the user saved to or configured, so a link at its own path is theirs to
    keep; links inside it are still refused."""
    names = files.keys() | (previous or {}).keys()
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
            raise NetworkError("Progress contains an unsafe file path. Fix its manifest before saving again.")
        if name != MANIFEST and name != "README.md" and not name.startswith(("src/", "best/", "include/")):
            raise NetworkError("Progress contains an unsupported file path. Fix its manifest before saving again.")
        target = folder / path
        for parent in (target, *target.parents):
            if parent == folder.parent:
                break
            if parent == folder and chosen:
                continue
            if parent.is_symlink():
                raise NetworkError("Progress paths cannot use symbolic links. Replace the link and try again.")
        if target.exists() and not target.is_file():
            raise NetworkError("Progress files must be regular files. Replace the conflicting path and try again.")
    if expected is not None:
        for name in names:
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


def _manifest_revision(manifest: dict) -> int:
    """A manifest's revision; 0 for saves written before manifests had one."""
    value = manifest.get("revision")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _stored_revision(cfg: dict) -> int:
    """The highest revision this database has written for its project's progress folder."""
    value = cfg.get("progress_revision")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _next_revision(net, proj, folder: Path) -> int:
    """One more than the highest revision this project or the folder has seen.

    A revision tells a first sync without `progress_state` (a database restored from backup, a project copied without
    .decomp) whether the folder is an older snapshot than the database's own work (#101 review)."""
    revision = _stored_revision(net.cfg(proj))
    try:
        previous = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        revision = max(revision, _manifest_revision(previous))
    except (OSError, ValueError, TypeError):
        pass
    return revision + 1


def _remember_revision(net, slug: str, manifest: dict) -> None:
    """Record the revision of a manifest this project just wrote, for the next first sync without a baseline."""
    written = _manifest_revision(manifest)
    if written <= 0:
        return
    with net.store.tx() as db:
        row = db.execute("SELECT id,config FROM projects WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return
        config = json.loads(row["config"])
        if written > _stored_revision(config):
            config["progress_revision"] = written
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(config), row["id"]))


def _check_progress_folder(folder: Path, *, chosen: bool = False) -> None:
    """Reject links or wrong types before README, the progress card or a source write can follow one outside the
    progress folder. `chosen`: the user picked this folder itself in Settings or the Save dialog, so a link at its
    own path is theirs to keep; links inside it are never followed."""
    if folder.is_symlink():
        if not chosen:
            raise NetworkError("The progress folder is a symbolic link to another place. Replace it with a real folder, "
                               "or choose that place in the project's Settings, and try again.")
        if not folder.is_dir():
            raise NetworkError("The progress folder's link points to a place that isn't a folder. "
                               "Fix the link and try again.")
    for name in ("README.md", save_readme.CARD):
        path = folder / name
        if path.is_symlink():
            raise NetworkError("Progress paths cannot use symbolic links. Replace the link and try again.")
        if path.exists() and path.is_dir():
            raise NetworkError("The progress folder contains a path with the wrong type. Move it and try again.")
    for name in ("src", "best", "include"):
        path = folder / name
        if path.is_symlink():
            raise NetworkError("Progress folders must be regular directories. Replace the symbolic link and try again.")
        if path.exists() and not path.is_dir():
            raise NetworkError("The progress folder contains a path with the wrong type. Move it and try again.")


def _check_save_paths(folder: Path, *, chosen: bool = False) -> None:
    """Reject links before README, the map or a save's files can follow one outside the selected save folder."""
    _check_progress_folder(folder, chosen=chosen)
    for name in ("src", "best", "include"):
        path = folder / name
        if path.is_dir():
            for child in path.iterdir():
                if child.is_symlink() or not child.is_file():
                    raise NetworkError("Progress files must be regular files. Replace the conflicting path and try again.")


def _read_owned(folder: Path) -> dict[str, str]:
    """Read only the generated files a previous manifest says this save owns."""
    if not (folder / MANIFEST).exists():
        return {}
    from .progress import _read

    previous = _read(folder)
    try:
        manifest = json.loads(previous[MANIFEST])
        functions = manifest["functions"]
        headers = manifest["headers"]
        if not isinstance(functions, dict) or not isinstance(headers, dict):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise NetworkError("Fix the saved project's manifest before replacing its files.") from None
    owned = {MANIFEST}
    owned.update(f"src/{name}.c" if "matched_by" in info else f"best/{name}.c"
                 for name, info in functions.items() if isinstance(info, dict))
    owned.update("include/" + file for file in headers.values() if isinstance(file, str))
    return {name: text for name, text in previous.items() if name in owned}


def _function_rows(net, project_id: int, *, sources: bool) -> list:
    """The columns a render reads. Without `sources` only whether a file exists is selected: a manifest needs the
    layout and who matched it, not the megabytes of C that make the files."""
    exists = ("CASE WHEN matched_source IS NOT NULL AND matched_source != '' THEN 1 ELSE 0 END AS has_matched, "
              "CASE WHEN best_source IS NOT NULL AND best_source != '' THEN 1 ELSE 0 END AS has_best ")
    if sources:
        return net.store.all(
            "SELECT name, vram, size, status, matched_source, matched_agent_id, best_source, best_score, "
            f"best_agent_id, readable, meta, {exists}FROM functions WHERE project_id=? ORDER BY vram", project_id)
    return net.store.all(
        "SELECT name, vram, size, status, matched_agent_id, best_score, best_agent_id, readable, meta, "
        f"{exists}FROM functions WHERE project_id=? ORDER BY vram", project_id)


def _manifest_from_rows(net, proj, cfg: dict, rows, folder: Path) -> tuple[dict, dict, set[str]]:
    """The progress manifest and its function index from function rows (with or without their source text)."""
    pid = proj["id"]
    names = {r["id"]: r["name"] for r in net.store.all("SELECT id, name FROM agents")}
    functions = {}
    for r in rows:
        if r["status"] == "matched" and r["has_matched"]:
            functions[r["name"]] = {"vram": r["vram"], "matched_by": names.get(r["matched_agent_id"], ""),
                                    "readable": bool(r["readable"])}
        elif r["has_best"] and r["best_score"] is not None:
            functions[r["name"]] = {"vram": r["vram"], "best_score": r["best_score"],
                                    "best_by": names.get(r["best_agent_id"], "")}
    units = {""} | net._unit_contexts(pid)
    notes = [{"function": r["fn"], "text": r["text"], "by": r["agent"] or ""} for r in net.store.all(
        "SELECT n.text, a.name AS agent, f.name AS fn FROM notes n LEFT JOIN agents a ON a.id=n.agent_id "
        "LEFT JOIN functions f ON f.id=n.function_id WHERE n.project_id=? ORDER BY n.id", pid)]
    knowledge = [r["text"] for r in net.store.all("SELECT text FROM knowledge WHERE project_id=? AND status='accepted' "
                                                  "ORDER BY id", pid)]
    manifest = {
        "format": FORMAT, "saved_at": time.time(), "revision": _next_revision(net, proj, folder),
        "title": proj["name"], "platform": cfg.get("platform"),
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
        # what's set aside and why: agents' verdicts that no C makes it, the user's, a boundary report's leftover
        # (the import sets aside again what it finds by itself)
        "set_aside": [{"name": r["name"], "vram": r["vram"],
                       **{k: v for k, v in json.loads(r["meta"] or "{}").items() if k in SET_ASIDE_META}}
                      for r in rows if r["status"] == "asm"],
    }
    return manifest, functions, units


def _readme_key(manifest: dict) -> tuple:
    """What the README's counts and the progress map show; unchanged means no need to rebuild either."""
    states = []
    functions = manifest.get("functions") or {}
    for entry in manifest.get("layout") or []:
        name = entry.get("name")
        info = functions.get(name) or {}
        if entry.get("status") == "asm":
            state = "aside"
        elif "matched_by" in info:
            state = "matched"
        elif info.get("best_score") is not None:
            state = "attempt"
        else:
            state = "open"
        states.append((name, state))
    compiler = (manifest.get("compiler") or {})
    compiler_name = compiler.get("toolchain") if isinstance(compiler, dict) else str(compiler)
    return (manifest.get("title"), manifest.get("platform"), compiler_name or "not recorded", tuple(states))


def _write_readme(net, proj, folder: Path, manifest: dict, functions: dict) -> None:
    """Refresh the progress card and counts. Only the files whose text or map changed are written."""
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    cfg = net.cfg(proj)
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
    folder.mkdir(parents=True, exist_ok=True)
    save_readme.update(net, proj, folder, readme, len(functions) - matched, details)


def _source_files(rows) -> dict[str, str]:
    """Each row's progress file: its match's C, else its best attempt's."""
    files = {}
    for r in rows:
        if r["status"] == "matched" and r["has_matched"]:
            files[f"src/{r['name']}.c"] = _source_text(r["matched_source"])
        elif r["has_best"] and r["best_score"] is not None:
            files[f"best/{r['name']}.c"] = _source_text(r["best_source"])
    return files


def _render(net, slug: str, folder: Path | None = None, *, chosen: bool | None = None,
            readme_key: tuple | None = None) -> tuple[dict[str, str], dict]:
    proj = net._project(slug)
    cfg = net.cfg(proj)
    readme_folder = folder or default_folder(net, slug)
    if chosen is None:
        chosen = bool(cfg.get("progress_folder"))
    _check_progress_folder(readme_folder, chosen=chosen)
    pid = proj["id"]
    rows = _function_rows(net, pid, sources=True)
    manifest, functions, units = _manifest_from_rows(net, proj, cfg, rows, readme_folder)
    files = _source_files(rows)
    for unit in sorted(units):
        files["include/" + _header_file(unit)] = net._context(pid, unit)["header"]
    files[MANIFEST] = json.dumps(manifest, indent=1) + "\n"
    if readme_key is None or _readme_key(manifest) != readme_key:
        _write_readme(net, proj, readme_folder, manifest, functions)
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    return files, {"matched": matched, "best": len(functions) - matched}


def _render_partial(net, proj, folder: Path, names: list[str], *, headers: bool = False,
                    readme_key: tuple | None = None) -> tuple[dict[str, str], dict]:
    """Render the manifest and only the named functions' files (plus every header when one changed).

    A match, a rename or a header edit touches a handful of files; rebuilding the other 17,999 sources' text is what
    made one database change take seconds on a big game. The manifest is always rebuilt, because it carries the
    revision, the layout and who matched what."""
    cfg = net.cfg(proj)
    pid = proj["id"]
    rows = _function_rows(net, pid, sources=False)
    manifest, functions, units = _manifest_from_rows(net, proj, cfg, rows, folder)
    files = {MANIFEST: json.dumps(manifest, indent=1) + "\n"}
    if headers:
        for unit in sorted(units):
            files["include/" + _header_file(unit)] = net._context(pid, unit)["header"]
    if names:
        for start in range(0, len(names), 400):
            chunk = names[start:start + 400]
            selected = net.store.all(
                "SELECT name, vram, size, status, matched_source, matched_agent_id, best_source, best_score, "
                "best_agent_id, readable, meta, "
                "CASE WHEN matched_source IS NOT NULL AND matched_source != '' THEN 1 ELSE 0 END AS has_matched, "
                "CASE WHEN best_source IS NOT NULL AND best_source != '' THEN 1 ELSE 0 END AS has_best "
                f"FROM functions WHERE project_id=? AND name IN ({','.join('?' * len(chunk))})", pid, *chunk)
            files.update(_source_files(selected))
    if readme_key is None or _readme_key(manifest) != readme_key:
        _write_readme(net, proj, folder, manifest, functions)
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    return files, {"matched": matched, "best": len(functions) - matched}


def _save_best(net, proj, folder: Path) -> dict:
    """A project-mode game's save: each open function's closest attempt as best/<function>.c, with how close it is,
    who wrote it and its source file in a comment at its top. Its matched C is in the project's own files."""
    names = {r["id"]: r["name"] for r in net.store.all("SELECT id, name FROM agents")}
    rows = net.store.all("SELECT name, unit, n_insns, best_source, best_score, best_agent_id, meta FROM functions WHERE "
                         "project_id=? AND status='open' AND best_source IS NOT NULL AND best_score IS NOT NULL "
                         "ORDER BY unit, name", proj["id"])
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for r in rows:
        pct = 100 * (1 - r["best_score"] / max(1, (r["n_insns"] or 1) * 100))
        source = json.loads(r["meta"] or "{}").get("source") or r["unit"]
        files[f"best/{r['name']}.c"] = (
            f"/* {r['name']}: the closest attempt so far, {percent_text(pct)} match, by "
            f"{names.get(r['best_agent_id'], '?')}; it belongs in {source} */\n\n" + r["best_source"].strip() + "\n")
    readme = f"""# {proj['name']}: the closest attempts so far

Saved by [decompdecomp](https://github.com/n-delic/decompdecomp). Each file in `best/` is the closest C an agent wrote
for a function of the game that doesn't match byte for byte yet: its comment says how close,
who wrote it and which source file it belongs in. They compile, but they're not the original's code.

The matched functions aren't here: they're in the project's own source files (and its git history), where every
match was judged byte for byte. No game data is in this folder.
"""
    save_readme.update(net, proj, folder, readme, len(rows))
    best_dir = folder / "best"
    if best_dir.is_symlink():
        raise NetworkError("Progress paths cannot use symbolic links. Replace the link and try again.")
    prior = {}
    for path in best_dir.glob("*.c"):
        if path.is_symlink() or not path.is_file():
            raise NetworkError("Progress files must be regular files. Replace the conflicting path and try again.")
        prior[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
    if not _publish(folder, files, prior):
        raise NetworkError("The save folder changed while it was being written. Save again after the change finishes.")
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
    from .progress import _manifest, _read

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
        game_sha1 = consoles.identify(data).read_game(data, data.name).info["sha1"]
        if game_sha1 != manifest["game"]["sha1"]:
            raise NetworkError(f"that's not the same game: this save is for {manifest['title']} "
                               f"({manifest['game'].get('file') or 'its original file'}); pick your copy of that one")
        files = _read(folder)
        manifest = _manifest(files, {"rom": {"sha1": game_sha1}})
        log("importing and splitting your game copy")
        existing = net.store.all("SELECT slug,root,config FROM projects")
        source_folder = Path(source).expanduser()
        same_game = []
        for row in existing:
            cfg = json.loads(row["config"])
            if cfg.get("rom", {}).get("sha1") != game_sha1:
                continue
            linked = Path(cfg.get("progress_folder") or Path(row["root"]) / SAVE_FOLDER)
            same_game.append((row, linked))
        same_linked_save = (len(same_game) == 1 and source_folder.is_dir()
                            and source_folder.resolve() == same_game[0][1].resolve())
        if same_game and not same_linked_save:
            raise NetworkError("This game is already open in this decompdecomp. Open its project to keep working, or restore on another computer.")
        progress = getattr(net, "progress", None)
        existing_slug = same_game[0][0]["slug"] if same_linked_save else None
        import_lock = progress._project_lock(existing_slug) if progress is not None and existing_slug else nullcontext()
        with compiler.cancellable(getattr(log, "check_cancel", lambda: None)), import_lock:
            if progress is not None:
                progress.local.busy = True  # import hooks must not synchronize before the save folder is bound
            try:
                slug = net.import_rom(data, confirmed=True, name=data.name, log=log)
            finally:
                if progress is not None:
                    progress.local.busy = False
            getattr(log, "check_cancel", lambda: None)()
            imported = slug not in {row["slug"] for row in existing}
            try:
                if progress is None:
                    raise RuntimeError("restoring a saved project requires progress synchronization")
                # Import may create a new project; from here on, sync and rebind take the same lock.
                with progress._project_lock(slug):
                    progress.results.pop(slug, None)
                    progress.local.busy = True
                    try:
                        # Keep a local checkout in place; keep a clone (including .git) after the temporary directory ends.
                        if Path(source).expanduser().is_dir():
                            live = folder.resolve()
                        else:
                            live = Path(net.store.one("SELECT root FROM projects WHERE slug=?", slug)["root"]) / SAVE_FOLDER
                            if (live / ".git").exists():
                                raise NetworkError("This project already has a progress checkout. Pull changes there, or open that local folder.")
                            _publish(live, {}, _read(live))
                            shutil.copytree(folder, live, dirs_exist_ok=True)
                        net._update_cfg(slug, lambda cfg: cfg.__setitem__("progress_folder", str(live)))
                        project_id = net.store.one("SELECT id FROM projects WHERE slug=?", slug)["id"]
                        if not same_linked_save:
                            with net.store.tx() as db:
                                db.execute("DELETE FROM progress_state WHERE project_id=?", (project_id,))
                            progress.baselines.pop(project_id, None)
                        progress.disk.pop(slug, None)
                        progress.projected.pop(slug, None)
                        progress.signatures.pop(slug, None)
                        progress.checked.pop(slug, None)
                        progress.sync_changes.pop(slug, None)
                    finally:
                        progress.local.busy = False
                    if not same_linked_save:
                        progress.adopting.add(slug)
                    try:
                        progress.sync(slug, force=True, logger=log)
                    finally:
                        progress.adopting.discard(slug)
                    result = progress.results.pop(slug, None) or _restore_counts(net, slug)
                    return result | {"slug": slug}
            except Exception:
                # #101 review: importing and splitting happened before the save could be applied. A restore that
                # fails (a missing compiler, a cancelled judge) leaves no half-open project, as a failed room join
                # doesn't; a project that was here before stays.
                if imported:
                    _discard_imported(net, slug, progress)
                raise


def _discard_imported(net, slug: str, progress=None) -> None:
    """Remove a project a failed restore imported, without letting its half-applied folder synchronize on the way."""
    row = net.store.one("SELECT id,root FROM projects WHERE slug=?", slug)
    if row is None:
        return
    previous = getattr(progress.local, "busy", False) if progress is not None else False
    if progress is not None:
        progress.local.busy = True
    removed = False
    try:
        net.remove_project(slug)
        removed = True
    except Exception:  # cleanup must not replace the error that made the restore fail
        log.exception("could not remove the project a failed restore imported")
    finally:
        if progress is not None:
            progress.local.busy = previous
        if removed:
            shutil.rmtree(Path(row["root"]), ignore_errors=True)


def _restore_counts(net, slug: str) -> dict:
    """Counts for a restore whose linked save already matches the project's known state."""
    proj = net.store.one("SELECT id FROM projects WHERE slug=?", slug)
    rows = net.store.all("SELECT status,matched_source,best_source,best_score FROM functions WHERE project_id=?", proj["id"])
    return {
        "matched": sum(row["status"] == "matched" and bool(row["matched_source"]) for row in rows),
        "best": sum(row["status"] != "matched" and row["best_source"] is not None
                    and row["best_score"] is not None for row in rows),
        "lost": [],
    }


def _saved_source(folder: Path, files: dict[str, str] | None, name: str) -> str | None:
    if files is not None:
        return files.get(name)
    path = folder / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _apply_context(net, slug: str, folder: Path, manifest: dict, log,
                   files: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Put back everything a save changes before its C is judged: layout, names, settings, notes, set-aside."""
    check_cancel = getattr(log, "check_cancel", lambda: None)
    proj = net._project(slug)
    pid = proj["id"]
    log("putting back the function splits and names")
    layout = manifest.get("layout") or []
    symbols = manifest.get("symbols") or []
    known_symbols = {s["name"] for s in symbols}
    symbols = symbols + [{"name": f["name"], "address": f["vram"], "size": f["size"], "kind": "func"}
                         for f in layout if f["name"] not in known_symbols]
    if _layout_matches_db(net, pid, layout):
        log("the saved layout already matches this project")
    else:
        _join_saved_layout(net, proj, layout, log)
        net.apply_remote_layout(slug, layout, symbols)
    with net.store.tx() as db:
        for item in manifest.get("set_aside") or []:
            # A synced layout can already have set it aside before this runs; its reason still has to come back.
            row = _set_aside_row(db, pid, item)
            if row is None:
                continue
            why = {key: value for key, value in item.items() if key in SET_ASIDE_META}
            meta = json.loads(row["meta"] or "{}") | why
            db.execute("UPDATE functions SET status='asm',meta=? WHERE id=?", (json.dumps(meta), row["id"]))

    def setup(cfg):
        if manifest.get("compiler") and toolchains.installed(manifest["compiler"].get("toolchain") or "") is not None:
            cfg["compiler"] = manifest["compiler"]
        ranges = manifest.get("compiler_ranges") or []
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
            header = _saved_source(folder, files, "include/" + file)
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
            net.add_note(net.local_agent(note.get("by") or "restored")[0], slug, note["text"], note.get("function"))
        except NetworkError:
            continue  # its function is gone (a split the layout couldn't make again)
    with net.store.tx() as db:
        for item in manifest.get("set_aside") or []:
            row = _set_aside_row(db, pid, item)
            if row is not None:
                why = {k: v for k, v in item.items() if k in SET_ASIDE_META}
                db.execute("UPDATE functions SET status='asm', meta=? WHERE id=?",
                           (json.dumps({**json.loads(row["meta"] or "{}"), **why}), row["id"]))
    by_name = {r["name"]: r for r in net.store.all("SELECT name,vram FROM functions WHERE project_id=?", pid)}
    return by_name, manifest.get("functions") or {}


def _judge_saved(net, slug: str, folder: Path, manifest: dict, by_name: dict, saved: dict, log,
                 files: dict[str, str] | None = None, judge_code: bool = True) -> dict:
    check_cancel = getattr(log, "check_cancel", lambda: None)
    pid = net._project(slug)["id"]
    by_vram: dict[int, list[str]] = {}
    for name, row in by_name.items():
        by_vram.setdefault(row["vram"], []).append(name)
    matched, lost, best = 0, [], 0
    log(f"judging {len(saved)} saved functions again")
    for old_name, info in saved.items():
        check_cancel()
        log(f"checking {old_name}")
        kind = "src" if "matched_by" in info else "best"
        text = _saved_source(folder, files, f"{kind}/{old_name}.c")
        row = by_name.get(old_name)
        name = old_name if row is not None and row["vram"] == info["vram"] else None
        if name is None:
            candidates = by_vram.get(info["vram"], [])
            name = candidates[0] if len(candidates) == 1 else None
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
        if not judge_code:
            # A pull that changed only the manifest's metadata applies its settings, notes and layout, but does not
            # compile every saved attempt again (the folder's C did not change).
            if kind == "src":
                matched += current["status"] == "matched"
            else:
                best += 1
            continue
        credit, _ = net.local_agent((info.get("matched_by") or info.get("best_by") or "restored"))
        agent, _ = net.local_agent("progress-judge")
        try:
            result = net.attempt(agent, name, text, slug)
        except (NetworkError, toolchains.ToolchainError) as e:
            # A compiler that isn't here (an unknown one can never be installed) must not stop the other saved work
            # from coming back; the function stays open for when the user picks one (#118).
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


def _apply(net, slug: str, folder: Path, manifest: dict, log, files: dict[str, str] | None = None,
           judge_code: bool = True) -> dict:
    by_name, saved = _apply_context(net, slug, folder, manifest, log, files=files)
    return _judge_saved(net, slug, folder, manifest, by_name, saved, log, files=files, judge_code=judge_code)


def _layout_matches_db(net, project_id: int, layout: list[dict]) -> bool:
    """Whether every layout entry is the database's own function, by name, address and size.

    A folder this app saved from this database has such a layout; applying it again must not join or split anything,
    and same-address overlay functions are different functions, not split pieces."""
    rows = net.store.all("SELECT name,vram,size FROM functions WHERE project_id=?", project_id)
    if len(rows) != len(layout):
        return False
    by_name = {row["name"]: row for row in rows}
    return all(by_name.get(entry["name"]) is not None
               and by_name[entry["name"]]["vram"] == entry["vram"]
               and by_name[entry["name"]]["size"] == entry["size"] for entry in layout)


def _set_aside_row(db, project_id: int, item: dict):
    """The function a saved set-aside entry names, never another overlay's function at the same address."""
    row = db.execute("SELECT id,meta FROM functions WHERE project_id=? AND name=? AND vram=? "
                     "AND status IN ('open','asm')", (project_id, item["name"], item["vram"])).fetchone()
    if row is not None:
        return row
    rows = db.execute("SELECT id,meta FROM functions WHERE project_id=? AND vram=? "
                      "AND status IN ('open','asm') ORDER BY id", (project_id, item["vram"])).fetchall()
    return rows[0] if len(rows) == 1 else None


def _join_saved_layout(net, proj, layout: list[dict], report=None) -> None:
    """Switching git branches can undo a split. Join its stored assembly before applying the new splits, keeping the
    tails' attempts on the joined function: they are work agents did, not something a layout change may delete.

    A join is planned for the layout entry's own function (same name and address, and only pieces in its own source
    unit), so two overlays that share an address are never joined. Every plan is checked before the first write; a gap
    or a matched function among the pieces refuses the whole layout instead of deleting work."""
    from dataclasses import replace

    report = report or (lambda line: None)
    pid = proj["id"]
    plans = []
    for entry in layout:
        start, end = entry["vram"], entry["vram"] + entry["size"]
        candidates = net.store.all("SELECT * FROM functions WHERE project_id=? AND vram=? ORDER BY id", pid, start)
        head = next((row for row in candidates if row["name"] == entry["name"]), None)
        if head is None:
            if len(candidates) != 1:
                continue  # an ambiguous overlay address (or a rename): never guess which function is meant
            head = candidates[0]
        if (head["size"] or 0) >= entry["size"]:
            continue  # already the saved function: there is no split to undo
        rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND vram>=? AND vram<? AND unit=? "
                             "ORDER BY vram, id", pid, start, end, head["unit"])
        if len(rows) < 2 or rows[0]["id"] != head["id"]:
            continue
        tails = rows[1:]
        if any(tail["status"] == "matched" or tail["matched_source"] for tail in tails):
            raise NetworkError(
                f"The saved layout would join a function that already matches at 0x{start:08X}. "
                "Reimport the game before continuing; nothing was changed.")
        parts = [net._parse_asm(row["name"], row["asm"], row["unit"]) for row in rows]
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
        plans.append((head, tails, joined, target))
    if not plans:
        return
    with net.store.tx() as db:
        for head, tails, joined, target in plans:
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,seed=NULL,attempts=attempts+? "
                       "WHERE id=?",
                       (joined.size, len(joined.lines), target.code, json.dumps(target.to_json()),
                        net._asm_source(joined), sum(tail["attempts"] or 0 for tail in tails), head["id"]))
            moved = 0
            for tail in tails:
                moved += db.execute("UPDATE attempts SET function_id=? WHERE function_id=?",
                                    (head["id"], tail["id"])).rowcount
                db.execute("UPDATE OR IGNORE twin_misses SET function_id=? WHERE function_id=?", (head["id"], tail["id"]))
                db.execute("DELETE FROM twin_misses WHERE function_id=?", (tail["id"],))
                db.execute("DELETE FROM claims WHERE function_id=?", (tail["id"],))
                db.execute("DELETE FROM waits WHERE function_id=? OR needs_id=?", (tail["id"], tail["id"]))
                db.execute("UPDATE notes SET function_id=? WHERE function_id=?", (head["id"], tail["id"]))
                db.execute("DELETE FROM functions WHERE id=?", (tail["id"],))
                db.execute("DELETE FROM symbols WHERE project_id=? AND address=? AND kind='func'", (pid, tail["vram"]))
            report(f"joined {', '.join(tail['name'] for tail in tails)} into {head['name']}: "
                   f"{moved} attempt(s) kept on it")
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
