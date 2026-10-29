"""Keep ROM progress files authoritative, with SQLite as the engine's working representation.

SQLite stores the last successfully synchronized disk and database snapshots as one hash per file, so a restart can
distinguish an old checkout from a contributor's deletion or edit without keeping two full copies of the sources.
Triggers (store.DIRTY_TRIGGERS, knowledge.SCHEMA) mark the columns a render reads, so a local match writes its file
and the manifest, while a claim, an event or an attempt that didn't improve the best writes nothing. A pull is
validated before its files or database state are changed.

Change detection guarantee: a non-forced check compares the manifest's and the three folders' stats and may skip an
in-place file edit until the one-second autosave; a forced check (judging, tools, routes, saves, the autosave) compares
every file's name, size and mtime, so an editor's in-place write (which leaves its directory's mtime alone) is seen
before code is judged or a write is acknowledged. A file hash in the baseline is always compared for every file a
sync is about to write or remove.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .. import toolchains
from ..network import NetworkError
from ..rom.symbols import IDENT
from . import saves

CHECK_SECONDS = 0.25  # a non-forced check of an unchanged folder is this rare per project
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
                cfg.get("compiler_ranges") or [], cfg.get("instructions") or "", cfg.get("priority") or [],
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


def _read(folder: Path) -> dict[str, str]:
    files = {}
    for directory in ("src", "best", "include"):
        path = folder / directory
        if path.is_symlink():
            raise NetworkError("Progress folders must be regular directories. Replace the symbolic link and try again.")
    for pattern in (saves.MANIFEST, "src/*.c", "best/*.c", "include/*.h"):
        for path in folder.glob(pattern):
            if path.is_symlink() or not path.is_file():
                raise NetworkError("Progress files must be regular files. Replace the symbolic link and try again.")
            text = path.read_text(encoding="utf-8")
            if any(line.startswith(("<<<<<<<", "=======", ">>>>>>>")) for line in text.splitlines()):
                raise NetworkError("Resolve the git conflicts in progress before continuing this project.")
            files[path.relative_to(folder).as_posix()] = text
    return files


def _manifest(files: dict[str, str], cfg: dict) -> dict:
    try:
        manifest = json.loads(files[saves.MANIFEST])
        if manifest["format"] != saves.FORMAT or manifest["game"]["sha1"] != cfg["rom"]["sha1"]:
            raise ValueError
        for file in (manifest.get("headers") or {}).values():
            if Path(file).name != file or "include/" + file not in files:
                raise ValueError
        layout = {f["name"]: f for f in manifest.get("layout", [])}
        if len(layout) != len(manifest.get("layout", [])):
            raise ValueError
        for name, entry in layout.items():
            if (not IDENT.fullmatch(name) or not isinstance(entry["vram"], int)
                    or not isinstance(entry["size"], int) or entry["size"] <= 0
                    or entry.get("status", "open") not in ("open", "asm")):
                raise ValueError
        compiler = manifest.get("compiler")
        if compiler is not None and not isinstance(compiler, dict):
            raise ValueError
        if not isinstance(manifest.get("symbols", []), list) or not isinstance(manifest.get("notes", []), list) \
                or not isinstance(manifest.get("knowledge", []), list) \
                or not isinstance(manifest.get("set_aside", []), list):
            raise ValueError
        for symbol in manifest.get("symbols", []):
            if (not isinstance(symbol, dict) or not IDENT.fullmatch(symbol.get("name", ""))
                    or not isinstance(symbol.get("address"), int) or not isinstance(symbol.get("size", 0), int)
                    or symbol.get("kind", "data") not in ("func", "data", "string", "jtbl", "bss")):
                raise ValueError
        for note in manifest.get("notes", []):
            if (not isinstance(note, dict) or not isinstance(note.get("text"), str)
                    or note.get("function") is not None and not isinstance(note.get("function"), str)
                    or not isinstance(note.get("by", ""), str)):
                raise ValueError
        if any(not isinstance(item, str) for item in manifest.get("knowledge", [])):
            raise ValueError
        for item in manifest.get("set_aside", []):
            if (not isinstance(item, dict) or not IDENT.fullmatch(item.get("name", ""))
                    or not isinstance(item.get("vram"), int)
                    or any(key in item and not isinstance(item[key], str) for key in ("asm_code", "asm_twin"))
                    or "not_c" in item and (not isinstance(item["not_c"], dict)
                        or any(not isinstance(value, dict)
                               or not all(isinstance(value.get(field), str if field != "t" else (int, float))
                                          for field in ("model", "why", "t"))
                               for value in item["not_c"].values()))):
                raise ValueError
        if "" not in (manifest.get("headers") or {}):
            raise ValueError
        for name in files:
            if name.startswith(("src/", "best/")) and Path(name).stem not in layout:
                raise NetworkError(f"Add {Path(name).stem} to the function layout in progress/decompdecomp.json before continuing.")
        functions = {}
        for name, info in layout.items():
            if Path(name).name != name or "\\" in name or "/" in name:
                raise ValueError
            previous = manifest.get("functions", {}).get(name, {})
            if f"src/{name}.c" in files:
                functions[name] = {"vram": info["vram"], "matched_by": previous.get("matched_by", ""),
                                   "readable": previous.get("readable", False)}
            elif f"best/{name}.c" in files:
                functions[name] = {"vram": info["vram"], "best_by": previous.get("best_by", "")}
        manifest["functions"] = functions
        return manifest
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NetworkError("Fix progress/decompdecomp.json: it must describe this game and reference existing headers.") from None


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
                return item.get("vram") if key == "set_aside" else item.get("name")
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
                  "knowledge": lambda item: item, "set_aside": lambda item: item.get("vram")}
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


class Progress:
    def __init__(self, net):
        self.net = net
        self.lock = threading.RLock()
        self.local = threading.local()
        self.disk: dict[str, dict[str, str]] = {}
        self.projected: dict[str, dict[str, str]] = {}
        self.checked: dict[str, float] = {}
        self.results: dict[str, dict] = {}
        self.revisions: dict[str, int] = {}
        self.project_locks: dict[str, threading.RLock] = {}
        self.errors: dict[str, str] = {}
        self.signatures: dict[str, tuple] = {}  # slug -> every file's stat after the last acknowledged sync
        self.stamps: dict[str, tuple] = {}  # slug -> the cheap directory/manifest stat a non-forced check compares
        self.published: dict[str, set[str]] = {}  # slug -> the files a sync just wrote or removed
        self.baselines: dict[int, _Baseline | None] = {}
        self.states: dict[int, _State | None] = {}
        self.sync_changes: dict[str, int] = {}  # kept for restore, which drops every in-memory trace of a project
        self.adopting: set[str] = set()
        self.blocked: dict[str, tuple] = {}  # slug -> (folder, dirty token, settings) while a compiler waits

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

    def forget(self, slug: str, project_id: int) -> None:
        """Drop in-memory state with the database row so a later project can safely reuse its slug or ID."""
        with self._project_lock(slug):
            self.baselines.pop(project_id, None)
            self.states.pop(project_id, None)
            for cache in (self.disk, self.projected, self.checked, self.results, self.revisions,
                          self.signatures, self.stamps, self.published, self.sync_changes, self.errors, self.blocked):
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

    def sync(self, slug: str, *, force: bool = False, logger=None) -> bool:
        """Read contributions first, then publish only work changed locally since the last synchronization.

        A no-op non-forced check stats the manifest and the three folders; a forced check stats every file too, so an
        in-place edit an editor made (which leaves a directory's mtime alone) is never missed by judging, a tool call,
        the routes or the autosave. Nothing is rendered or written unless the folder or a column the render reads
        changed."""
        if self.applying or getattr(self.net.store._local, "in_tx", False):
            return False
        with self._project_lock(slug):
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
                cached_signature = self.signatures.get(slug)
                result = self._sync(slug, logger)
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
                return result
            except (OSError, UnicodeError, json.JSONDecodeError) as e:
                message = "Could not read or write progress. Finish the git operation and check the folder's permissions."
                self.errors[slug] = message
                raise NetworkError(message) from e
            except NetworkError as e:
                self.errors[slug] = str(e)
                raise
            finally:
                self.local.busy = False

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
        else:
            possible = {f"src/{name}.c" for name in names} | {f"best/{name}.c" for name in names}
            if flags.headers:
                possible |= {name for name in baseline.projected.files if name.startswith("include/")}
            removed = {name: None for name in possible
                       if name in baseline.projected.files and name not in files}
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
            manifest = _manifest(disk, cfg)
            report = logger or (lambda line: saves.log.info("%s: %s", slug, line))
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
            pending = {}
            if projected is not None:
                rendered = comparison if comparison is not None else saves._render(net, slug)[0]
                previous = projected
                pending = {name: text for name, text in rendered.items()
                           if name not in changed and previous.files.get(name) != _hash(text)
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
                all_code = any(name.startswith("include/") for name in changed)
                if saves.MANIFEST in changed:
                    old = json.loads(comparison_manifest)
                    all_code |= any(old.get(key) != merged_manifest.get(key) for key in
                                    ("compiler", "compiler_ranges", "symbols", "headers", "set_aside"))
                    if old.get("layout") != merged_manifest.get("layout"):
                        old_layout = {f["name"]: (f["vram"], f["size"]) for f in old.get("layout", [])}
                        new_layout = {f["name"]: (f["vram"], f["size"]) for f in merged_manifest.get("layout", [])}
                        all_code |= old_layout != new_layout
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
                code_files = any(name.startswith(("src/", "best/")) for name in disk)
                database_code = not code_files and net.store.one(
                    "SELECT 1 FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL "
                    "LIMIT 1", pid) is not None
                judging_change = all_code or any(name.startswith(("src/", "best/", "include/"))
                                                 for name in changed)
                needs_judging = judging_change and (code_files or database_code)
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
                if before is None:
                    applied_changed = {name for name in set(applied_files) | set(comparison)
                                       if applied_files.get(name) != comparison.get(name)}
                else:
                    applied_changed = {name for name in set(applied_files) | set(before.files)
                                       if _hash(applied_files.get(name)) != before.files.get(name)}
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
                        changed_source = f"src/{row['name']}.c" in applied_changed
                        changed_best = f"best/{row['name']}.c" in applied_changed
                        keep_backup = False
                        keep_best = False
                        if changed_source or all_code:
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
                            elif all_code and not changed_best and old["best_source"] is not None:
                                # Nothing about this function's own code changed: rejudging can only replace or lose
                                # the attempt already recorded, so keep it.
                                keep_best = True
                        if all_code or changed_source or changed_best:
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
                    db.execute("DELETE FROM notes WHERE project_id=?", (pid,))
                    db.execute("DELETE FROM knowledge WHERE project_id=? AND status='accepted'", (pid,))
                    units = set(applied_manifest.get("headers") or {})
                    for row in db.execute("SELECT DISTINCT unit FROM context_versions WHERE project_id=?",
                                          (pid,)).fetchall():
                        if row["unit"] and row["unit"] not in units:
                            db.execute("DELETE FROM context_versions WHERE project_id=? AND unit=?",
                                       (pid, row["unit"]))
                    by_name, _ = saves._apply_context(net, slug, folder, applied_manifest, report,
                                                     files=applied_files)
                    if not saves._publish(folder, pending, removed, expected=disk,
                                          chosen=bool(cfg.get("progress_folder"))):
                        raise _FolderChanged
            except _FolderChanged:
                return False
            self.revisions[slug] = self.revisions.get(slug, 0) + 1
            self.published[slug] = set(pending) | set(removed)
            disk = _read(folder)
            manifest = _manifest(disk, cfg)
            saves._remember_revision(net, slug, manifest)
            self.results[slug] = saves._judge_saved(net, slug, folder, manifest, by_name,
                                                    manifest.get("functions") or {}, report,
                                                    files=disk, judge_code=needs_judging)
            net.bump_generation(pid)
            proj = net._project(slug)
            # `_apply_context` and the judge write matches and notes of their own: acknowledge the writes this
            # sync's final render saw.
            applied_dirty = self._dirty(pid)
            applied_tokens = [r["token"] for r in self._dirty_files(pid)]
            rendered, _ = saves._render(net, slug)
            self.projected[slug] = rendered
            self.disk[slug] = disk
            self._remember(pid, disk, rendered, _config_key(proj, net.cfg(proj)),
                           applied_dirty.token, applied_tokens)
            return True
        if baseline is None:
            return self._publish_initial(slug, proj, folder, disk, dirty, config_key)
        return False
