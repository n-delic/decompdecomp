"""Keep ROM progress files authoritative, with SQLite as the engine's working representation.

SQLite stores the last successfully synchronized disk and database snapshots as one hash per file, so a restart can
distinguish an old checkout from a contributor's deletion or edit without keeping two full copies of the sources.
Triggers (store.DIRTY_TRIGGERS, knowledge.SCHEMA) mark the columns a render reads, so a local match writes its file
and the manifest, while a claim, an event or an attempt that didn't improve the best writes nothing. A pull is
validated before its files or database state are changed.

Change detection guarantee: a non-forced check compares the manifest's and the three folders' stats and may skip an
in-place file edit until the next periodic deep autosave check (at most 15 seconds); a forced check (judging, tools, routes, explicit saves, periodic deep autosave) compares
every file's name, size and mtime, so an editor's in-place write (which leaves its directory's mtime alone) is seen
before code is judged or a write is acknowledged. Read-only UI views use the current database and show when
verification is running. A file hash in the baseline is always compared for every file a
sync is about to write or remove.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import compiler, toolchains
from ..network import NetworkError
from ..rom.symbols import ASM_LOCAL, IDENT

# spimdisasm can name a function piece by the numeric label at its first instruction.
LOCAL_PIECE = re.compile(r"\.L[0-9A-F]+")
from . import saves

CHECK_SECONDS = 0.25  # a non-forced check of an unchanged folder is this rare per project
# A sync that failed (its game file gone) is tried again by reads and the autosave only when what it reads changed, or
# after this long. Retried on every one, two such projects cost 2.9 s each second in the autosave alone, and each try
# claimed a function anew. A user's action or a compile (forced) always tries again: a compiler installed meanwhile.
RETRY_FAILED_SECONDS = 60
PARTIAL_FUNCTIONS = 256  # more changed functions than this: one full query beats this many per-name lookups


def _hash(text: str | None) -> str | None:
    """A file's identity in the baseline; None keeps a missing file apart from an empty one."""
    return None if text is None else hashlib.sha1(text.encode("utf-8")).hexdigest()


