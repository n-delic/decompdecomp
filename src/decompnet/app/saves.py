"""A ROM project's work as a folder of plain files, and a project rebuilt from one: to move to another computer (the
owner changes Linux distributions now and then), to keep in git, or to start over after something broke.

The progress folder is authoritative; progress.py keeps the engine's database in step with edits and git merges.
It contains what people and agents made: the matched C, the best attempts, the shared headers, the names, the function
splits, the notes, the instructions, which functions helpers take first, what agents learned, the compiler and its
ranges. Never the game: the save
holds its fingerprint (sha1), and a restore asks for the user's own copy and checks it's the same game. Every
matched function is judged again on the way in: a match counts because it matches, not because a file says so.

A project-mode game (DS, GameCube, Wii, PC; a public decomp) keeps its matched C in its own source files and git;
what only the database has is each open function's closest attempt, and a save writes those (best/), with a README
(#43: on a DS game near-misses of 97-99.9% were locked away, wanted for a port while matching goes on).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import closing, nullcontext
from pathlib import Path

from .. import compiler, consoles, toolchains
from ..hostenv import NO_WINDOW, stop_process_tree
from ..network import CLAIM_ORDERS, NetworkError
from ..request_timing import phase
from ..scoring import percent_text
from . import save_readme
from .restore_effects import batched as restore_batch

FORMAT = 1
MANIFEST = "decompdecomp.json"
SAVE_FOLDER = "progress"  # in the project's folder, unless the user picks another
SET_ASIDE_META = ("asm_code", "not_c", "asm_twin", "verified_assembly")  # why an agent or user set a function aside
SOURCE_COMPONENT_BYTES = 249  # leaves room for the publisher's .c.tmp suffix under common 255-byte limits
SOURCE_PREFIX_BYTES = 80  # readable hint; the SHA-256 suffix distinguishes symbols with the same prefix
GIT_TIMEOUT = 300
log = logging.getLogger("decompdecomp")


def _source_file(name: str) -> str:
    """Keep C++ template symbols intact, but fit their source files on Windows and Unix filesystems.

    The layout retains the full symbol; its digest makes the shortened filename stable across computers and
    different from another template with the same prefix. Leave room for the publisher's .c.tmp suffix.
    """
    if len(name.encode("utf-8")) <= SOURCE_COMPONENT_BYTES:
        return name + ".c"
    prefix = name.encode("utf-8")[:SOURCE_PREFIX_BYTES].decode("utf-8", errors="ignore")
    return prefix + "_" + hashlib.sha256(name.encode("utf-8")).hexdigest() + ".c"


def _header_file(unit: str) -> str:
    return "common.h" if not unit else f"common_{unit}.h"


def _source_text(text: str) -> str:
    # Leading blank lines can affect __LINE__, so saving must preserve them too.
    return text if text.endswith("\n") else text + "\n"


def default_folder(net, slug: str) -> Path:
    """The project's progress folder. Reads the project row rather than `net._project`, which synchronizes: a save to
    another folder must not be stopped by the default folder's unresolved git merge (#101 review)."""
    store = net.project_store(slug)
    proj = store.one("SELECT * FROM projects WHERE slug=?", slug)
    if proj is None:
        raise NetworkError(f"no project {slug!r} here: check its name, or whether it was removed")
    return Path(net.cfg(proj).get("progress_folder") or Path(proj["root"]) / SAVE_FOLDER)


def save_folder(net, slug: str) -> Path:
    """The last successful manual save, or the linked progress folder for a project opened from a save.

    This is a local preference, kept out of the shared manifest: another computer has its own paths.
    Saving a backup elsewhere does not move the authoritative progress folder.
    """
    store = net.project_store(slug)
    proj = store.one("SELECT * FROM projects WHERE slug=?", slug)
    if proj is None:
        raise NetworkError(f"no project {slug!r} here: check its name, or whether it was removed")
    return Path(net.cfg(proj).get("save_folder") or default_folder(net, slug))


def _remember_folder(net, project_id: int, folder: Path, *, chosen: bool) -> None:
    # A rescue save must work even when synchronizing the authoritative folder fails. Only update local config.
    store = net.project_store(project_id)
    with store.tx() as db:
        row = db.execute("SELECT config FROM projects WHERE id=?", (project_id,)).fetchone()
        cfg = json.loads(row["config"])
        if chosen:
            cfg["save_folder"] = str(folder)
        else:
            cfg.pop("save_folder", None)
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), project_id))


def build_checkout(folder: Path) -> Path | None:
    """A selected build root, or its progress child, has one safe progress destination."""
    for root in (folder, folder.parent):
        if (root / 'build.json').is_file() and (root / 'build.py').is_file():
            return root
    return None


def progress_destination(folder: Path) -> Path:
    """Never mix a portable save's flat src/include tree with a standalone build."""
    root = build_checkout(folder)
    if root == folder:
        target = root / SAVE_FOLDER
        if target.is_symlink():
            raise NetworkError("The detected progress subfolder is a symbolic link. Choose its actual location explicitly.")
        return target
    return folder


def save(net, slug: str, folder: Path | None = None) -> dict:
    """Write the project's work to `folder` (default: progress/ in the project's folder). Returns what was saved.

    A folder chosen here is written from the database alone: the default progress folder can be in the middle of a
    git merge or a failed write, and "Save now" elsewhere must still get the work out."""
    default = default_folder(net, slug)
    target = progress_destination(Path(folder)) if folder is not None else default
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
        if folder is not None and target != Path(folder):
            manifest = target / MANIFEST
            if manifest.is_symlink():
                raise NetworkError("The detected progress manifest is a symbolic link. Choose a regular progress folder.")
            if manifest.is_file():
                try:
                    existing = json.loads(manifest.read_text(encoding="utf-8"))
                except ValueError:
                    raise NetworkError("Fix the saved progress manifest before replacing its files.") from None
                game = existing.get('game') if isinstance(existing, dict) else None
                if not isinstance(game, dict) or game.get('sha1') != net.cfg(proj).get('rom', {}).get('sha1'):
                    raise NetworkError("This repository's progress folder belongs to another game. Choose a different project folder.")
        if not net.is_rom(proj):
            result = _save_best(net, proj, target)
            _remember_folder(net, proj["id"], target.resolve(), chosen=chosen)
            return result
        if chosen:
            files, result = _render(net, slug, target, chosen=True)
            previous = _read_owned(target)
            if not _publish(target, files, previous, chosen=True):
                raise NetworkError("The save folder changed while it was being written. Save again after the change finishes.")
            _remember_revision(net, slug, json.loads(files[MANIFEST]))
        else:
            net.progress.sync(slug, force=True)
            net.progress.prune_matched_drafts(slug)
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
        _remember_folder(net, proj["id"], target.resolve(), chosen=chosen)
        if chosen:
            # Local-only receipt: distinguish an untouched app backup from contributor edits.
            receipt = {"folder":str(target.resolve()), "digest":owned_digest(files)}
            with net.project_store(slug).tx() as db:
                row = db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()
                config = json.loads(row["config"])
                config["progress_save_receipt"] = receipt
                db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(config), proj["id"]))
        return result | {"folder": str(target.resolve())}
    except OSError as e:
        detail = e.strerror or str(e)
        raise NetworkError(f"Couldn't save to {target}: {detail}. Choose a writable folder and try again.") from None
    finally:
        if chosen and progress is not None:
            progress.local.busy = was_busy


