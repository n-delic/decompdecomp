"""Content-bound evidence for the installed tools a linked-image proof used."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .. import toolchains


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(tid: str) -> str | None:
    """Hash a pinned compiler and its declared installed dependencies.

    This checks installed content, not just the registry's archive fingerprint.
    The builder must compare snapshots before and after compilation. A caller must
    separately bind its source, flags, headers, app code and any host runtime.
    User-owned kits, Wine and VM/WSL runtimes need their own evidence and are not
    claimed by this helper. Return None when a complete snapshot is unavailable.
    """
    digest = hashlib.sha256()
    states: dict[str, bool] = {}

    def add(value: str) -> None:
        data = value.encode('utf-8')
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)

    def visit(name: str, optional: bool = False) -> bool:
        if name in states:
            return states[name] or optional
        tc = toolchains.get(name)
        manifest = toolchains.installed(name)
        add(name)
        if manifest is None:
            add('absent')
            states[name] = False
            return optional
        if manifest.get('own') or tc.wine or tc.via_vm or tc.via_wsl:
            return False
        installed_path = toolchains.path(name)
        directory = installed_path.resolve()
        if (installed_path.is_symlink() or not directory.is_relative_to(toolchains.root().resolve())
                or not directory.is_dir()):
            return False
        add(repr((tc.id, tc.cc, tc.flags, tc.cmd, tc.prelude, tc.intrinsics, tc.driver,
                  tc.source_extension, tc.cplusplus, tc.runtime_from)))
        states[name] = True  # a repeated dependency or a cycle needs no second traversal
        def fail(error):
            raise error
        for base, dirs, files in os.walk(directory, followlinks=False, onerror=fail):
            dirs.sort()
            files.sort()
            folder = Path(base)
            # Following links would let a changed external runtime escape the proof.
            if any((folder / part).is_symlink() for part in (*dirs, *files)):
                return False
            for part in dirs:
                add('directory:' + (folder / part).relative_to(directory).as_posix())
            for part in files:
                file = folder / part
                before = file.stat()
                add(file.relative_to(directory).as_posix())
                add(str(before.st_mode))
                add(_file_hash(file))
                after = file.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    return False
        return all(visit(dep) for dep in tc.requires) and all(visit(dep, True) for dep in tc.optional)

    try:
        return digest.hexdigest() if visit(tid) else None
    except (OSError, ValueError, TypeError, AttributeError, toolchains.ToolchainError):
        return None