def _config_key(proj, cfg: dict) -> str:
    """A digest of only the settings a progress folder's manifest is rendered from.

    Spending, check marks and the stored revision change constantly and must not look like a manifest edit; a title,
    compiler, instructions, priority, layout source or fingerprint does."""
    rom = cfg.get("rom") or {}
    settings = [proj["name"], cfg.get("platform"), rom.get("sha1"), rom.get("file"), cfg.get("compiler"),
                cfg.get("compiler_ranges") or [], cfg.get('function_compilers') or {}, cfg.get("instructions") or "", cfg.get("priority") or [],
                cfg.get("auto_compiler"), bool(cfg.get("knowledge_auto"))]
    return hashlib.sha1(json.dumps(settings, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass
class _Snapshot:
    files: dict[str, str]
    manifest: str | None = None

    @classmethod
    def of(cls, files: dict[str, str]) -> _Snapshot:
        return cls({name: _hash(text) for name, text in files.items()}, files.get(saves.MANIFEST))

    def changed(self, changes: dict[str, str | None], manifest: str | None) -> _Snapshot:
        files = dict(self.files)
        for name, text in changes.items():
            fingerprint = _hash(text)
            if fingerprint is None:
                files.pop(name, None)
            else:
                files[name] = fingerprint
        return _Snapshot(files, self.manifest if manifest is None else manifest)


@dataclass
class _Baseline:
    disk: _Snapshot
    projected: _Snapshot
    dirty_token: str | None = None
    config_key: str | None = None


@dataclass
class _State:
    dirty_token: str | None
    config_key: str | None


@dataclass
class _Dirty:
    token: str | None = None
    functions: bool = False
    headers: bool = False
    metadata: bool = False


class _FolderChanged(Exception):
    """The folder moved while a sync was applying its contribution; its transaction must roll back."""


class _StillFailing(NetworkError):
    """The last sync's failure again, nothing it reads having changed since (RETRY_FAILED_SECONDS)."""


def _read(folder: Path) -> dict[str, str]:
    # A git/editor update can remove a file between glob, lstat and read. Retry
    # only this read-only capture; never apply a partial snapshot or replay writes.
    for attempt in range(3):
        try:
            return _read_once(folder)
        except FileNotFoundError:
            if attempt == 2:
                raise


def _read_once(folder: Path) -> dict[str, str]:
    files = {}
    for directory in ("src", "best", "include"):
        path = folder / directory
        if path.is_symlink():
            raise NetworkError(f"Progress folder {directory!r} is a symbolic link. Replace it with a regular directory and try again.")
    for pattern in (saves.MANIFEST, "src/*.c", "best/*.c", "include/*.h"):
        for path in folder.glob(pattern):
            relative = path.relative_to(folder).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise NetworkError(f"Progress file {relative!r} is a symbolic link. Replace it with a regular file and try again.")
            if not stat.S_ISREG(mode):
                raise NetworkError(f"Progress path {relative!r} is not a regular file. Move the conflicting path and try again.")
            text = path.read_text(encoding="utf-8")
            if any(line.startswith(("<<<<<<<", "=======", ">>>>>>>")) for line in text.splitlines()):
                raise NetworkError("Resolve the git conflicts in progress before continuing this project.")
            files[path.relative_to(folder).as_posix()] = text
    return files


def _repair(net, pid: int, folder: Path, disk: dict[str, str], report) -> dict[str, str]:
    """What a folder an older version wrote lacks, put right before it's checked (#167: a PS2 project's folder, kept
    since before the folder was read back, stopped opening):
    - a header its manifest names but include/ lacks is written again from the project: it's the app's own output;
    - a source in src/ or best/ of no function in its layout nor in the project (a function's file under a name it
      no longer has) moves to unlisted/, kept as it is and not judged, so it stops the project no more. One the
      project still has (a layout edit renamed it) is left: `_manifest` refuses that edit.
    A manifest it can't read, or one with no layout, is left to `_manifest` to say what's wrong."""
    try:
        manifest = json.loads(disk[saves.MANIFEST])
        headers = dict(manifest.get("headers") or {})
        layout = {entry["name"] for entry in manifest.get("layout") or []}
    except (ValueError, TypeError, KeyError, AttributeError):
        return disk
    if not layout:
        return disk
    disk = dict(disk)
    for unit, file in headers.items():
        name = f"include/{file}"
        if isinstance(unit, str) and isinstance(file, str) and Path(file).name == file and file.endswith(".h") \
                and name not in disk:
            disk[name] = net._context(pid, unit)["header"]
            (folder / "include").mkdir(exist_ok=True)
            (folder / name).write_text(disk[name], encoding="utf-8")
            report(f"{name} was missing: written again from the project")
    left = sorted(name for name in disk if name.startswith(("src/", "best/")) and Path(name).stem not in layout)
    if left:
        known = {row["name"] for row in net.store.all("SELECT name FROM functions WHERE project_id=?", pid)}
        left = [name for name in left if Path(name).stem not in known]
    for name in left:
        (folder / "unlisted" / name).parent.mkdir(parents=True, exist_ok=True)
        os.replace(folder / name, folder / "unlisted" / name)
        del disk[name]
    if left:
        report(f"{len(left)} file(s) in src/ and best/ are of no function of the project (a function's file under a "
               "name it no longer has): moved to unlisted/, kept and not judged")
    return disk


def _manifest(files: dict[str, str], cfg: dict) -> dict:
    try:
        manifest = json.loads(files[saves.MANIFEST])
        if manifest["format"] != saves.FORMAT or manifest["game"]["sha1"] != cfg["rom"]["sha1"]:
            raise ValueError("it is another game, or not format %d" % saves.FORMAT)
        for file in (manifest.get("headers") or {}).values():
            if Path(file).name != file or "include/" + file not in files:
                raise ValueError(f"header {file!r} is not a file in include/")
        layout = {f["name"]: f for f in manifest.get("layout", [])}
        if len(layout) != len(manifest.get("layout", [])):
            raise ValueError("a function is listed twice in layout")
        for name, entry in layout.items():
            if (not (IDENT.fullmatch(name) or LOCAL_PIECE.fullmatch(name)) or not isinstance(entry["vram"], int)
                    or not isinstance(entry["size"], int) or entry["size"] <= 0
                    or entry.get("status", "open") not in ("open", "asm")):
                raise ValueError(f"layout entry {name!r} needs a name, an integer vram and a size above 0")
        compiler = manifest.get("compiler")
        if compiler is not None and not isinstance(compiler, dict):
            raise ValueError("compiler is not an object")
        if not isinstance(manifest.get("symbols", []), list) or not isinstance(manifest.get("notes", []), list) \
                or not isinstance(manifest.get("knowledge", []), list) \
                or not isinstance(manifest.get("set_aside", []), list):
            raise ValueError("symbols, notes, knowledge and set_aside must be lists")
        for symbol in manifest.get("symbols", []):
            if (not isinstance(symbol, dict) or not (IDENT.fullmatch(symbol.get("name", "")) or ASM_LOCAL.fullmatch(symbol.get("name", "")))
                    or not isinstance(symbol.get("address"), int) or not isinstance(symbol.get("size", 0), int)
                    or symbol.get("kind", "data") not in ("func", "data", "string", "jtbl", "bss")):
                raise ValueError(f"symbol {symbol.get('name')!r} has an invalid name, address, size or kind")
        for note in manifest.get("notes", []):
            if (not isinstance(note, dict) or not isinstance(note.get("text"), str)
                    or note.get("function") is not None and not isinstance(note.get("function"), str)
                    or not isinstance(note.get("by", ""), str)):
                raise ValueError("a note is malformed")
        if any(not isinstance(item, str) for item in manifest.get("knowledge", [])):
            raise ValueError("knowledge must be a list of text")
        for item in manifest.get("set_aside", []):
            if (not isinstance(item, dict) or not (IDENT.fullmatch(item.get("name", "")) or LOCAL_PIECE.fullmatch(item.get("name", "")))
                    or not isinstance(item.get("vram"), int)
                    or any(key in item and not isinstance(item[key], str) for key in ("asm_code", "asm_twin"))
                    or "not_c" in item and (not isinstance(item["not_c"], dict)
                        or any(not isinstance(value, dict)
                               or not all(isinstance(value.get(field), str if field != "t" else (int, float))
                                          for field in ("model", "why", "t"))
                               for value in item["not_c"].values()))):
                raise ValueError(f"set_aside entry {item.get('name')!r} is malformed")
        if "" not in (manifest.get("headers") or {}):
            raise ValueError("headers must name the project-wide header (the empty name)")
        for name in files:
            if name.startswith(("src/", "best/")) and Path(name).stem not in layout:
                raise NetworkError(f"Add {Path(name).stem} to the function layout in progress/decompdecomp.json before continuing.")
        functions = {}
        for name, info in layout.items():
            if Path(name).name != name or "\\" in name or "/" in name:
                raise ValueError(f"layout name {name!r} is not a function name")
            previous = manifest.get("functions", {}).get(name, {})
            if f"src/{name}.c" in files:
                functions[name] = {"vram": info["vram"], "matched_by": previous.get("matched_by", ""),
                                   "readable": previous.get("readable", False)}
            elif f"best/{name}.c" in files:
                functions[name] = {"vram": info["vram"], "best_by": previous.get("best_by", "")}
        manifest["functions"] = functions
        return manifest
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # #158: the one sentence alone gave no way to see which part of a big manifest was rejected.
        why = e.args[0] if isinstance(e, ValueError) and e.args else f"{type(e).__name__} {e}"
        raise NetworkError(f"Fix progress/decompdecomp.json: it must describe this game and reference existing headers ({why}).") from None


def _merge_manifest(old, new, current, key: str = ""):
    """Apply database fields changed since the baseline onto the current saved manifest."""
    if isinstance(old, dict) and isinstance(new, dict) and isinstance(current, dict):
        merged = dict(current)
        for field in old.keys() | new.keys():
            if old.get(field) == new.get(field):
                continue
            if field not in new:
                merged.pop(field, None)
            elif field in old and field in current:
                merged[field] = _merge_manifest(old[field], new[field], current[field], field)
            else:
                merged[field] = new[field]
        return merged
    if key in ("layout", "symbols", "set_aside", "notes", "knowledge") \
            and all(isinstance(value, list) for value in (old, new, current)):
        def identity(item):
            if key == "knowledge":
                return item if isinstance(item, str) else None
            if key == "notes" and isinstance(item, dict):
                return (item.get("function"), item.get("text"), item.get("by", ""))
            if isinstance(item, dict):
                return (item.get("name"),item.get("vram")) if key == "set_aside" else item.get("name")
            return None

        old_items = {identity(item): item for item in old if identity(item) is not None}
        new_items = {identity(item): item for item in new if identity(item) is not None}
        current_items = {identity(item): item for item in current if identity(item) is not None}
        for name in old_items.keys() | new_items.keys():
            if old_items.get(name) == new_items.get(name):
                continue
            if name not in new_items:
                current_items.pop(name, None)
            elif name in old_items and name in current_items:
                current_items[name] = _merge_manifest(old_items[name], new_items[name], current_items[name])
            else:
                current_items[name] = new_items[name]
        order = [identity(item) for item in current if identity(item) is not None]
        order.extend(identity(item) for item in new if identity(item) is not None and identity(item) not in order)
        ordered = [item for item in current if identity(item) is None]
        ordered.extend(current_items[name] for name in order if name in current_items)
        return ordered
    return new


def _initial_merge(manifest: dict, projected: dict[str, str], disk: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """A first sync has no deletion history, so keep database work while adding saved contributions."""
    local = json.loads(projected[saves.MANIFEST])
    for key in ("title", "compiler", "compiler_ranges", "instructions", "priority", "auto_compiler",
                "knowledge_auto"):
        manifest[key] = local.get(key)

    identities = {"layout": lambda item: item.get("name"), "symbols": lambda item: item.get("name"),
                  "notes": lambda item: (item.get("function"), item.get("text"), item.get("by", "")),
                  "knowledge": lambda item: item, "set_aside": lambda item: (item.get("name"),item.get("vram"))}
    for key, identity in identities.items():
        current = manifest.get(key) or []
        known = {identity(item) for item in current}
        for item in local.get(key) or []:
            ident = identity(item)
            if ident not in known and (key != "layout" or not any(row.get("vram") == item.get("vram") for row in current)):
                current.append(item)
                known.add(ident)
        manifest[key] = current

    headers = manifest.get("headers") or {}
    pending = {}
    for unit, file in (local.get("headers") or {}).items():
        if unit not in headers:
            headers[unit] = file
            name = "include/" + file
            if name in projected:
                pending[name] = projected[name]
    manifest["headers"] = headers
    for name, text in projected.items():
        if name.startswith(("src/", "best/")) and name not in disk:
            pending[name] = text
    return manifest, pending


def _database_newer(net, proj, manifest: dict, stored_revision: int) -> set[str]:
    """The database matches that a first sync (no baseline) must keep over the folder's snapshot.

    A snapshot says when it was written (`saved_at`), and a positive manifest revision below the database's own last
    save means the folder predates that save. A manifest without a revision is from before revisions: use its
    `saved_at`. With neither, the folder can't be shown to be newer, so the database's matches win: a same-name stale
    file must not overwrite a match made after the folder was saved (#101 review)."""
    rows = net.store.all("SELECT name, matched_at FROM functions WHERE project_id=? AND status='matched'", proj["id"])
    revision = manifest.get("revision")
    if (isinstance(revision, int) and not isinstance(revision, bool) and revision > 0
            and revision < stored_revision):
        return {row["name"] for row in rows}
    saved_at = manifest.get("saved_at")
    if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool):
        return {row["name"] for row in rows
                if isinstance(row["matched_at"], (int, float)) and row["matched_at"] > saved_at}
    return {row["name"] for row in rows}


def affected_functions(old, new, changed, rows):
    """Scope known per-function build inputs; unknown shared linking/layout changes require a full check."""
    from ..project import compiler_for

    # Symbol order and verdict notes do not alter linked bytes. Only actual symbol
    # changes are shared inputs; an assembly decision belongs to its own function.
    def symbols(data):
        return sorted(json.dumps(entry,sort_keys=True) for entry in data.get('symbols', []))
    if symbols(old) != symbols(new):
        return None
    def layout(data):
        return {f['name']: (f['vram'], f['size']) for f in data.get('layout', [])}
    if layout(old) != layout(new):
        return None
    old_headers, new_headers = old.get('headers') or {}, new.get('headers') or {}
    known_headers = {'include/'+file for mapping in (old_headers, new_headers) for file in mapping.values()}
    if any(path.startswith('include/') and path not in known_headers for path in changed):
        return None
    if bool(old.get('compiler')) != bool(new.get('compiler')):
        return None
    def assembly(data):
        return {(entry['name'],entry['vram']): entry.get('asm_code')
                for entry in data.get('set_aside', [])}
    previous_assembly, current_assembly = assembly(old), assembly(new)
    affected = {name for name,address in previous_assembly.keys() | current_assembly.keys()
                if ((name,address) in previous_assembly) != ((name,address) in current_assembly)
                or previous_assembly.get((name,address)) != current_assembly.get((name,address))}
    for row in rows:
        name, unit = row['name'], row['unit'] or ''
        previous = old_headers.get(unit, old_headers.get(''))
        current = new_headers.get(unit, new_headers.get(''))
        compiler_changed = bool(old.get('compiler')) and compiler_for(old, row['vram'], row) != compiler_for(new, row['vram'], row)
        if (compiler_changed or
            previous != current or (current and 'include/'+current in changed) or
            f'src/{name}.c' in changed or f'best/{name}.c' in changed):
            affected.add(name)
    return affected


@dataclass
class _Recheck:
    folder: Path
    signature: tuple
    functions: set[str] = field(default_factory=set)
    foreground: bool = False
    ready: bool = False


class Progress:
    def __init__(self, net):
        self.net = net
        self.lock = threading.RLock()
        self.local = threading.local()
        self.activity_lock = threading.Lock()
        self.activity: dict[str, dict] = {}
        self.paused: set[str] = set()
        self.batches: dict[str, int] = {}
        self.verification_slot = threading.Semaphore(1)
        self.disk: dict[str, dict[str, str]] = {}
        self.projected: dict[str, dict[str, str]] = {}
        self.checked: dict[str, float] = {}
        self.results: dict[str, dict] = {}
        self.revisions: dict[str, int] = {}
        self.project_locks: dict[str, threading.RLock] = {}
        self.rechecking: dict[str, _Recheck] = {}
        self.errors: dict[str, str] = {}
        self.signatures: dict[str, tuple] = {}  # slug -> every file's stat after the last acknowledged sync
        self.stamps: dict[str, tuple] = {}  # slug -> the cheap directory/manifest stat a non-forced check compares
        self.published: dict[str, set[str]] = {}  # slug -> the files a sync just wrote or removed
        self.baselines: dict[int, _Baseline | None] = {}
        self.states: dict[int, _State | None] = {}
        self.sync_changes: dict[str, int] = {}  # kept for restore, which drops every in-memory trace of a project
        self.adopting: set[str] = set()
        self.blocked: dict[str, tuple] = {}  # slug -> (folder, dirty token, settings) while a compiler waits
        self.failed: dict[str, tuple] = {}  # slug -> ((dirty token, settings, folder), when) of the last failed sync

    @staticmethod
    def _signature(folder: Path, *, deep: bool = True) -> tuple:
        """What a check compares: the manifest and three folders always, and with `deep` every file too.

        Directory mtimes catch files added, removed or renamed by git; they don't change when an editor writes a file
        in place, so a forced sync uses the deep form (`deep=True`, the default) and sees that edit. A non-forced
        check only needs to know there is something to read."""
        items = []
        for name in (saves.MANIFEST, "src", "best", "include"):
            path = folder / name
            try:
                info = os.stat(path, follow_symlinks=False)
                items.append((name, info.st_mtime_ns, info.st_size, info.st_mode))
                if deep and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                    with os.scandir(path) as entries:
                        children = sorted(entries, key=lambda entry: entry.name)
                    for child in children:
                        info = child.stat(follow_symlinks=False)
                        items.append((f"{name}/{child.name}", info.st_mtime_ns, info.st_size, info.st_mode))
            except OSError:
                items.append((name, None))
        return tuple(items)

    def _stamp(self, folder: Path) -> tuple:
        """The cheap check's signature; kept separate so a test or caller can observe the deep one."""
        return Progress._signature(folder, deep=False)

    @staticmethod
    def _expected_signature(folder: Path, cached: tuple, touched: set[str]) -> tuple | None:
        """The signature a publish should leave, from the cached one plus the files it wrote.

        Anything it can't account for (a file added, removed or edited by a pull or an editor meanwhile) returns
        None, so the cache stays cold and the next sync reads the folder: an edit is never acknowledged unseen."""
        entries = {item[0]: item for item in cached}
        items = []
        for name in (saves.MANIFEST, "src", "best", "include"):
            path = folder / name
            try:
                info = os.stat(path, follow_symlinks=False)
            except OSError:
                items.append((name, None))
                continue
            items.append((name, info.st_mtime_ns, info.st_size, info.st_mode))
            if name == saves.MANIFEST or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                continue
            with os.scandir(path) as entries_in_dir:
                children = sorted(entries_in_dir, key=lambda entry: entry.name)
            prefix = name + "/"
            known = {key[len(prefix):] for key in entries if key.startswith(prefix)}
            actual = {child.name for child in children}
            expected = (known - {key[len(prefix):] for key in touched if key.startswith(prefix)}
                        ) | {key[len(prefix):] for key in touched if key.startswith(prefix) and key[len(prefix):] in actual}
            if actual != expected:
                return None
            for child in children:
                key = prefix + child.name
                if key in touched or key not in entries:
                    child_info = child.stat(follow_symlinks=False)
                    items.append((key, child_info.st_mtime_ns, child_info.st_size, child_info.st_mode))
                else:
                    items.append(entries[key])
        return tuple(items)

    @staticmethod
    def _touched_acknowledged(folder: Path, touched: set[str], baseline: _Baseline | None) -> bool:
        """The files a publish wrote still hold what the baseline recorded."""
        if baseline is None:
            return False
        for name in touched:
            path = folder / name
            try:
                text = path.read_text(encoding="utf-8") if path.exists() else None
            except (OSError, UnicodeError):
                return False
            if _hash(text) != baseline.disk.files.get(name):
                return False
        return True

    def _state(self, project_id: int) -> _State | None:
        """The baseline's small bookkeeping, without loading 18,000 file hashes for a no-op check."""
        if project_id not in self.states:
            row = self.net.store.one("SELECT dirty_token,config_key FROM progress_state WHERE project_id=?",
                                     project_id)
            self.states[project_id] = None if row is None else _State(row["dirty_token"], row["config_key"])
        return self.states[project_id]

    def _dirty(self, project_id: int) -> _Dirty:
        row = self.net.store.one("SELECT token,functions,headers,metadata FROM progress_dirty WHERE project_id=?",
                                 project_id)
        if row is None:
            return _Dirty()
        return _Dirty(row["token"], bool(row["functions"]), bool(row["headers"]), bool(row["metadata"]))

    def _dirty_files(self, project_id: int) -> list:
        """The function files changed since the last publish: (name, the token their last write had)."""
        return self.net.store.all("SELECT name,token FROM progress_dirty_file WHERE project_id=? ORDER BY name",
                                  project_id)

    def _project_lock(self, slug: str):
        with self.lock:
            return self.project_locks.setdefault(slug, threading.RLock())

    def _baseline(self, project_id: int) -> _Baseline | None:
        if project_id in self.baselines:
            return self.baselines[project_id]
        row = self.net.store.one(
            "SELECT format,disk,projected,disk_manifest,projected_manifest,config_key,dirty_token "
            "FROM progress_state WHERE project_id=?", project_id)
        if row is None:
            self.baselines[project_id] = None
            return None
        if (row["format"] or 1) < 2:
            # The first release stored both full file sets as JSON (23 MB at 18,000 functions). Read that row once,
            # keep the manifests the merge needs, and convert the files to hashes so later syncs stay small.
            disk = _Snapshot.of(json.loads(row["disk"]))
            projected = _Snapshot.of(json.loads(row["projected"]))
            baseline = _Baseline(disk, projected, row["dirty_token"], row["config_key"])
            with self.net.store.tx() as db:
                db.execute("DELETE FROM progress_files WHERE project_id=?", (project_id,))
                self._write_side(db, project_id, "disk", None, disk)
                self._write_side(db, project_id, "projected", None, projected)
                db.execute("UPDATE progress_state SET disk='{}',projected='{}',format=2,disk_manifest=?,"
                           "projected_manifest=? WHERE project_id=?",
                           (disk.manifest, projected.manifest, project_id))
        else:
            files: dict[str, dict[str, str]] = {"disk": {}, "projected": {}}
            for r in self.net.store.all("SELECT side,name,hash FROM progress_files WHERE project_id=?", project_id):
                files.setdefault(r["side"], {})[r["name"]] = r["hash"]
            baseline = _Baseline(_Snapshot(files["disk"], row["disk_manifest"]),
                                 _Snapshot(files["projected"], row["projected_manifest"]),
                                 row["dirty_token"], row["config_key"])
        self.baselines[project_id] = baseline
        self.states[project_id] = _State(baseline.dirty_token, baseline.config_key)
        return baseline

    @staticmethod
    def _write_side(db, project_id: int, side: str, old: _Snapshot | None, new: _Snapshot) -> None:
        """Write only the file hashes that differ from the baseline."""
        if old is None:
            db.execute("DELETE FROM progress_files WHERE project_id=? AND side=?", (project_id, side))
            db.executemany("INSERT INTO progress_files(project_id,side,name,hash) VALUES(?,?,?,?)",
                           [(project_id, side, name, fingerprint) for name, fingerprint in new.files.items()])
            return
        for name in old.files.keys() - new.files.keys():
            db.execute("DELETE FROM progress_files WHERE project_id=? AND side=? AND name=?", (project_id, side, name))
        db.executemany(
            "INSERT INTO progress_files(project_id,side,name,hash) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id,side,name) DO UPDATE SET hash=excluded.hash",
            [(project_id, side, name, fingerprint) for name, fingerprint in new.files.items()
             if old.files.get(name) != fingerprint])

    @staticmethod
    def _write_side_changes(db, project_id: int, side: str, changes: dict[str, str | None],
                            old: _Snapshot) -> None:
        for name, text in changes.items():
            fingerprint = _hash(text)
            if old.files.get(name) == fingerprint:
                continue
            if fingerprint is None:
                db.execute("DELETE FROM progress_files WHERE project_id=? AND side=? AND name=?",
                           (project_id, side, name))
            else:
                db.execute("INSERT INTO progress_files(project_id,side,name,hash) VALUES(?,?,?,?) "
                           "ON CONFLICT(project_id,side,name) DO UPDATE SET hash=excluded.hash",
                           (project_id, side, name, fingerprint))

    @staticmethod
    def _write_state(db, project_id: int, baseline: _Baseline) -> None:
        db.execute(
            "INSERT INTO progress_state(project_id,disk,projected,updated_at,format,disk_manifest,"
            "projected_manifest,config_key,dirty_token) VALUES(?,'{}','{}',?,2,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET disk='{}',projected='{}',updated_at=excluded.updated_at,"
            "format=2,disk_manifest=excluded.disk_manifest,projected_manifest=excluded.projected_manifest,"
            "config_key=excluded.config_key,dirty_token=excluded.dirty_token",
            (project_id, time.time(), baseline.disk.manifest, baseline.projected.manifest,
             baseline.config_key, baseline.dirty_token))

    @staticmethod
    def _ack_dirty(db, project_id: int, token: str | None, tokens: list[str]) -> None:
        """Acknowledge the writes whose tokens the render saw; a later write has a token this does not delete."""
        for start in range(0, len(tokens), 400):
            chunk = tokens[start:start + 400]
            db.execute(f"DELETE FROM progress_dirty_file WHERE project_id=? AND token IN ({','.join('?' * len(chunk))})",
                       (project_id, *chunk))
        row = db.execute("SELECT token FROM progress_dirty WHERE project_id=?", (project_id,)).fetchone()
        if row is not None and row["token"] == token:
            db.execute("UPDATE progress_dirty SET functions=0,headers=0,metadata=0 WHERE project_id=?", (project_id,))

    def _remember(self, project_id: int, disk: dict[str, str], projected: dict[str, str],
                  config_key: str, dirty_token: str | None, dirty_tokens: list[str]) -> None:
        """Record a full sync as per-file hashes, writing only the hashes that changed."""
        baseline = _Baseline(_Snapshot.of(disk), _Snapshot.of(projected), dirty_token, config_key)
        old = self.baselines.get(project_id)
        if old is None:
            old = self._baseline(project_id)
        with self.net.store.tx() as db:
            self._write_side(db, project_id, "disk", old.disk if old else None, baseline.disk)
            self._write_side(db, project_id, "projected", old.projected if old else None, baseline.projected)
            self._write_state(db, project_id, baseline)
            self._ack_dirty(db, project_id, dirty_token, dirty_tokens)
        self.baselines[project_id] = baseline
        self.states[project_id] = _State(dirty_token, config_key)

    def _remember_changes(self, project_id: int, changes: dict[str, str | None], manifest: str,
                          config_key: str, dirty_token: str | None, dirty_tokens: list[str]) -> None:
        """Record what an incremental publish wrote, touching only those files' baseline rows."""
        old = self._baseline(project_id)
        if old is None:
            return
        baseline = _Baseline(old.disk.changed(changes, manifest), old.projected.changed(changes, manifest),
                             dirty_token, config_key)
        with self.net.store.tx() as db:
            self._write_side_changes(db, project_id, "disk", changes, old.disk)
            self._write_side_changes(db, project_id, "projected", changes, old.projected)
            self._write_state(db, project_id, baseline)
            self._ack_dirty(db, project_id, dirty_token, dirty_tokens)
        self.baselines[project_id] = baseline
        self.states[project_id] = _State(dirty_token, config_key)

    def prune_matched_drafts(self, slug: str) -> int:
        """Remove acknowledged drafts only while their verified selected C is still present unchanged."""
        with self._project_lock(slug):
            proj = self.net.store.one('SELECT * FROM projects WHERE slug=?', slug)
            if not self.net.is_rom(proj):
                return 0  # PROJECT mode's selected sources belong to its own build tree.
            baseline = self._baseline(proj['id'])
            if baseline is None:
                return 0
            names = [row['name'] for row in self.net.store.all("SELECT name FROM functions WHERE project_id=? "
                     "AND status='matched'", proj['id'])
                     if f"best/{row['name']}.c" in baseline.disk.files]
            if not names:
                return 0  # Normal autosaves must not inflate every matched source or scan the tree.
            folder = saves.default_folder(self.net, slug)
            saves._check_save_paths(folder, chosen=True)
            removed = 0
            for name in names:
                row = self.net.store.one('SELECT name,status,matched_source FROM functions WHERE project_id=? AND name=?',
                                         proj['id'], name)
                if row['status'] != 'matched' or not row['matched_source']:
                    continue
                source, draft = f"src/{name}.c", f"best/{name}.c"
                src, best = folder/source, folder/draft
                if not src.is_file() or not best.is_file():
                    continue
                selected, text = src.read_text(encoding='utf-8'), best.read_text(encoding='utf-8')
                if (selected != saves._source_text(row['matched_source'])
                        or _hash(selected) != baseline.disk.files.get(source)
                        or _hash(text) != baseline.disk.files[draft]):
                    continue
                current = self.net.store.one('SELECT status,matched_source FROM functions WHERE project_id=? AND name=?',
                                             proj['id'], row['name'])
                if current['status'] != 'matched' or current['matched_source'] != row['matched_source']:
                    continue
                if not saves._publish(folder, {source: selected}, {draft: text},
                                      expected={source: selected, draft: text}, chosen=True):
                    continue
                changes = {draft: None}
                with self.net.store.tx() as db:
                    self._write_side_changes(db, proj['id'], 'disk', changes, baseline.disk)
                    self._write_side_changes(db, proj['id'], 'projected', changes, baseline.projected)
                baseline.disk.files.pop(draft, None)
                baseline.projected.files.pop(draft, None)
                self.disk.get(slug, {}).pop(draft, None)
                self.projected.get(slug, {}).pop(draft, None)
                self.published.setdefault(slug, set()).add(draft)
                removed += 1
            if removed:
                self.signatures.pop(slug, None)
                self.stamps.pop(slug, None)
            return removed

    def forget(self, slug: str, project_id: int) -> None:
        """Drop in-memory state with the database row so a later project can safely reuse its slug or ID."""
        with self._project_lock(slug):
            self.baselines.pop(project_id, None)
            self.states.pop(project_id, None)
            for cache in (self.disk, self.projected, self.checked, self.results, self.revisions,
                          self.signatures, self.stamps, self.published, self.sync_changes, self.errors, self.blocked,
                          self.failed):
                cache.pop(slug, None)

    @contextmanager
    def rebuilding(self, slug: str):
        """An explicitly requested reimport changes the game's fingerprint and layout together with its progress."""
        self.sync(slug, force=True)
        with self._project_lock(slug):
            self.local.busy = True
            try:
                yield
                folder = saves.default_folder(self.net, slug)
                proj = self.net._project(slug)
                project_id = proj["id"]
                baseline = self._baseline(project_id)
                dirty = self._dirty(project_id)
                dirty_tokens = [r["token"] for r in self._dirty_files(project_id)]
                rendered = saves._render(self.net, slug)[0]
                if not saves._publish(folder, rendered, baseline.disk.files if baseline else None,
                                      chosen=bool(self.net.cfg(proj).get("progress_folder"))):
                    raise NetworkError("Progress changed while the project was being rebuilt. Read it again and retry.")
                self.disk[slug] = _read(folder)
                self.projected[slug] = rendered
                saves._remember_revision(self.net, slug, json.loads(rendered[saves.MANIFEST]))
                self._remember(project_id, self.disk[slug], rendered,
                               _config_key(proj, self.net.cfg(proj)), dirty.token, dirty_tokens)
                self.revisions[slug] = self.revisions.get(slug, 0) + 1
            finally:
                self.local.busy = False

    @contextmanager
    def batch(self, slug: str, logger=None):
        """Read external contributions once, then synchronize a group of exact publications once.
        Other threads defer this project's autosave without blocking its UI or skipping byte judgments."""
        with self._project_lock(slug):
            self.sync(slug, force=True, logger=logger)
            with self.activity_lock:
                self.batches[slug] = self.batches.get(slug, 0) + 1
            try:
                yield
            finally:
                with self.activity_lock:
                    depth = self.batches[slug] - 1
                    if depth:
                        self.batches[slug] = depth
                    else:
                        self.batches.pop(slug)
                if not depth:
                    self.sync(slug, force=True, logger=logger)

    def _batching(self, slug: str) -> bool:
        with self.activity_lock:
            return bool(self.batches.get(slug))

    @contextmanager
    def foreground(self):
        """Only explicit judge/notes requests may use already-imported inputs during a saved-code recheck."""
        previous = getattr(self.local, 'foreground', False)
        self.local.foreground = True
        try:
            yield
        finally:
            self.local.foreground = previous

    def foreground_changed(self, slug, name=''):
        if self.applying:
            return
        with self._project_lock(slug):
            active = self.rechecking.get(slug)
            if active is not None:
                active.foreground = True
                if name:active.functions.add(name)

    def _check_rechecking_files(self, slug):
        pending = self.rechecking.get(slug)
        if pending and self._signature(pending.folder) != pending.signature:
            raise NetworkError('Saved files changed during the recheck. Wait for synchronization, then read the function and retry.')

    def _judge_saved(self, slug, *args, **kwargs):
        """Wait without owning the project lock while saved functions compile and publish one at a time.

        Context/layout imports have committed before this phase. Other imports still wait;
        foreground attempts use those new inputs, and every saved verdict re-reads its function.
        Condition.wait releases every recursive acquisition, including a caller's batch lock.
        """
        if not kwargs.get('judge_code', True):
            return saves._judge_saved(self.net, slug, *args, **kwargs), None
        lock = self._project_lock(slug)
        condition = threading.Condition(lock)
        outcome = []
        folder = args[0]
        active = _Recheck(folder, self._signature(folder))
        self.rechecking[slug] = active

        def run():
            self.local.busy = True
            try:
                from .workers import lower_priority
                lower_priority()
                outcome.append((True, saves._judge_saved(self.net, slug, *args, **kwargs)))
            except BaseException as error:
                outcome.append((False, error))
            finally:
                self.local.busy = False
                with condition:
                    condition.notify_all()

        worker = threading.Thread(target=run, name='saved-judge-'+slug, daemon=True)
        try:
            worker.start()
            while not outcome:
                condition.wait(timeout=0.1)
        finally:
            self.rechecking.pop(slug, None)
        ok, result = outcome[0]
        if not ok:
            raise result
        return result, active

    @contextmanager
    def judging(self, slug: str):
        """Compiles may run together, but a result from before a pull must never replace its contribution."""
        if self.applying:
            yield
            return
        self.sync(slug, force=True)
        previous = getattr(self.local, "judging", None)
        self.local.judging = (slug, self.revisions.get(slug, 0))
        try:
            yield
        finally:
            self.local.judging = previous
        self.sync(slug, force=True)

    def check_result(self) -> None:
        pending = getattr(self.local, "judging", None)
        if pending and not self.applying:
            self._check_rechecking_files(pending[0])
        if not self.applying and pending and self.revisions.get(pending[0], 0) != pending[1]:
            raise NetworkError("Progress changed while this attempt compiled. Read the function again and retry with the updated project.")

    def flush(self) -> dict[str, str]:
        """Synchronize every ROM project's final database work before the process exits."""
        failures = {}
        for row in self.net.store.all("SELECT slug FROM projects"):
            try:
                self.sync(row["slug"], force=True)
            except NetworkError as error:
                failures[row["slug"]] = str(error)
        return failures

    @property
    def applying(self) -> bool:
        return getattr(self.local, "busy", False)

    @property
    def viewing(self) -> bool:
        return getattr(self.local, "viewing", False)

    @contextmanager
    def view(self):
        """Read the current database for the UI while background synchronization continues."""
        previous = self.viewing
        self.local.viewing = True
        try:
            yield
        finally:
            self.local.viewing = previous

    def _activity(self, slug: str, **fields):
        with self.activity_lock:
            now = time.monotonic()
            current = self.activity.setdefault(slug, {"started": now})
            if fields.get("phase", current.get("phase")) != current.get("phase") and "done" in fields:
                current.update(rate_started=now, rate_done=fields["done"])
            current.update(fields, updated=now)

    def status(self, slug: str) -> dict | None:
        with self.activity_lock:
            current = self.activity.get(slug)
            if current is None:
                return None
            now = time.monotonic()
            completed = current.get("done", 0) - current.get("rate_done", 0)
            duration = now - current.get("rate_started", now)
            total = current.get("total")
            remaining = (max(0, int((total-current["done"])*duration/completed))
                         if total and completed >= 3 and duration > 0 and not current.get("paused") else None)
            return {k: v for k, v in current.items() if k not in {"started", "updated", "rate_started", "rate_done"}} | {
                "remaining_seconds": remaining,
                "elapsed_seconds": int(now - current["started"]),
                "idle_seconds": int(now - current["updated"])}

    def pause(self, slug: str):
        with self.activity_lock:
            self.paused.add(slug)
        self._activity(slug, phase="Verification paused", paused=True)

    def resume(self, slug: str):
        with self.activity_lock:
            self.paused.discard(slug)
            self.activity.pop(slug, None)
        self.errors.pop(slug, None)

    def _check_paused(self, slug: str):
        if slug in self.paused:
            raise NetworkError("Saved-progress verification is paused. Resume it before changing this project.")

    def syncing(self, slug: str) -> bool:
        # Judging and edits also hold the project lock; they are not a saved-progress check.
        return self.status(slug) is not None

    def sync(self, slug: str, *, force: bool = False, logger=None, retry: bool | None = None) -> bool:
        """Read contributions first, then publish only work changed locally since the last synchronization.

        A no-op non-forced check stats the manifest and the three folders; a forced check stats every file too, so an
        in-place edit an editor made (which leaves a directory's mtime alone) is never missed by judging, a tool call,
        the routes or a periodic deep autosave check. Nothing is rendered or written unless the folder or a column the render reads
        changed. `retry` (forced syncs by default): try again after a failure even when nothing it reads changed."""
        if self._batching(slug) or self.applying or getattr(self.net.store._local, "in_tx", False):
            return False
        if logger is not None:getattr(logger, "check_cancel", lambda: None)()
        self._check_paused(slug)
        from ..request_timing import span, timed_lock
        with (span('progress_sync'),
              timed_lock(self._project_lock(slug), 'progress_project_lock_wait')):
            while slug in self.rechecking:
                if getattr(self.local, 'foreground', False) and self.rechecking[slug].ready:
                    self._check_rechecking_files(slug)
                    return False
                if logger is not None:getattr(logger, 'check_cancel', lambda: None)()
                self._check_paused(slug)
                # Public Condition.wait also releases an outer batch's recursive lock.
                threading.Condition(self._project_lock(slug)).wait(timeout=0.1)
            if self._batching(slug):
                return False
            if logger is not None:getattr(logger, "check_cancel", lambda: None)()
            self._check_paused(slug)
            self._activity(slug, phase="Reading saved files", done=0, total=None, function=None, full_recheck=False)
            self.local.busy = True
            try:
                proj = self.net._project(slug)
                rom = self.net.is_rom(proj) and bool(self.net.cfg(proj).get("rom", {}).get("sha1"))
                if rom:
                    pid = proj["id"]
                    folder = saves.default_folder(self.net, slug)
                    state = self._state(pid)
                    dirty = self._dirty(pid)
                    config_key = _config_key(proj, self.net.cfg(proj))
                    failed = None if (force if retry is None else retry) else self.failed.get(slug)
                    if (failed and slug in self.errors and time.monotonic() - failed[1] < RETRY_FAILED_SECONDS
                            and failed[0] == (dirty.token, config_key, self._signature(folder))):
                        raise _StillFailing(self.errors[slug])
                    changed_db = state is not None and (
                        dirty.token != state.dirty_token or dirty.functions or dirty.headers or dirty.metadata
                        or state.config_key is None or config_key != state.config_key)
                    if state is not None and not changed_db:
                        if not force and time.monotonic() - self.checked.get(slug, 0) < CHECK_SECONDS:
                            return False
                        stamp = self._stamp(folder)
                        if not force and stamp == self.stamps.get(slug):
                            self.checked[slug] = time.monotonic()
                            return False
                        if force:
                            signature = self._signature(folder)
                            if signature == self.signatures.get(slug):
                                self.checked[slug] = time.monotonic()
                                self.stamps[slug] = self._stamp(folder)
                                return False
                # Unchanged inputs need no compiler/verification slot. Keep the project
                # lock for those checks; serialize actual imports and judging as before.
                with timed_lock(self.verification_slot, 'progress_verification_wait'):
                    if logger is not None:getattr(logger, "check_cancel", lambda: None)()
                    self._check_paused(slug)
                    if self._batching(slug):
                        return False
                    # Another project may have changed shared state while we waited.
                    # _sync reloads its inputs too; post-sync acknowledgement uses fresh metadata.
                    proj = self.net._project(slug)
                    rom = self.net.is_rom(proj) and bool(self.net.cfg(proj).get("rom", {}).get("sha1"))
                    cached_signature = self.signatures.get(slug)
                    self._activity(slug, phase="Comparing saved files")
                    result = self._sync(slug, logger)
                    if rom and result:
                        self.prune_matched_drafts(slug)
                    self.checked[slug] = time.monotonic()
                    self.sync_changes[slug] = self.net.store.conn.total_changes
                    if rom:
                        folder = saves.default_folder(self.net, slug)
                        # After a real write, make sure the folder is exactly what this sync rendered and wrote. An edit
                        # that arrived meanwhile must not be acknowledged (and so missed forever) by the cache.
                        signature = self._signature(folder)
                        touched = self.published.pop(slug, None)
                        acknowledged = True
                        if result:
                            baseline = self._baseline(proj["id"])
                            if cached_signature is not None and touched is not None:
                                expected = self._expected_signature(folder, cached_signature, touched)
                                acknowledged = (expected == signature
                                                and self._touched_acknowledged(folder, touched, baseline))
                            else:
                                acknowledged = (baseline is not None
                                                and _Snapshot.of(_read(folder)).files == baseline.disk.files)
                        stable = signature == self._signature(folder)
                        if acknowledged and stable:
                            self.signatures[slug] = signature
                            self.stamps[slug] = self._stamp(folder)
                        else:
                            self.signatures.pop(slug, None)
                            self.stamps.pop(slug, None)
                    if not self.blocked.get(slug):
                        self.errors.pop(slug, None)
                    self.failed.pop(slug, None)
                    return result
            except _StillFailing:
                raise  # (when it failed stays when it first did: a read every second mustn't put the next try off)
            except (OSError, UnicodeError, json.JSONDecodeError) as e:
                from ..storage_errors import message as storage_error
                message = storage_error(e) or "Could not read or write progress. Finish the git operation and check the folder's permissions."
                if isinstance(e, OSError):
                    # Do not stringify arbitrary exception arguments: they may contain source text.
                    code = errno.errorcode.get(e.errno, type(e).__name__)
                    filename = os.fsdecode(e.filename) if e.filename is not None else None
                    location = f" at {filename[:400]!r}" if filename else ""
                    message += f"\nFilesystem failure: {code}{location}."
                self.errors[slug] = message
                self._failed(slug)
                raise NetworkError(message) from e
            except NetworkError as e:
                self.errors[slug] = str(e)
                self._failed(slug)
                raise
            finally:
                self.local.busy = False
                with self.activity_lock:
                    if slug not in self.paused:
                        self.activity.pop(slug, None)

    def _failed(self, slug: str) -> None:
        """Remember what a failed sync read (after its own writes), so the next one waits for a change."""
        try:
            proj = self.net._project(slug)
            if self.net.is_rom(proj):
                folder = saves.default_folder(self.net, slug)
                key = (self._dirty(proj["id"]).token, _config_key(proj, self.net.cfg(proj)), self._signature(folder))
                self.failed[slug] = (key, time.monotonic())
        except (NetworkError, OSError):  # (a project removed meanwhile: nothing to wait for)
            self.failed.pop(slug, None)

    def _publish_initial(self, slug: str, proj, folder: Path, disk: dict[str, str], dirty: _Dirty,
                         config_key: str) -> bool:
        """A first sync with nothing on disk yet: render the database's work once and record its hashes."""
        net = self.net
        dirty_tokens = [r["token"] for r in self._dirty_files(proj["id"])]
        rendered, _ = saves._render(net, slug)
        baseline = self._baseline(proj["id"])
        previous = baseline.projected if baseline is not None else None
        changes = {name: text for name, text in rendered.items()
                   if previous is None or previous.files.get(name) != _hash(text)}
        removed = {name: None for name in (previous.files if previous is not None else {}) if name not in rendered}
        if not saves._publish(folder, changes, removed, expected=disk,
                              chosen=bool(net.cfg(proj).get("progress_folder"))):
            return False
        if saves.MANIFEST in changes:
            saves._remember_revision(net, slug, json.loads(changes[saves.MANIFEST]))
        disk = _read(folder)
        proj = net._project(slug)
        self.disk[slug] = disk
        self.projected[slug] = rendered
        self.published[slug] = set(changes) | set(removed)
        self._remember(proj["id"], disk, rendered, _config_key(proj, net.cfg(proj)),
                       dirty.token, dirty_tokens)
        return bool(changes or removed)

    def _publish_changes(self, slug: str, proj, folder: Path, baseline: _Baseline, dirty: _Dirty,
                         config_key: str) -> bool | None:
        """Publish only the files the database changed since the last sync; None means the folder moved meanwhile."""
        net = self.net
        pid = proj["id"]
        rows = self._dirty_files(pid)
        dirty_tokens = [r["token"] for r in rows]
        # A token changed but no flag survived (a rollback): publish everything once rather than miss a change.
        no_clue = (dirty.token != baseline.dirty_token
                   and not (dirty.functions or dirty.headers or dirty.metadata or rows))
        flags = _Dirty(dirty.token, dirty.functions or bool(rows) or no_clue,
                       dirty.headers or no_clue, dirty.metadata or no_clue)
        names = [r["name"] for r in rows] if flags.functions else []
        full = (flags.functions and not names) or len(names) > PARTIAL_FUNCTIONS
        readme_key = None
        if baseline.projected.manifest is not None:
            readme_key = saves._readme_key(json.loads(baseline.projected.manifest))
        if full:
            files, _ = saves._render(net, slug, readme_key=readme_key)
        else:
            files, _ = saves._render_partial(net, proj, folder, names, headers=flags.headers,
                                             readme_key=readme_key)
        changes = {name: text for name, text in files.items()
                   if baseline.projected.files.get(name) != _hash(text)}
        if full:
            removed = {name: None for name in baseline.projected.files if name not in files}
            redundant = {"best/"+name[4:] for name in files if name.startswith("src/")}
            removed.update({name: None for name in redundant if name in baseline.disk.files and name not in files})
        else:
            possible = {f"src/{name}.c" for name in names} | {f"best/{name}.c" for name in names}
            if flags.headers:
                possible |= {name for name in baseline.projected.files if name.startswith("include/")}
            removed = {name: None for name in possible
                       if name in baseline.disk.files and name not in files}
        expected = {}
        for name in set(changes) | set(removed):
            path = folder / name
            try:
                current = path.read_text(encoding="utf-8") if path.exists() else None
            except (OSError, UnicodeError):
                return None
            if _hash(current) != baseline.disk.files.get(name):
                return None  # a pull or an editor wrote while this was rendering: read it next pass
            expected[name] = current
        if not changes and not removed:
            self._remember_changes(pid, {}, files[saves.MANIFEST], config_key, dirty.token, dirty_tokens)
            return False
        if not saves._publish(folder, changes, removed, expected=expected,
                              chosen=bool(net.cfg(proj).get("progress_folder"))):
            return None
        if saves.MANIFEST in changes:
            saves._remember_revision(net, slug, json.loads(changes[saves.MANIFEST]))
        self.disk[slug] = dict(baseline.disk.files)
        self.projected[slug] = files
        self.published[slug] = set(changes) | set(removed)
        self._remember_changes(pid, {**changes, **removed}, files[saves.MANIFEST], config_key,
                               dirty.token, dirty_tokens)
        return bool(changes or removed)

    def _sync(self, slug: str, logger=None) -> bool:
        net = self.net
        proj = net._project(slug)
        if not net.is_rom(proj):
            return False  # PROJECT mode's source files already belong to its build and git repository.
        cfg = net.cfg(proj)
        if not cfg.get("rom", {}).get("sha1") or net._context(proj["id"]) is None:
            return False  # import has not registered the game's fingerprint and shared header yet
        folder = saves.default_folder(net, slug)
        self.blocked.pop(slug, None)  # a run that gets past its compiler gate clears the wait
        saves._check_progress_folder(folder, chosen=bool(cfg.get("progress_folder")))
        pid = proj["id"]
        state = self._state(pid)
        dirty = self._dirty(pid)
        config_key = _config_key(proj, cfg)
        stored_token = state.dirty_token if state is not None else None
        db_changed = (dirty.token != stored_token or dirty.functions or dirty.headers or dirty.metadata)
        config_changed = state is not None and (state.config_key is None or config_key != state.config_key)
        baseline = self._baseline(pid)
        signature = self._signature(folder)
        if baseline is not None and self.signatures.get(slug) == signature:
            if not db_changed and not config_changed:
                return False
            result = self._publish_changes(slug, proj, folder, baseline, dirty, config_key)
            if result is not None:
                return result
        disk = _read(folder)
        snapshot = _Snapshot.of(disk)
        if baseline is not None and snapshot.files == baseline.disk.files:
            if not db_changed and not config_changed:
                return False
            result = self._publish_changes(slug, proj, folder, baseline, dirty, config_key)
            if result is not None:
                return result
            disk = _read(folder)
            snapshot = _Snapshot.of(disk)
        before = baseline.disk if baseline is not None else None
        if saves.MANIFEST not in disk and (before is not None or cfg.get("progress_folder")
                                           or disk or (folder / ".git").exists()):
            raise NetworkError("Restore progress/decompdecomp.json before continuing this project.")
        if saves.MANIFEST in disk and (before is None or snapshot.files != before.files):
            destination = logger or (lambda line: saves.log.info("%s: %s", slug, line))
            def report(line):
                self._activity(slug, message=line)
                destination(line)
            disk = _repair(net, pid, folder, disk, report)
            manifest = _manifest(disk, cfg)
            def check_cancel():
                self._check_paused(slug)
                getattr(destination, "check_cancel", lambda: None)()
            report.check_cancel = check_cancel
            def progress(**fields):
                self._activity(slug, **fields)
                # Import jobs need the same measured counters as the project activity indicator.
                publish = getattr(destination, "progress", None)
                if publish is not None:
                    publish(self.status(slug))
            report.progress = progress
            getattr(report, "check_cancel", lambda: None)()
            # Captured before any render: the folder's revision is compared with what the database wrote last.
            stored_revision = saves._stored_revision(cfg)
            # Preserve work on unrelated functions even if a helper finished while a contribution was pulled.
            comparison = None if before is not None else saves._render(net, slug)[0]
            comparison_manifest = before.manifest if before is not None else comparison[saves.MANIFEST]
            if before is None:
                changed = {name for name in set(disk) | set(comparison) if disk.get(name) != comparison.get(name)}
            else:
                changed = {name for name in set(disk) | set(before.files)
                           if _hash(disk.get(name)) != before.files.get(name)}
            initial_pending = {}
            if before is None and slug not in self.adopting:
                changed = {name for name in changed if name in disk}
                manifest, initial_pending = _initial_merge(manifest, comparison, disk)
            legacy_newer = set()
            if before is None and slug not in self.adopting:
                # A database match newer than the folder's snapshot wins even when the folder has an older file of
                # the same name; its source is published below and that older file is not judged (#101 review).
                legacy_newer = _database_newer(net, proj, manifest, stored_revision)
                changed.difference_update(f"src/{name}.c" for name in legacy_newer)
            projected = baseline.projected if baseline is not None else _Snapshot.of(comparison)
            # A helper can finish another function just before a pull. Publish that independent work before
            # loading the pull; the contributor wins if both changed the same file.
            all_code = False
            affected = None
            judging_change = True  # Unknown import shapes conservatively invalidate in-flight results.
            pending = {}
            if projected is not None:
                rendered = comparison if comparison is not None else saves._render(net, slug)[0]
                previous = projected
                pending = {name: text for name, text in rendered.items()
                           if name not in changed and previous.files.get(name) != _hash(text)
                           and not (name.startswith("src/") and "best/"+name[4:] in changed)
                           and not (name.startswith("best/") and "src/"+name[5:] in changed)
                           and name != "README.md"}
                pending.update(initial_pending)
                if before is None:
                    # A first sync upgrades the folder's manifest in one write: the merge above kept the database's
                    # work, and the new revision/saved_at make later first syncs safe (#101 review).
                    manifest["saved_at"] = time.time()
                    manifest["revision"] = saves._next_revision(net, proj, folder)
                    pending[saves.MANIFEST] = json.dumps(manifest, indent=1) + "\n"
                for name in legacy_newer:
                    source = f"src/{name}.c"
                    if source in rendered:
                        pending[source] = rendered[source]
                if (before is not None and saves.MANIFEST in rendered and previous.manifest is not None
                        and saves.MANIFEST in disk and previous.manifest != rendered[saves.MANIFEST]):
                    pending[saves.MANIFEST] = json.dumps(
                        _merge_manifest(json.loads(previous.manifest), json.loads(rendered[saves.MANIFEST]),
                                        json.loads(disk[saves.MANIFEST])), indent=1) + "\n"
                removed = {name: None for name in previous.files
                           if name not in changed and name not in rendered}
                candidate = {name: text for name, text in (disk | pending).items() if name not in removed}
                merged_manifest = _manifest(candidate, cfg)
                function_rows = net.store.all('SELECT name,unit,vram,rom_offset FROM functions WHERE project_id=?', pid)
                affected = affected_functions(json.loads(comparison_manifest), merged_manifest, changed, function_rows)
                if before is None:
                    applied_changed = {name for name in set(candidate) | set(comparison)
                                       if candidate.get(name) != comparison.get(name)}
                else:
                    applied_changed = {name for name in set(candidate) | set(before.files)
                                       if _hash(candidate.get(name)) != before.files.get(name)}
                if affected is not None:
                    # Include locally completed C before deciding whether to judge. Adding it
                    # after that decision could reopen a match on a metadata-only pull without
                    # checking its merged source, leaving the intact saved C marked open.
                    affected.update(path.split('/', 1)[1][:-2] for path in applied_changed
                                    if path.startswith(('src/', 'best/')) and path.endswith('.c'))
                all_code = affected is None
                toolchain = (merged_manifest.get("compiler") or {}).get("toolchain")
                if toolchain and not toolchains.known(toolchain):
                    # A folder from another tool can name a compiler this app doesn't have. The project's own
                    # Settings are a compiler the user already picked: use it (and correct the folder) rather than
                    # wait forever for an id that can never be installed.
                    local = cfg.get("compiler")
                    local_toolchain = local.get("toolchain") if isinstance(local, dict) else None
                    if isinstance(local_toolchain, str) and toolchains.known(local_toolchain):
                        merged_manifest["compiler"] = local
                        merged_manifest["saved_at"] = time.time()
                        merged_manifest["revision"] = saves._next_revision(net, proj, folder)
                        pending[saves.MANIFEST] = json.dumps(merged_manifest, indent=1) + "\n"
                        toolchain = local_toolchain
                code_files = any(name.startswith(("src/", "best/")) for name in candidate)
                database_code = not code_files and net.store.one(
                    "SELECT 1 FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL "
                    "LIMIT 1", pid) is not None
                judging_change = all_code or bool(affected) or any(name.startswith(("src/", "best/", "include/"))
                                                 for name in changed)
                needs_judging = judging_change and (code_files or database_code)
                if needs_judging:
                    reason = ("Shared linking inputs or function layout changed. Saved functions must be rebuilt to confirm their original bytes."
                              if all_code else f"Build inputs changed for {len(affected or [])} functions. Only their saved sources are being rechecked; other matches are retained.")
                    self._activity(slug, reason=reason, full_recheck=all_code or bool(function_rows) and
                                   all(row["name"] in (affected or set()) for row in function_rows))
                if toolchain and needs_judging:
                    if not toolchains.known(toolchain):
                        # #118: no Setup can install a compiler decompdecomp doesn't know. Open the project and
                        # leave the folder's sources unjudged until the user picks one in Settings.
                        self.blocked[slug] = (self._signature(folder), dirty.token, config_key)
                        self.errors[slug] = (f"progress/decompdecomp.json names {toolchain}, which isn't a compiler "
                                             "decompdecomp has. Pick one in this project's Settings; its saved code "
                                             "waits until then.")
                        return False
                    if toolchains.installed(toolchain) is None:
                        raise NetworkError(
                            f"Install {toolchains.get(toolchain).name} ({toolchain}) in Setup before continuing: "
                            "progress/decompdecomp.json's code has to be judged with it.")
                stale_revision = (saves._manifest_revision(merged_manifest)
                                  < saves._stored_revision(cfg))
                if legacy_newer or stale_revision:
                    merged_manifest["saved_at"] = time.time()
                    merged_manifest["revision"] = saves._next_revision(net, proj, folder)
                    pending[saves.MANIFEST] = json.dumps(merged_manifest, indent=1) + "\n"
                applied_files = candidate
                applied_manifest = merged_manifest
            layout_by_name = {f["name"]: f for f in applied_manifest.get("layout", [])}
            layout_by_vram: dict[int, list] = {}
            for entry in applied_manifest.get("layout", []):
                layout_by_vram.setdefault(entry["vram"], []).append(entry)
            try:
                # The layout, the database resets and every applied change are one transaction. An error rolls all
                # of them back; the folder is written inside it, so a failed apply publishes nothing.
                with net.store.tx() as db:
                    rows = db.execute("SELECT id,name,vram,n_insns FROM functions WHERE project_id=?",
                                      (pid,)).fetchall()
                    for row in rows:
                        impacted = all_code or row["name"] in (affected or set())
                        changed_source = f"src/{row['name']}.c" in applied_changed
                        changed_best = f"best/{row['name']}.c" in applied_changed
                        keep_backup = False
                        keep_best = False
                        if changed_source or impacted:
                            old = db.execute("SELECT status,matched_source,matched_agent_id,best_source,best_score,"
                                             "best_agent_id FROM functions WHERE id=?", (row["id"],)).fetchone()
                            if old["status"] == "matched" and old["matched_source"]:
                                # An all_code change (a header or the layout) can't be judged yet if the compiler is
                                # missing or the restore is cancelled. The last match stays as the best attempt until
                                # the new source is judged (#101 review).
                                keep_backup = True
                                db.execute("UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=?",
                                           (old["matched_source"], max(1, row["n_insns"] or 1) * 100,
                                            old["matched_agent_id"], row["id"]))
                            elif impacted and not changed_best and old["best_source"] is not None:
                                # Nothing about this function's own code changed: rejudging can only replace or lose
                                # the attempt already recorded, so keep it.
                                keep_best = True
                        if impacted or changed_source or changed_best:
                            entry = layout_by_name.get(row["name"])
                            if entry is None or entry["vram"] != row["vram"]:
                                candidates = layout_by_vram.get(row["vram"], [])
                                entry = candidates[0] if len(candidates) == 1 else None
                            status = (entry or {}).get("status", "open")
                            keep = keep_backup or keep_best
                            db.execute("UPDATE functions SET status=?, "
                                       "matched_source=NULL,matched_agent_id=NULL,matched_at=NULL,readable=0,"
                                       "best_source=CASE WHEN ? THEN best_source ELSE NULL END,"
                                       "best_score=CASE WHEN ? THEN best_score ELSE NULL END,"
                                       "best_agent_id=CASE WHEN ? THEN best_agent_id ELSE NULL END WHERE id=?",
                                       (status, keep, keep, keep, row["id"]))
                            db.execute("DELETE FROM claims WHERE function_id=?", (row["id"],))
                    # A source-only edit must not replay the whole note history. Compare
                    # with the fresh database render, retaining concurrent new notes too.
                    restore_notes = applied_manifest.get('notes', []) != json.loads(rendered[saves.MANIFEST]).get('notes', [])
                    if restore_notes:
                        db.execute("DELETE FROM notes WHERE project_id=?", (pid,))
                    db.execute("DELETE FROM knowledge WHERE project_id=? AND status='accepted'", (pid,))
                    units = set(applied_manifest.get("headers") or {})
                    for row in db.execute("SELECT DISTINCT unit FROM context_versions WHERE project_id=?",
                                          (pid,)).fetchall():
                        if row["unit"] and row["unit"] not in units:
                            db.execute("DELETE FROM context_versions WHERE project_id=? AND unit=?",
                                       (pid, row["unit"]))
                    with compiler.cancellable(report.check_cancel):
                        by_name, _ = saves._apply_context(net, slug, folder, applied_manifest, report,
                                                         files=applied_files, restore_notes=restore_notes)
                    report.check_cancel()
                    if not saves._publish(folder, pending, removed, expected=disk,
                                          chosen=bool(cfg.get("progress_folder"))):
                        raise _FolderChanged
            except _FolderChanged:
                return False
            # Notes/instructions do not change the bytes being judged. Invalidating on every
            # metadata import discarded concurrent compiles even though their inputs survived.
            if judging_change:
                self.revisions[slug] = self.revisions.get(slug, 0) + 1
            self.published[slug] = set(pending) | set(removed)
            disk = _read(folder)
            manifest = _manifest(disk, cfg)
            saves._remember_revision(net, slug, manifest)
            saved = manifest.get("functions") or {}
            if affected is not None and needs_judging:
                saved = {name: info for name, info in saved.items() if name in affected}
            self.results[slug], recheck = self._judge_saved(slug, folder, manifest, by_name,
                                                    saved, report,
                                                    files=disk, judge_code=needs_judging)
            self._activity(slug, phase="Saving verified progress", function=None)
            net.bump_generation(pid)
            proj = net._project(slug)
            # `_apply_context` and the judge write matches and notes of their own: acknowledge the writes this
            # sync's final render saw.
            applied_dirty = self._dirty(pid)
            applied_tokens = [r["token"] for r in self._dirty_files(pid)]
            rendered, _ = saves._render(net, slug)
            # The import judge can promote a draft or reject a former match. Publish both
            # path transitions before acknowledging its dirty tokens, so the saved verdict
            # and source location agree with the judge. Move only the unchanged input C.
            promoted = {name: text for name, text in rendered.items()
                        if name.startswith("src/") and name not in disk
                        and "best/"+name[4:] in disk
                        and saves._source_text(disk["best/"+name[4:]]) == text}
            demoted = {name: text for name, text in rendered.items()
                       if name.startswith("best/") and name not in disk
                       and "src/"+name[5:] in disk
                       and saves._source_text(disk["src/"+name[5:]]) == text}
            # Foreground work may replace an existing saved C while this recheck compiles.
            # Publish only its affected files, guarded against an editor's newer disk contents.
            foreground_names = recheck.functions if recheck is not None else set()
            foreground_files = {name: text for name, text in rendered.items()
                                if ((name.startswith(('src/', 'best/')) and name.split('/', 1)[1][:-2] in foreground_names)
                                    or (name.startswith('include/') and recheck is not None and recheck.foreground))
                                and text != disk.get(name)}
            if promoted or demoted or (recheck is not None and recheck.foreground):
                obsolete = {"best/"+name[4:]: disk["best/"+name[4:]] for name in promoted}
                obsolete.update({"src/"+name[5:]: disk["src/"+name[5:]] for name in demoted})
                obsolete.update({name: text for name, text in disk.items()
                                 if name.startswith(("src/", "best/")) and name.split("/", 1)[1][:-2] in foreground_names
                                 and name not in rendered})
                transitions = promoted | demoted | foreground_files
                transitions[saves.MANIFEST] = rendered[saves.MANIFEST]
                if not saves._publish(folder, transitions, obsolete, expected=disk,
                                      chosen=bool(cfg.get("progress_folder"))):
                    return False  # an editor wrote meanwhile; leave dirty tokens for the retry
                disk = _read(folder)
                saves._remember_revision(net, slug, json.loads(rendered[saves.MANIFEST]))
                proj = net._project(slug)
                self.published[slug].update(transitions.keys() | obsolete.keys())
                self.results[slug] = self.results[slug] | {
                    "matched": sum(name.startswith("src/") for name in rendered),
                    "best": sum(name.startswith("best/") for name in rendered)}
            self.projected[slug] = rendered
            self.disk[slug] = disk
            self._remember(pid, disk, rendered, _config_key(proj, net.cfg(proj)),
                           applied_dirty.token, applied_tokens)
            if baseline is not None and net.cfg(proj).get('auto_commit_matches'):
                # The restore judge temporarily reopens existing matches. Queue only genuine
                # promotions, after attribution is restored to the contributing agent.
                from . import match_commits
                old = json.loads(baseline.projected.manifest or '{}').get('functions', {})
                new = json.loads(rendered[saves.MANIFEST]).get('functions', {})
                promoted_names = [name for name, info in new.items()
                                  if 'matched_by' in info and 'matched_by' not in old.get(name, {})]
                for name in promoted_names:
                    row = net.store.one('SELECT id FROM functions WHERE project_id=? AND name=?', pid, name)
                    if row:match_commits.enqueue(net, proj, row['id'], restored=True)
            return True
        if baseline is None:
            return self._publish_initial(slug, proj, folder, disk, dirty, config_key)
        return False