@phase('progress_publish')
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
    store = net.project_store(slug)
    written = _manifest_revision(manifest)
    if written <= 0:
        return
    with store.tx() as db:
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
    if (folder / 'build.json').is_file() and (folder / 'build.py').is_file():
        raise NetworkError('This is a standalone build checkout. Use Sync to Git repository instead of Save now, '
                           'and keep the progress folder separate from the build tree.')
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
    owned.update(f"src/{_source_file(name)}" if "matched_by" in info else f"best/{_source_file(name)}"
                 for name, info in functions.items() if isinstance(info, dict))
    owned.update("include/" + file for file in headers.values() if isinstance(file, str))
    return {name: text for name, text in previous.items() if name in owned}


def owned_digest(files: dict[str, str]) -> str:
    """Content receipt for an app-written backup; never shared in the manifest."""
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode('utf-8')).hexdigest()


def _function_rows(net, project_id: int, *, sources: bool, names: list[str] | None = None) -> list:
    """Render metadata and, when requested, only the C that will become a progress file."""
    store = net.project_store(project_id)
    # Packed sources are nonempty hash blobs; only large text is packed. Presence
    # checks must use physical rows so they don't inflate either source again.
    matched = "r.status='matched' AND r.matched_source IS NOT NULL AND r.matched_source != ''"
    columns = ("r.name, r.unit, r.vram, r.size, r.status, r.matched_agent_id, r.best_score, "
               "r.best_agent_id, r.readable, r.meta, "
               "CASE WHEN r.matched_source IS NOT NULL AND r.matched_source != '' THEN 1 ELSE 0 END AS has_matched, "
               "CASE WHEN r.best_source IS NOT NULL AND r.best_source != '' THEN 1 ELSE 0 END AS has_best")
    join = ""
    if sources:
        # CASE short-circuits unused payloads, but selected C still uses the normal
        # checksum-validated detail view in the same read snapshot as its metadata.
        columns += (f", CASE WHEN {matched} THEN f.matched_source END AS matched_source, "
                    f"CASE WHEN {matched} THEN NULL WHEN r.best_score IS NOT NULL "
                    "THEN f.best_source END AS best_source")
        join = " JOIN functions f ON f.id=r.id"
    selected = "" if names is None else " AND r.name IN (SELECT value FROM json_each(?))"
    params = (project_id,) if names is None else (project_id, json.dumps(names))
    if not sources:
        # sqlite3 releases/reacquires the GIL while stepping each returned row.
        # CPU-heavy background Python work can turn thousands of cheap metadata
        # rows into seconds of fetch waits. Transport bounded JSON pages in one
        # snapshot; source payloads still use the checksum-validated detail path.
        fields = ('name','unit','vram','size','status','matched_agent_id','best_score',
                  'best_agent_id','readable','meta','has_matched','has_best')
        pairs = ','.join(f"'{field}',{field}" for field in fields)
        batches = store.all(f"""WITH numbered AS (
            SELECT {columns},row_number() OVER (ORDER BY r.vram) AS ordinal
            FROM main.functions r WHERE r.project_id=?{selected})
            SELECT json_group_array(json_object({pairs})) AS batch FROM numbered
            GROUP BY (ordinal-1)/128 ORDER BY min(ordinal)""", *params)
        rows = []
        while batches:
            rows.extend(json.loads(batches.pop()['batch']))
        # Aggregate input order is not a SQL guarantee; retain ORDER BY vram,
        # including SQLite's NULL-first ordering, after expanding the pages.
        rows.sort(key=lambda row: (row['vram'] is not None, row['vram']))
        return rows
    return store.all(f"SELECT {columns} FROM main.functions r{join} WHERE r.project_id=?{selected} ORDER BY r.vram",
                     *params)


def _knowledge_store(net, project):
    # Base Network users (including room recovery) do not own the GUI engine's
    # Knowledge service, but portable progress still needs its project schema.
    from .knowledge import service
    return service(net)._store(project)


