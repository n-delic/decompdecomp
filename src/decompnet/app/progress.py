"""Keep ROM progress files authoritative, with SQLite as the engine's working representation.

Only memory records what was last read and rendered. There is no second save, index or cache on disk. Local
changes write only their changed files; a pull's files are read before any write, including after a restart.
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
    for pattern in (saves.MANIFEST, "src/*.c", "best/*.c", "include/*.h"):
        for path in folder.glob(pattern):
            if path.is_symlink():
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

    @contextmanager
    def rebuilding(self, slug: str):
        """An explicitly requested reimport changes the game's fingerprint and layout together with its progress."""
        self.sync(slug, force=True)
        with self.lock:
            self.local.busy = True
            try:
                yield
                folder = saves.default_folder(self.net, slug)
                rendered = saves._render(self.net, slug)[0]
                saves._publish(folder, rendered, self.disk.get(slug))
                self.disk[slug] = _read(folder)
                self.projected[slug] = rendered
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

    @property
    def applying(self) -> bool:
        return getattr(self.local, "busy", False)

    def sync(self, slug: str, *, force: bool = False, logger=None) -> bool:
        """Read contributions first, then publish only work changed locally since the last synchronization."""
        if self.applying or getattr(self.net.store._local, "in_tx", False):
            return False
        with self.lock:
            if not force and time.monotonic() - self.checked.get(slug, 0) < 1:
                return False
            self.local.busy = True
            try:
                result = self._sync(slug, logger)
                self.checked[slug] = time.monotonic()
                return result
            except (OSError, UnicodeError, json.JSONDecodeError) as e:
                raise NetworkError("Could not read or write progress. Finish the git operation and check the folder's permissions.") from e
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
        before = self.disk.get(slug)
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
            changed = set(disk) | set(comparison)
            changed = {name for name in changed if disk.get(name) != comparison.get(name)}
            # A helper can finish another function just before a pull. Publish that independent work before
            # loading the pull; the contributor wins if both changed the same file.
            if slug in self.projected:
                rendered = saves._render(net, slug)[0]
                previous = self.projected[slug]
                pending = {name: text for name, text in rendered.items()
                           if name not in changed and previous.get(name) != text and name != "README.md"}
                removed = {name: text for name, text in previous.items()
                           if name not in changed and name not in rendered}
                if not saves._publish(folder, pending, removed, expected=disk):
                    return False
                disk = _read(folder)
                manifest = _manifest(disk, net.cfg(proj))
                changed = {name for name in set(disk) | set(comparison) if disk.get(name) != comparison.get(name)}
            all_code = any(name.startswith("include/") for name in changed)
            if saves.MANIFEST in changed:
                old = json.loads(comparison[saves.MANIFEST])
                all_code |= any(old.get(key) != manifest.get(key) for key in
                                ("compiler", "compiler_ranges", "layout", "symbols", "headers"))
            self.revisions[slug] = self.revisions.get(slug, 0) + 1
            with net.store.tx() as db:
                layout = {f["vram"]: f for f in manifest.get("layout", [])}
                rows = db.execute("SELECT id,name,vram FROM functions WHERE project_id=?", (proj["id"],)).fetchall()
                for row in rows:
                    if all_code or any(f"{sub}/{row['name']}.c" in changed for sub in ("src", "best")):
                        status = layout.get(row["vram"], {}).get("status", "open")
                        db.execute("UPDATE functions SET status=?, "
                                   "matched_source=NULL,matched_agent_id=NULL,matched_at=NULL,readable=0,"
                                   "best_source=NULL,best_score=NULL,best_agent_id=NULL WHERE id=?", (status, row["id"]))
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
            return True
        rendered, _ = saves._render(net, slug)
        previous = self.projected.get(slug, {})
        changes = {name: text for name, text in rendered.items() if previous.get(name) != text}
        removed = {name: text for name, text in previous.items() if name not in rendered}
        # Metadata added by a contributor survives an unrelated local note or match.
        if saves.MANIFEST in changes and saves.MANIFEST in disk and saves.MANIFEST in previous:
            old, new, current = (json.loads(text) for text in
                                 (previous[saves.MANIFEST], rendered[saves.MANIFEST], disk[saves.MANIFEST]))
            for key in set(old) | set(new):
                if old.get(key) != new.get(key):
                    current[key] = new.get(key)
            changes[saves.MANIFEST] = json.dumps(current, indent=1) + "\n"
        if changes or removed:
            if not saves._publish(folder, changes, removed, expected=disk):
                return False
        self.projected[slug] = rendered
        # Record only the snapshot actually applied or written. Reading again here could acknowledge an edit
        # that arrived during the write without ever applying it, so the next poll would miss it forever.
        self.disk[slug] = {name: text for name, text in (disk | changes).items()
                           if name != "README.md" and name not in removed}
        return bool(changes or removed)
