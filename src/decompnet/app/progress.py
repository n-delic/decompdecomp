"""Keep ROM progress files authoritative, with SQLite as the engine's working representation.

SQLite stores the last successfully synchronized disk and database snapshots, so a restart can distinguish an old
checkout from a contributor's deletion or edit. Local changes write only their changed files; a pull is validated
before its files or database state are changed.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .. import toolchains
from ..network import NetworkError
from ..rom.symbols import IDENT
from . import saves


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
        self.signatures: dict[str, tuple] = {}
        self.baselines: dict[int, tuple[dict[str, str], dict[str, str]]] = {}
        self.sync_changes: dict[str, int] = {}
        self.adopting: set[str] = set()

    @staticmethod
    def _signature(folder: Path) -> tuple:
        items = []
        for name in (saves.MANIFEST, "src", "best", "include"):
            path = folder / name
            try:
                stat = path.stat(follow_symlinks=False)
                items.append((name, stat.st_mtime_ns, stat.st_size, stat.st_mode))
                if path.is_dir() and not path.is_symlink():
                    for child in sorted(path.iterdir()):
                        stat = child.stat(follow_symlinks=False)
                        items.append((child.relative_to(folder).as_posix(), stat.st_mtime_ns, stat.st_size, stat.st_mode))
            except OSError:
                items.append((name, None))
        return tuple(items)

    def _project_lock(self, slug: str):
        with self.lock:
            return self.project_locks.setdefault(slug, threading.RLock())

    def _baseline(self, project_id: int) -> tuple[dict[str, str], dict[str, str]] | None:
        if project_id in self.baselines:
            return self.baselines[project_id]
        row = self.net.store.one("SELECT disk,projected FROM progress_state WHERE project_id=?", project_id)
        if row is None:
            return None
        result = (json.loads(row["disk"]), json.loads(row["projected"]))
        self.baselines[project_id] = result
        return result

    def _remember(self, project_id: int, disk: dict[str, str], projected: dict[str, str]) -> None:
        with self.net.store.tx() as db:
            db.execute("INSERT INTO progress_state(project_id,disk,projected,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(project_id) DO UPDATE SET disk=excluded.disk,projected=excluded.projected,"
                       "updated_at=excluded.updated_at",
                       (project_id, json.dumps(disk), json.dumps(projected), time.time()))
        self.baselines[project_id] = (disk, projected)

    def forget(self, slug: str, project_id: int) -> None:
        """Drop in-memory state with the database row so a later project can safely reuse its slug or ID."""
        with self._project_lock(slug):
            self.baselines.pop(project_id, None)
            for cache in (self.disk, self.projected, self.checked, self.results, self.revisions,
                          self.signatures, self.sync_changes, self.errors):
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
                rendered = saves._render(self.net, slug)[0]
                if not saves._publish(folder, rendered, self.disk.get(slug)):
                    raise NetworkError("Progress changed while the project was being rebuilt. Read it again and retry.")
                self.disk[slug] = _read(folder)
                self.projected[slug] = rendered
                project_id = self.net.store.one("SELECT id FROM projects WHERE slug=?", slug)["id"]
                self._remember(project_id, self.disk[slug], rendered)
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
        """Read contributions first, then publish only work changed locally since the last synchronization."""
        if self.applying or getattr(self.net.store._local, "in_tx", False):
            return False
        with self._project_lock(slug):
            if (not force and time.monotonic() - self.checked.get(slug, 0) < 0.25
                    and self.sync_changes.get(slug) == self.net.store.conn.total_changes):
                return False
            self.local.busy = True
            try:
                proj = self.net._project(slug)
                if self.net.is_rom(proj) and self.net.cfg(proj).get("rom", {}).get("sha1"):
                    folder = saves.default_folder(self.net, slug)
                    signature = (self._signature(folder), self.net.store.conn.total_changes)
                    if self._baseline(proj["id"]) is not None and self.signatures.get(slug) == signature:
                        return False
                result = self._sync(slug, logger)
                self.checked[slug] = time.monotonic()
                self.sync_changes[slug] = self.net.store.conn.total_changes
                if self.net.is_rom(proj):
                    folder = saves.default_folder(self.net, slug)
                    signature = self._signature(folder)
                    acknowledged = self.disk.get(slug) is not None and _read(folder) == self.disk.get(slug)
                    stable = signature == self._signature(folder)
                    if acknowledged and stable:
                        self.signatures[slug] = (signature, self.net.store.conn.total_changes)
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

    def _sync(self, slug: str, logger=None) -> bool:
        net = self.net
        proj = net._project(slug)
        if not net.is_rom(proj):
            return False  # PROJECT mode's source files already belong to its build and git repository.
        if not net.cfg(proj).get("rom", {}).get("sha1") or net._context(proj["id"]) is None:
            return False  # import has not registered the game's fingerprint and shared header yet
        folder = saves.default_folder(net, slug)
        disk = _read(folder)
        baseline = self._baseline(proj["id"])
        before = baseline[0] if baseline is not None else self.disk.get(slug)
        if baseline is not None:
            self.disk.setdefault(slug, baseline[0])
            self.projected.setdefault(slug, baseline[1])
        if saves.MANIFEST not in disk and (before is not None or net.cfg(proj).get("progress_folder")
                                           or disk or (folder / ".git").exists()):
            raise NetworkError("Restore progress/decompdecomp.json before continuing this project.")
        if saves.MANIFEST in disk and disk != before:
            manifest = _manifest(disk, net.cfg(proj))
            report = logger or (lambda line: saves.log.info("%s: %s", slug, line))
            getattr(report, "check_cancel", lambda: None)()
            compiler = manifest.get("compiler") or {}
            if compiler.get("toolchain") and toolchains.installed(compiler["toolchain"]) is None:
                raise NetworkError("Install the compiler named in progress/decompdecomp.json through Setup before continuing.")
            # Preserve work on unrelated functions even if a helper finished while a contribution was pulled.
            comparison = before if before is not None else saves._render(net, slug)[0]
            if before is None:
                self.projected.setdefault(slug, comparison)
            changed = set(disk) | set(comparison)
            changed = {name for name in changed if disk.get(name) != comparison.get(name)}
            initial_pending = {}
            if before is None and slug not in self.adopting:
                changed = {name for name in changed if name in disk}
                manifest, initial_pending = _initial_merge(manifest, comparison, disk)
            legacy_newer = set()
            saved_at = manifest.get("saved_at")
            if before is None and slug not in self.adopting and isinstance(saved_at, (int, float)):
                legacy_newer = {row["name"] for row in net.store.all(
                    "SELECT name,matched_at FROM functions WHERE project_id=? AND status='matched' AND matched_at>?",
                    proj["id"], saved_at) if f"src/{row['name']}.c" not in disk}
                changed.difference_update(f"src/{name}.c" for name in legacy_newer)
            # A helper can finish another function just before a pull. Publish that independent work before
            # loading the pull; the contributor wins if both changed the same file.
            if slug in self.projected:
                rendered = saves._render(net, slug)[0]
                previous = self.projected[slug]
                pending = {name: text for name, text in rendered.items()
                           if name not in changed and previous.get(name) != text and name != "README.md"}
                pending.update(initial_pending)
                if before is None:
                    pending[saves.MANIFEST] = json.dumps(manifest, indent=1) + "\n"
                for name in legacy_newer:
                    source = f"src/{name}.c"
                    if source in rendered:
                        pending[source] = rendered[source]
                if (saves.MANIFEST in rendered and saves.MANIFEST in previous and saves.MANIFEST in disk
                        and previous[saves.MANIFEST] != rendered[saves.MANIFEST]):
                    pending[saves.MANIFEST] = json.dumps(
                        _merge_manifest(json.loads(previous[saves.MANIFEST]), json.loads(rendered[saves.MANIFEST]),
                                        json.loads(disk[saves.MANIFEST])), indent=1) + "\n"
                removed = {name: text for name, text in previous.items()
                           if name not in changed and name not in rendered}
                candidate = {name: text for name, text in (disk | pending).items() if name not in removed}
                merged_manifest = _manifest(candidate, net.cfg(proj))
                if legacy_newer:
                    pending[saves.MANIFEST] = json.dumps(merged_manifest, indent=1) + "\n"
                selected_compiler = merged_manifest.get("compiler") or {}
                if (selected_compiler.get("toolchain")
                        and toolchains.installed(selected_compiler["toolchain"]) is None):
                    raise NetworkError("Install the compiler named in progress/decompdecomp.json through Setup before continuing.")
                if not saves._publish(folder, pending, removed, expected=disk):
                    return False
                disk = _read(folder)
                manifest = _manifest(disk, net.cfg(proj))
                changed = {name for name in set(disk) | set(comparison) if disk.get(name) != comparison.get(name)}
            all_code = any(name.startswith("include/") for name in changed)
            if saves.MANIFEST in changed:
                old = json.loads(comparison[saves.MANIFEST])
                all_code |= any(old.get(key) != manifest.get(key) for key in
                                ("compiler", "compiler_ranges", "symbols", "headers", "set_aside"))
                if old.get("layout") != manifest.get("layout"):
                    old_layout = {f["name"]: (f["vram"], f["size"]) for f in old.get("layout", [])}
                    new_layout = {f["name"]: (f["vram"], f["size"]) for f in manifest.get("layout", [])}
                    all_code |= old_layout != new_layout
            self.revisions[slug] = self.revisions.get(slug, 0) + 1
            with net.store.tx() as db:
                layout = {f["vram"]: f for f in manifest.get("layout", [])}
                rows = db.execute("SELECT id,name,vram,n_insns FROM functions WHERE project_id=?", (proj["id"],)).fetchall()
                for row in rows:
                    changed_source = f"src/{row['name']}.c" in changed
                    changed_best = f"best/{row['name']}.c" in changed
                    keep_backup = False
                    if changed_source or all_code:
                        old = db.execute("SELECT status,matched_source,matched_agent_id FROM functions WHERE id=?",
                                         (row["id"],)).fetchone()
                        if old["status"] == "matched" and old["matched_source"] and changed_source:
                            keep_backup = True
                            db.execute("UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=?",
                                       (old["matched_source"], max(1, row["n_insns"] or 1) * 100,
                                        old["matched_agent_id"], row["id"]))
                    if all_code or changed_source or changed_best:
                        status = layout.get(row["vram"], {}).get("status", "open")
                        db.execute("UPDATE functions SET status=?, "
                                   "matched_source=NULL,matched_agent_id=NULL,matched_at=NULL,readable=0,"
                                   "best_source=CASE WHEN ? THEN best_source ELSE NULL END,"
                                   "best_score=CASE WHEN ? THEN best_score ELSE NULL END,"
                                   "best_agent_id=CASE WHEN ? THEN best_agent_id ELSE NULL END WHERE id=?",
                                   (status, keep_backup, keep_backup, keep_backup, row["id"]))
                        db.execute("DELETE FROM claims WHERE function_id=?", (row["id"],))
                db.execute("DELETE FROM notes WHERE project_id=?", (proj["id"],))
                db.execute("DELETE FROM knowledge WHERE project_id=? AND status='accepted'", (proj["id"],))
                units = set(manifest.get("headers") or {})
                for row in db.execute("SELECT DISTINCT unit FROM context_versions WHERE project_id=?", (proj["id"],)).fetchall():
                    if row["unit"] and row["unit"] not in units:
                        db.execute("DELETE FROM context_versions WHERE project_id=? AND unit=?", (proj["id"], row["unit"]))
            self.results[slug] = saves._apply(net, slug, folder, manifest, report, files=disk)
            net.bump_generation(proj["id"])
            self.projected[slug] = saves._render(net, slug)[0]
            self.disk[slug] = disk
            self._remember(proj["id"], disk, self.projected[slug])
            return True
        rendered, _ = saves._render(net, slug)
        previous = self.projected.get(slug, {})
        changes = {name: text for name, text in rendered.items() if previous.get(name) != text}
        removed = {name: text for name, text in previous.items() if name not in rendered}
        # Metadata added by a contributor survives an unrelated local note or match.
        if saves.MANIFEST in changes and saves.MANIFEST in disk and saves.MANIFEST in previous:
            old, new, current = (json.loads(text) for text in
                                 (previous[saves.MANIFEST], rendered[saves.MANIFEST], disk[saves.MANIFEST]))
            changes[saves.MANIFEST] = json.dumps(_merge_manifest(old, new, current), indent=1) + "\n"
        if changes or removed:
            if not saves._publish(folder, changes, removed, expected=disk):
                return False
        self.projected[slug] = rendered
        # Record only the snapshot actually applied or written. Reading again here could acknowledge an edit
        # that arrived during the write without ever applying it, so the next poll would miss it forever.
        self.disk[slug] = {name: text for name, text in (disk | changes).items()
                           if name != "README.md" and name not in removed}
        self._remember(proj["id"], self.disk[slug], rendered)
        return bool(changes or removed)