def _manifest_from_rows(net, proj, cfg: dict, rows, folder: Path) -> tuple[dict, dict, set[str]]:
    """The progress manifest and its function index from function rows (with or without their source text)."""
    store = _knowledge_store(net, proj['id'])
    pid = proj["id"]
    note_rows = store.all(
        "SELECT n.text,n.created_at,n.score_before,n.score_after,n.agent_id,f.name AS fn "
        "FROM notes n LEFT JOIN functions f ON f.id=n.function_id WHERE n.project_id=? ORDER BY n.id", pid)
    agent_ids = sorted({r[k] for r in rows for k in ('matched_agent_id', 'best_agent_id') if r[k] is not None}
                       | {r['agent_id'] for r in note_rows if r['agent_id'] is not None})
    # Identities remain shared; project notes and functions need not join a copy
    # of the agent registry inside each operational database.
    names = {r["id"]: r["name"] for r in net.store.all(
        "SELECT id,name FROM agents WHERE id IN (SELECT value FROM json_each(?))", json.dumps(agent_ids))}
    functions = {}
    for r in rows:
        if r["status"] == "matched" and r["has_matched"]:
            functions[r["name"]] = {"vram": r["vram"], "matched_by": names.get(r["matched_agent_id"], ""),
                                    "readable": bool(r["readable"])}
        elif r["has_best"] and r["best_score"] is not None:
            functions[r["name"]] = {"vram": r["vram"], "best_score": r["best_score"],
                                    "best_by": names.get(r["best_agent_id"], "")}
    units = {""} | net._unit_contexts(pid)
    notes = [{"function": r["fn"], "text": r["text"], "by": names.get(r['agent_id'], ''),
              "created_at": r["created_at"], "score_before": r["score_before"],
              "score_after": r["score_after"]} for r in note_rows]
    knowledge = [r["text"] for r in store.all("SELECT text FROM knowledge WHERE project_id=? AND status='accepted' "
                                                  "ORDER BY id", pid)]
    manifest = {
        "format": FORMAT, "saved_at": time.time(), "revision": _next_revision(net, proj, folder),
        "title": proj["name"], "platform": cfg.get("platform"),
        "game": {"sha1": cfg.get("rom", {}).get("sha1"), "file": cfg.get("rom", {}).get("file")},
        "compiler": cfg.get("compiler"), "compiler_ranges": cfg.get("compiler_ranges") or [],
        "function_compilers": cfg.get('function_compilers') or {},
        "instructions": cfg.get("instructions") or "", "knowledge": knowledge,
        "claim_order": cfg.get("claim_order") or "",
        "priority": cfg.get("priority") or [],
        "auto_compiler": cfg.get("auto_compiler"),
        "knowledge_auto": bool(cfg.get("knowledge_auto")), "headers": {u: _header_file(u) for u in sorted(units)},
        "functions": functions,
        "function_headers": {r["name"]: r["unit"] for r in rows if r["unit"].startswith("exact_match_")},
        "layout": [{"name": r["name"], "vram": r["vram"], "size": r["size"],
                    "status": "asm" if r["status"] == "asm" else "open"} for r in rows],
        "symbols": net.layout_symbols(pid),
        "notes": notes,
        # what's set aside and why: agents' verdicts that no C makes it, the user's, a boundary report's leftover
        # (the import sets aside again what it finds by itself)
        "set_aside": [{"name": r["name"], "vram": r["vram"],
                       **{k: v for k, v in json.loads(r["meta"] or "{}").items() if k in SET_ASIDE_META}}
                      for r in rows if r["status"] == "asm"],
    }
    from ..match_bundle import portable
    if bundle := portable(net, pid):
        manifest['verification'] = bundle
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
                      functions' layout and names, notes, instructions, which functions helpers take first, what agents
                      learned, who matched what
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
            files[f"src/{_source_file(r['name'])}"] = _source_text(r["matched_source"])
        elif r["has_best"] and r["best_score"] is not None:
            files[f"best/{_source_file(r['name'])}"] = _source_text(r["best_source"])
    return files


@phase('progress_render')
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
    contexts = net._contexts(pid, units)
    for unit in sorted(units):
        files["include/" + _header_file(unit)] = (contexts[unit] or {"header": ""})["header"]
    files[MANIFEST] = json.dumps(manifest, indent=1) + "\n"
    if readme_key is None or _readme_key(manifest) != readme_key:
        _write_readme(net, proj, readme_folder, manifest, functions)
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    return files, {"matched": matched, "best": len(functions) - matched}


@phase('progress_render')
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
        contexts = net._contexts(pid, units)
        for unit in sorted(units):
            files["include/" + _header_file(unit)] = (contexts[unit] or {"header": ""})["header"]
    if names:
        for start in range(0, len(names), 400):
            chunk = names[start:start + 400]
            selected = _function_rows(net, pid, sources=True, names=chunk)
            files.update(_source_files(selected))
    if readme_key is None or _readme_key(manifest) != readme_key:
        _write_readme(net, proj, folder, manifest, functions)
    matched = sum(1 for f in functions.values() if "matched_by" in f)
    return files, {"matched": matched, "best": len(functions) - matched}


def _save_best(net, proj, folder: Path) -> dict:
    """A project-mode game's save: each open function's closest attempt as best/<function>.c, with how close it is,
    who wrote it and its source file in a comment at its top. Its matched C is in the project's own files."""
    store = net.project_store(proj['id'])
    rows = store.all("SELECT name, unit, n_insns, best_source, best_score, best_agent_id, meta FROM functions WHERE "
                         "project_id=? AND status='open' AND best_source IS NOT NULL AND best_score IS NOT NULL "
                         "ORDER BY unit, name", proj["id"])
    agent_ids = sorted({r['best_agent_id'] for r in rows if r['best_agent_id'] is not None})
    names = {r['id']: r['name'] for r in net.store.all(
        "SELECT id,name FROM agents WHERE id IN (SELECT value FROM json_each(?))", json.dumps(agent_ids))}
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for r in rows:
        pct = 100 * (1 - r["best_score"] / max(1, (r["n_insns"] or 1) * 100))
        source = json.loads(r["meta"] or "{}").get("source") or r["unit"]
        files[f"best/{_source_file(r['name'])}"] = (
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


def find_local_game(net, source, folder, manifest, log):
    """Check nearby originals and known local imports, never download or trust a filename."""
    expected = (manifest.get('game') or {}).get('sha1')
    if not expected:raise NetworkError('This save has no game fingerprint. Choose your own game file explicitly.')
    candidates = []
    for row in net.project_rows():
        cfg = json.loads(row['config'])
        rom = cfg.get('rom') or {}
        if rom.get('sha1') == expected:
            for key in ('file', 'program'):
                if rom.get(key):candidates.append(Path(row['root'])/rom[key])
    # Only local saves may discover adjacent originals. A cloned remote save never
    # authorizes looking around the temporary clone's parent directory.
    if Path(source).expanduser().is_dir():
        locations = [Path(source).expanduser(), folder]
        if folder.name == SAVE_FOLDER:locations.append(folder.parent)
        locations = list(dict.fromkeys(locations))
        locations += [base/sub for base in locations for sub in ('orig', 'rom', 'baserom')]
        filename = Path(str((manifest.get('game') or {}).get('file') or '')).name
        for base in locations:
            if filename:candidates.append(base/filename)
            if not base.is_dir():continue
            # Bound directory reads; no recursive walk of checkouts or the user's disk.
            candidates.extend(path for path in itertools.islice(base.iterdir(), 100) if path.is_file())
    checked = set()
    for candidate in candidates:
        getattr(log, 'check_cancel', lambda: None)()
        candidate = candidate.resolve()
        if candidate in checked or not candidate.is_file():continue
        checked.add(candidate)
        log('Checking local game candidate: '+str(candidate))
        try:
            picked = consoles.identify(candidate).read_game(candidate, candidate.name)
            if picked.info['sha1'] == expected:
                log('Using matching local game: '+str(candidate))
                return candidate
        except (OSError, ValueError, KeyError):
            continue
    raise NetworkError('No matching local game was found. Choose your copy of the game and open the saved project again.')


def restore(net, source: str, game: Path | None = None, log=print) -> dict:
    """Rebuild a project from a save (a folder or a git repository) and the user's own copy of the game: import it
    (or find it already imported), check it's the same game, then put back the layout, names, headers, settings and
    notes, and judge every saved function again. Returns the project and what came back."""
    from .progress import _manifest, _read

    log("checking the saved project and your game copy")
    with tempfile.TemporaryDirectory(prefix="decomp-restore-") as tmp:
        folder = fetch(source, Path(tmp) / "save", log)
        from ..forks.project import ForkProject
        if not (folder / MANIFEST).exists() and (saved := ForkProject(folder).progress_save()):
            folder = saved[0]
        try:
            manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise NetworkError(f"{folder} isn't a decompdecomp save (no {MANIFEST} in it)") from None
        if manifest.get("format") != FORMAT:
            raise NetworkError("this save was written by another version of decompdecomp: update it, then try again")
        if game is None:
            game = find_local_game(net, source, folder, manifest, log)
        data = Path(game).expanduser()
        getattr(log, "check_cancel", lambda: None)()
        game_sha1 = consoles.identify(data).read_game(data, data.name).info["sha1"]
        if game_sha1 != manifest["game"]["sha1"]:
            raise NetworkError(f"that's not the same game: this save is for {manifest['title']} "
                               f"({manifest['game'].get('file') or 'its original file'}); pick your copy of that one")
        files = _read(folder)
        manifest = _manifest(files, {"rom": {"sha1": game_sha1}})
        log("importing and splitting your game copy")
        existing = list(net.project_rows())
        source_folder = folder if Path(source).expanduser().is_dir() else Path(source).expanduser()
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
                            live = Path(net.project_store(slug).one("SELECT root FROM projects WHERE slug=?", slug)["root"]) / SAVE_FOLDER
                            if (live / ".git").exists():
                                raise NetworkError("This project already has a progress checkout. Pull changes there, or open that local folder.")
                            _publish(live, {}, _read(live))
                            shutil.copytree(folder, live, dirs_exist_ok=True)
                        net._update_cfg(slug, lambda cfg: cfg.__setitem__("progress_folder", str(live)))
                        project_id = net.project_store(slug).one("SELECT id FROM projects WHERE slug=?", slug)["id"]
                        if not same_linked_save:
                            with net.project_store(slug).tx() as db:
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
    store = net.project_store(slug)
    row = store.one("SELECT id,root FROM projects WHERE slug=?", slug)
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
    store = net.project_store(slug)
    proj = store.one("SELECT id FROM projects WHERE slug=?", slug)
    row=store.one("SELECT COALESCE(SUM(status='matched' AND matched_source IS NOT NULL AND length(matched_source)>0),0) AS matched, "
                  "COALESCE(SUM(status!='matched' AND best_source IS NOT NULL AND best_score IS NOT NULL),0) AS best "
                  "FROM main.functions WHERE project_id=?",proj['id'])
    return {'matched':row['matched'],'best':row['best'],'lost':[]}



def _saved_source(folder: Path, files: dict[str, str] | None, name: str) -> str | None:
    if files is not None:
        return files.get(name)
    path = folder / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _context_agents(net, store, manifest: dict, check_cancel=lambda: None, *, restore_notes=True) -> dict[str, int]:
    """Resolve shared credits before acquiring the project write transaction."""
    from ..store import in_transaction

    if in_transaction() and not (store is net.store and getattr(store._local, 'in_tx', False)):
        raise NetworkError('Resolve saved-progress authors before starting a database transaction.')
    names = {'restored'}
    if restore_notes:
        names.update(note.get('by') or 'restored' for note in manifest.get('notes') or [])
    agents = {}
    for name in sorted(names):
        check_cancel()
        agents[name] = net.attribution_agent(name).id
    return agents


@phase('progress_import')
def _apply_context(net, slug: str, folder: Path, manifest: dict, log,
                   files: dict[str, str] | None = None, *, restore_notes: bool = True,
                   agents: dict[str, int] | None = None) -> tuple[dict, dict]:
    """Put back everything a save changes before its C is judged: layout, names, settings, notes, set-aside."""
    store = _knowledge_store(net, slug)
    check_cancel = getattr(log, "check_cancel", lambda: None)
    if agents is None:
        agents = _context_agents(net, store, manifest, check_cancel, restore_notes=restore_notes)
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
    with store.tx() as db:
        for item in manifest.get("set_aside") or []:
            # A synced layout can already have set it aside before this runs; its reason still has to come back.
            row = _set_aside_row(db, pid, item)
            if row is None:
                continue
            why = restored_aside(item)
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
        cfg['function_compilers']={key:value for key,value in (manifest.get('function_compilers') or {}).items()
                                   if toolchains.known(str(value.get('toolchain') or ''))}
        cfg["instructions"] = manifest.get("instructions") or ""
        order = manifest.get("claim_order") or ""
        cfg["claim_order"] = order if order in CLAIM_ORDERS else ""
        cfg["priority"] = manifest.get("priority") or []
        cfg["auto_compiler"] = manifest.get("auto_compiler")
        cfg["knowledge_auto"] = bool(manifest.get("knowledge_auto"))
    net._update_cfg(slug, setup)
    if manifest.get("title"):
        with store.tx() as db:
            db.execute("UPDATE projects SET name=? WHERE id=?", (manifest["title"], pid))
    with store.tx() as db:
        for unit, file in (manifest.get("headers") or {}).items():
            check_cancel()
            header = _saved_source(folder, files, "include/" + file)
            if header is not None and header != net._context(pid, unit)["header"]:
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (pid, net._next_version(db, pid), header, agents['restored'],
                                                     "restored from a save", time.time(), unit))
        # Exact matches may retain a private header while the shared header moves on.
        # Restore that binding before compilation; older saves have no bindings.
        for name, unit in (manifest.get("function_headers") or {}).items():
            check_cancel()
            if isinstance(unit, str) and unit.startswith("exact_match_") and unit in (manifest.get("headers") or {}):
                db.execute("UPDATE functions SET unit=? WHERE project_id=? AND name=?", (unit, pid, name))
        for text in manifest.get("knowledge") or []:
            check_cancel()
            if not db.execute("SELECT 1 FROM knowledge WHERE project_id=? AND text=?", (pid, text)).fetchone():
                db.execute("INSERT INTO knowledge(project_id, text, status, created_at, decided_at) VALUES(?,?,?,?,?)",
                           (pid, text, "accepted", time.time(), time.time()))
    # Restored notes are historical records, not new agent handoffs. Going through add_note
    # recalculates attempt/session history once per note and can hold the writer for minutes.
    # Resolve the small identity/function sets once; old saves without measurements stay unknown.
    functions = {r["name"]: r["id"] for r in store.all(
        "SELECT id,name FROM functions WHERE project_id=?", pid)}
    with store.tx() as db:
        for note in (manifest.get("notes") or []) if restore_notes else []:
            check_cancel()
            name = note.get("function")
            if name and name not in functions:
                continue  # A split may have removed this function.
            author = note.get("by") or "restored"
            before, after = note.get("score_before"), note.get("score_after")
            if not all(type(value) is int and value >= 0 for value in (before, after)):
                before = after = None
            at = note.get("created_at")
            if type(at) not in (int, float):
                at = time.time()
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at,score_before,score_after) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, functions.get(name), agents[author], note["text"],
                                               at, before, after))
    with store.tx() as db:
        for item in manifest.get("set_aside") or []:
            row = _set_aside_row(db, pid, item)
            if row is not None:
                why = restored_aside(item)
                db.execute("UPDATE functions SET status='asm', meta=? WHERE id=?",
                           (json.dumps({**json.loads(row["meta"] or "{}"), **why}), row["id"]))
    by_name = {r["name"]: r for r in store.all("SELECT name,vram FROM functions WHERE project_id=?", pid)}
    return by_name, manifest.get("functions") or {}


def _saved_name(old_name, info, by_name, by_vram):
    row = by_name.get(old_name)
    if row is not None and row["vram"] == info["vram"]:
        return old_name
    candidates = by_vram.get(info["vram"], [])
    return candidates[0] if len(candidates) == 1 else None


def _saved_current(store, project_id, name, kind):
    # Already matched entries never use C here. For an open saved source, only the
    # normal judge needs the full function; unchanged drafts compare just best C.
    # CASE keeps unused packed columns out of the checksum/decompression path.
    return store.one("SELECT status,CASE WHEN status IS NOT 'matched' AND ? THEN best_source END AS best_source "
                     "FROM functions WHERE project_id=? AND name=?", kind == 'best', project_id, name)


def _saved_checks(net, proj, folder, files, by_name, by_vram, saved, judge_code, check_cancel):
    """Compile a bounded window ahead; only the caller may judge or change project state."""
    store = net.project_store(proj['id'])
    from .restore_compilation import Pool
    from .restore_compilation import width as compile_width

    pending = deque()
    entries = iter(saved.items())
    width = compile_width() if judge_code else 1
    # Shared background workers can be waiting for this project's lock. Never queue compiles
    # behind those tasks while holding that lock. Compiler._SLOTS still caps subprocesses globally.
    pool = (Pool(width)
            if judge_code else None)
    stopped = threading.Event()

    def compile_one(proj, header, text, fn):
        def check():
            if stopped.is_set():
                raise NetworkError("Saved-progress compilation stopped.")
            check_cancel()
        with compiler.cancellable(check):
            check()
            return net._compile(proj, header, text, fn)

    try:
        while True:
            while len(pending) < width:
                check_cancel()
                entry = next(entries, None)
                if entry is None:
                    break
                old_name, info = entry
                kind = "src" if "matched_by" in info else "best"
                text = _saved_source(folder, files, f"{kind}/{_source_file(old_name)}")
                name = _saved_name(old_name, info, by_name, by_vram)
                prepared = None
                if judge_code and name is not None and text is not None:
                    current = _saved_current(store, proj["id"], name, kind)
                    if current["status"] != "matched" and not (kind == "best" and current["best_source"] == text):
                        fn = dict(store.one("SELECT * FROM functions WHERE project_id=? AND name=?", proj["id"], name))
                        # A foreground result can arrive between the narrow read and
                        # the full compiler snapshot. Recheck against that fresh row.
                        if fn["status"] != "matched" and not (kind == "best" and fn["best_source"] == text):
                            ctx = net._context(proj["id"], fn["unit"])
                            if ctx is not None:
                                snapshot = net._compile_snapshot(proj, fn, ctx["header"], text)
                                future = pool.submit(compile_one, dict(proj), ctx["header"], text, fn)
                                prepared = (snapshot, future)
                pending.append((old_name, info, kind, text, name, prepared))
            if not pending:
                return
            item = pending.popleft()
            try:
                yield item
            finally:
                if item[-1] is not None:
                    item[-1][1].cancel()
    finally:
        stopped.set()
        for *_, prepared in pending:
            if prepared is not None:
                prepared[1].cancel()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


@restore_batch
def _judge_saved(net, slug: str, folder: Path, manifest: dict, by_name: dict, saved: dict, log,
                 files: dict[str, str] | None = None, judge_code: bool = True) -> dict:
    store = net.project_store(slug)
    check_cancel = getattr(log, "check_cancel", lambda: None)
    proj = dict(net._project(slug))
    pid = proj["id"]
    by_vram: dict[int, list[str]] = {}
    for name, row in by_name.items():
        by_vram.setdefault(row["vram"], []).append(name)
    matched, lost, best = 0, [], 0
    log(f"judging {len(saved)} saved functions again")
    update = getattr(log, "progress", lambda **fields: None)
    if judge_code:
        update(phase="Preparing saved verification",done=0,total=len(saved),function=None)
    from ..match_bundle import restore_evidence_many
    def evidence_names():
        for old_name, info in saved.items():
            check_cancel()
            name = _saved_name(old_name, info, by_name, by_vram)
            kind = "src" if "matched_by" in info else "best"
            if name is not None and _saved_source(folder, files, f"{kind}/{_source_file(old_name)}") is not None:
                yield name, old_name
    progress = getattr(net, "progress", None)
    verdict_lock = progress._project_lock(slug) if progress is not None else nullcontext()
    with verdict_lock:
        restore_evidence_many(net, pid, evidence_names(), manifest, check_cancel)
        if progress is not None and (active := progress.rechecking.get(slug)) is not None:
            # Expose the foreground lane only after old saved evidence is installed;
            # it must never replace a new match's verification bundle.
            active.ready = True
    # Metadata imports run under the project lock and do not compile or change function
    # status. One status snapshot replaces thousands of identical point queries;
    # reading best_source here would inflate every candidate without using it.
    current_rows = None if judge_code else {row["name"]:row for row in store.all(
        "SELECT name,status FROM functions WHERE project_id=?", pid)}
    last_update = 0
    judge_agent=None;credits={}
    checks = _saved_checks(net, proj, folder, files, by_name, by_vram, saved, judge_code, check_cancel)
    with closing(checks):
        for index, (old_name, info, kind, text, name, prepared) in enumerate(checks):
            # Compile workers do no writes. Wait outside the lock so a slow compiler cannot
            # occupy the foreground judge's publication lane.
            if prepared is not None:
                if not prepared[1].done():
                    update(phase="Compiling saved functions",done=index,total=len(saved),function=old_name,
                           matched=matched,best=best,lost=len(lost))
                while not prepared[1].done():
                    check_cancel()
                    time.sleep(0.01)
            with verdict_lock:
                now = time.monotonic()
                if judge_code or now - last_update >= 0.1:
                    update(phase="Verifying saved functions" if judge_code else "Reading saved function records",
                           done=index, total=len(saved), function=old_name, matched=matched, best=best, lost=len(lost))
                    last_update = now
                check_cancel()
                if judge_code:
                    log(f"checking {old_name}")
                if name is None or text is None:
                    lost.append(old_name) if kind == "src" else None
                    continue
                current = (_saved_current(store, pid, name, kind) if judge_code else current_rows[name])
                if current["status"] == "matched":
                    matched += kind == "src"
                    continue
                if judge_code and kind == "best" and current["best_source"] == text:
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
                author=info.get("matched_by") or info.get("best_by") or "restored"
                if author not in credits:credits[author]=net.attribution_agent(author)
                credit=credits[author]
                if judge_agent is None:judge_agent=net.local_agent("progress-judge")[0]
                agent=judge_agent
                try:
                    with net.compiled_ahead(prepared):
                        result = net.attempt(agent, name, text, slug)
                except (NetworkError, toolchains.ToolchainError) as e:
                    check_cancel()  # A paused/cancelled check is not a failed match; abort and roll back instead.
                    # A compiler that isn't here (an unknown one can never be installed) must not stop the other saved work
                    # from coming back; the function stays open for when the user picks one (#118).
                    log(f"{name}: {e}")
                    result = {"matched": False}
                finally:
                    with store.tx() as db:  # a saved attempt that didn't match leaves no claim behind
                        db.execute("DELETE FROM claims WHERE agent_id=? AND function_id IN "
                                   "(SELECT id FROM functions WHERE project_id=? AND name=?)", (agent.id, pid, name))
                with store.tx() as db:
                    db.execute("UPDATE functions SET matched_agent_id=? WHERE project_id=? AND name=? AND matched_agent_id=?",
                               (credit.id, pid, name, agent.id))
                    db.execute("UPDATE functions SET best_agent_id=? WHERE project_id=? AND name=? AND best_agent_id=?",
                               (credit.id, pid, name, agent.id))
                if kind == "src":
                    if result.get("matched") and info.get("readable"):  # its polished C matched again: it stays polished
                        with store.tx() as db:
                            db.execute("UPDATE functions SET readable=1 WHERE project_id=? AND name=?", (pid, name))
                    matched += bool(result.get("matched"))
                    if not result.get("matched"):
                        lost.append(name)
                else:
                    best += 1
    update(done=len(saved), total=len(saved), function=None, matched=matched, best=best, lost=len(lost))
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
    store = net.project_store(project_id)
    rows = store.all("SELECT name,vram,size FROM functions WHERE project_id=?", project_id)
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
    store = net.project_store(proj['id'])
    report = report or (lambda line: None)
    pid = proj["id"]
    console = net.console(proj)
    plans = []
    for entry in layout:
        start, end = entry["vram"], entry["vram"] + entry["size"]
        candidates = store.all("SELECT * FROM functions WHERE project_id=? AND vram=? ORDER BY id", pid, start)
        head = next((row for row in candidates if row["name"] == entry["name"]), None)
        if head is None:
            if len(candidates) != 1:
                continue  # an ambiguous overlay address (or a rename): never guess which function is meant
            head = candidates[0]
        if (head["size"] or 0) >= entry["size"]:
            continue  # already the saved function: there is no split to undo
        rows = store.all("SELECT * FROM functions WHERE project_id=? AND vram>=? AND vram<? AND unit=? "
                             "ORDER BY vram, id", pid, start, end, head["unit"])
        if len(rows) < 2 or rows[0]["id"] != head["id"]:
            continue
        tails = rows[1:]
        if any(tail["status"] == "matched" or tail["matched_source"] for tail in tails):
            raise NetworkError(
                f"The saved layout would join a function that already matches at 0x{start:08X}. "
                "Reimport the game before continuing; nothing was changed.")
        try:
            joined = console.join_saved_functions(
                rows, start, end, lambda address, n: net.reader(proj)(address, n))
        except ValueError:
            raise NetworkError("The saved layout crosses a gap in this analysis. Reimport the game before continuing.") from None
        target = net._make_target(proj, joined, net.symbols(pid))
        plans.append((head, tails, joined, target))
    if not plans:
        return
    with store.tx() as db:
        for head, tails, joined, target in plans:
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,seed=NULL,attempts=attempts+? "
                       "WHERE id=?",
                       (joined.size, console.function_insns(joined), target.code, json.dumps(target.to_json()),
                        console.function_asm(joined), sum(tail["attempts"] or 0 for tail in tails), head["id"]))
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


class _AutosavePaused(RuntimeError):
    """Cancel only a background check; foreground saves remain enabled."""


class Autosave:
    """Follow local work and contributions in progress/, including while no project window is open."""

    def __init__(self, net, settings=None):
        self.net, self.settings = net, settings
        self.stop = threading.Event()
        self.retry_after = {}
        self.deep_check_after = {}
        self.lock = threading.RLock()
        self.lane = threading.Semaphore(1)
        self.workers = {}
        self.paused = set(settings.data.get('autosave_paused', [])) if settings else set()
        self.thread = None

    def start(self) -> None:
        with self.lock:
            if self.thread is not None:
                return
            self.thread = threading.Thread(target=self._loop, daemon=True, name="autosave-coordinator")
            self.thread.start()
            self.push_thread = threading.Thread(target=self._push_loop, daemon=True, name="automatic-git-push")
            self.push_thread.start()

    def _push_loop(self):
        # Network latency must not occupy the autosave lane or the matching callers.
        from . import auto_push
        def check_cancel():
            if self.stop.is_set():raise _AutosavePaused("Automatic pushing stopped")
        while not self.stop.is_set():
            try:
                rows = self._projects(active_only=True)
            except Exception:
                if self.stop.is_set():return
                log.exception("Could not read projects for automatic Git push")
                self.stop.wait(5)
                continue
            for row in rows:
                if self.stop.is_set():return
                try:
                    push_settings = json.loads(row["config"]).get("auto_push", {})
                    if not push_settings.get("enabled"):continue
                    from .purpose_work import Executor
                    slug, threshold = row['slug'], push_settings.get('threshold',10)
                    with Executor(1, purpose='Automatic Git push', project=slug,
                                  stop=lambda slug=slug, threshold=threshold:auto_push.configure(self.net, slug, False, threshold),
                                  stop_note='Stop turns off automatic pushing for this project; commits stay local.') as pool:
                        def check():
                            check_cancel()
                            pool.check_cancel()
                        pool.submit(auto_push.process, self.net, slug, log.info, check).result()
                except _AutosavePaused:
                    return
                except Exception:
                    log.exception("Could not check automatic Git push for %s", row["slug"])
            self.stop.wait(5)

    def close(self):
        self.stop.set()
        with self.lock:
            for worker in self.workers.values():
                worker['cancel'].set()

    def snapshot(self):
        with self.lock:
            return [dict(key=slug, state=('pausing' if worker['running'] else 'paused')
                         if slug in self.paused else ('running' if worker['running'] else 'waiting'),
                         thread_id=worker['thread'].native_id, detail=worker['detail'])
                    for slug, worker in self.workers.items()]

    def pause(self, slug, paused=True):
        with self.lock:
            if slug not in self.workers:
                raise NetworkError('This project autosave is no longer available. Refresh the list.')
            worker = self.workers[slug]
            if paused:
                self.paused.add(slug)
                worker['cancel'].set()
            else:
                self.paused.discard(slug)
                # Never clear an active operation's cancellation request before it acknowledges it.
                if not worker['running']:
                    worker['cancel'].clear()
                self.retry_after.pop(slug, None)
                self.deep_check_after.pop(slug, None)
            if self.settings is not None:
                self.settings.data['autosave_paused'] = sorted(self.paused)
                self.settings.save()

    def _projects(self, *, active_only=False):
        store = self.net.store
        if hasattr(store, 'read_cache'):
            rows = store.read_cache.get('autosave-projects', store.read_version('project_list'),
                                       lambda: store.all("SELECT slug,root,config FROM projects"))
        else:
            rows = store.all("SELECT slug,root,config FROM projects")
        projects = []
        from .project_activity import active
        for row in rows:
            if active_only and not active(self.net, row['slug']):continue
            try:
                selected = self.net.project_store(row['slug'])
            except NetworkError:
                # Removal can commit after discovery took its catalog snapshot.
                # Skip only a confirmed removal; owner/corruption errors remain visible.
                if store.one('SELECT 1 FROM projects WHERE slug=?',row['slug']) is None:continue
                raise
            # Keep the existing single cached query in the legacy layout. After
            # the split, worker policy and paths come from the operational store,
            # not an older catalog snapshot of its configuration.
            current = row if selected is store else selected.read_cache.get(
                ('autosave-project',row['slug']),selected.read_version('project_list'),
                lambda selected=selected,slug=row['slug']: selected.one('SELECT slug,root,config FROM projects WHERE slug=?',slug))
            if current is None:
                if store.one('SELECT 1 FROM projects WHERE slug=?',row['slug']) is None:continue
                raise NetworkError(f"Project {row['slug']!r} is missing from its database. Restore it from backup.")
            projects.append(current)
        return projects

    def _reconcile(self):
        projects = {}
        for row in self._projects(active_only=True):
            cfg = json.loads(row['config'])
            if cfg.get('kind') == 'rom' and cfg.get('rom', {}).get('sha1'):
                projects[row['slug']] = row
        with self.lock:
            for slug in list(self.workers):
                if slug not in projects:
                    self.workers.pop(slug)['cancel'].set()
            for slug, row in projects.items():
                if slug in self.workers:
                    self.workers[slug]['row'] = row
                    self._start_commits(slug, self.workers[slug])
                    continue
                worker = dict(row=row, cancel=threading.Event(), running=False,
                              detail='Background file checks; explicit saves remain enabled.')
                worker['thread'] = threading.Thread(target=self._project_loop, args=(slug, worker),
                                                     daemon=True, name=f'autosave-{slug}')
                self.workers[slug] = worker
                worker['thread'].start()
                self._start_commits(slug, worker)

    def _start_commits(self, slug, worker):
        cfg = json.loads(worker['row']['config'])
        if worker.get('commit_thread') or not (cfg.get('auto_commit_matches') or cfg.get('auto_commit_polished') or cfg.get('score_commits', {}).get('enabled')):
            return
        worker['commit_thread'] = threading.Thread(target=self._commit_loop, args=(slug, worker),
                                                   daemon=True, name='automatic-commits-'+slug)
        worker['commit_thread'].start()

    def _commit_loop(self, slug, worker):
        from .purpose_work import Executor
        from .workers import lower_priority
        def check():
            from .project_activity import active
            if self.stop.is_set() or self.workers.get(slug) is not worker:
                raise _AutosavePaused('Automatic commits stopped')
            if slug in self.paused or not active(self.net, slug):
                raise _AutosavePaused('Automatic commits paused')
        def report(message):
            check()
            log.info('%s: %s', slug, message)
        report.check_cancel = check
        while not self.stop.is_set() and self.workers.get(slug) is worker:
            try:
                check()
                cfg = json.loads(worker['row']['config'])
                if not (cfg.get('auto_commit_matches') or cfg.get('auto_commit_polished') or cfg.get('score_commits', {}).get('enabled')):
                    self.stop.wait(1)
                    continue
                with Executor(1, purpose='Automatic progress commits', project=slug,
                              stop=lambda: self.pause(slug), initializer=lower_priority,
                              stop_note='Stop pauses automatic saving and commits for this project.') as pool:
                    def work():
                        check()
                        pool.check_cancel()
                        self._commit_once(slug, report)
                    pool.submit(work).result()
            except _AutosavePaused:
                pass
            except Exception:
                if not self.stop.is_set() and slug not in self.paused:
                    log.exception('Could not process automatic commits for %s', slug)
            self.stop.wait(1)

    def _commit_once(self, slug, output):
        # Read preferences now, not the coordinator's older discovery snapshot.
        proj = self.net.project_row(slug)
        if proj is None:return
        cfg = json.loads(proj['config'])
        if cfg.get('auto_commit_matches') or cfg.get('auto_commit_polished'):
            from . import match_commits
            match_commits.process(self.net, slug, output)
        if cfg.get('score_commits', {}).get('enabled'):
            from . import score_commits
            score_commits.process(self.net, slug, output)

    def _loop(self) -> None:
        from .workers import lower_priority
        lower_priority()
        while not self.stop.is_set():
            try:
                policy = getattr(self.net, 'project_activity', None)
                if policy is not None:policy.retire()
                self._reconcile()
                retention=getattr(self.net,'database_retention',None)
                if retention is not None:retention.tick()
            except Exception:
                log.exception('Could not refresh project autosave workers')
            self.stop.wait(2)

    def _project_loop(self, slug, worker):
        from .workers import lower_priority
        lower_priority()
        def check_cancel():
            from .project_activity import active
            if self.stop.is_set() or worker['cancel'].is_set() or not active(self.net, slug):
                raise _AutosavePaused('Background autosave paused')
        def report(message):
            check_cancel()
            with self.lock:worker['detail'] = str(message)
            log.info('%s: %s', slug, message)
        report.check_cancel = check_cancel
        while not self.stop.is_set():
            with self.lock:
                if self.workers.get(slug) is not worker:
                    return
                paused = slug in self.paused
                if not paused:
                    worker['cancel'].clear()
            if not paused and self.lane.acquire(timeout=.1):
                try:
                    with self.lock:
                        check_cancel()
                        worker['running'] = True
                    self.run_once(force=False, rows=[worker['row']], logger=report, commits=False)
                except _AutosavePaused:
                    pass
                finally:
                    with self.lock:
                        worker['running'] = False
                    self.lane.release()
            self.stop.wait(1)

    def run_once(self, *, force=True, rows=None, logger=None, commits=True) -> list[str]:
        """Save dirty work each cycle; background cycles deeply inspect unchanged files every 15 seconds.

        Explicit callers retain forced checks, including external edits with unchanged directory timestamps.
        """
        saved = []
        projects = self._projects(active_only=not force) if rows is None else rows
        for row in projects:
            if time.monotonic() < self.retry_after.get(row['slug'], 0):
                continue
            try:
                deep = force or time.monotonic() >= self.deep_check_after.get(row["slug"], 0)
                if self.net.progress.sync(row["slug"], force=deep, retry=False, **({"logger": logger} if logger else {})):  # (a failed one waits: RETRY_FAILED_SECONDS)
                    saved.append(row["slug"])
                if deep:
                    self.deep_check_after[row["slug"]] = time.monotonic() + 15
                self.retry_after.pop(row['slug'], None)
                commit_cfg = json.loads(row['config'])
                if commits and (commit_cfg.get('auto_commit_matches') or commit_cfg.get('auto_commit_polished') or commit_cfg.get('score_commits', {}).get('enabled')):
                    self._commit_once(row['slug'], logger or log.info)
            except _AutosavePaused:
                raise
            except Exception as error:  # keep other projects saving; do not flood logs every second
                from .progress import _StillFailing

                if isinstance(error, _StillFailing):
                    continue
                self.retry_after[row['slug']] = time.monotonic() + 30
                try:
                    cfg = json.loads(row['config'])
                    folder = cfg.get('progress_folder') or str(Path(row['root']) / SAVE_FOLDER)
                except (ValueError, TypeError, AttributeError):
                    folder = str(Path(row['root']) / SAVE_FOLDER)
                if isinstance(error, PermissionError):
                    action = "Check write permission for this folder and its files."
                elif isinstance(error, FileNotFoundError):
                    action = "Check that the project folder exists and its disk is connected."
                elif isinstance(error, OSError) and error.errno == 28:
                    action = "Free disk space on the drive containing this folder."
                elif 'locked' in str(error).lower():
                    action = "Close other programs using this project database; retry after they finish."
                else:
                    action = "Resolve any reported progress-file conflict or invalid file; include the complete traceback when reporting this error."
                from . import build_info
                log.exception("Progress sync failed for %s at %s: %s: %s. %s Retrying in 30 seconds. "
                              "Do not delete or overwrite progress files. Build: %s",
                              row['slug'], folder, type(error).__name__, str(error), action, build_info.label())
        return saved


def restored_aside(item):
    """Keep assembly provenance on restore, but require a fresh local assembler/data check."""
    meta={k:v for k,v in item.items() if k in SET_ASIDE_META}
    if isinstance(meta.get('verified_assembly'),dict):
        meta['verified_assembly']={**meta['verified_assembly'],'verified':False}
    else:meta.pop('verified_assembly',None)
    return meta
