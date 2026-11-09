"""Expose installed MIPS binutils under the prefixes public Makefiles search for, only in their build PATH."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .. import toolchains

_LOCK = threading.Lock()
_PREFIXES = ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-")


def build_paths(project) -> list[Path]:
    # On Windows these pinned tools run inside WSL, not in a native process's PATH.
    if os.name == "nt" or toolchains.installed("binutils-mips") is None:
        return []
    source = toolchains.require("binutils-mips")
    binaries = list(source.glob("mips-ps2-decompals-*"))
    if not binaries:
        return []
    folder = project.root / ".decomp-wtf" / "build-tools" / "mips-binutils"
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        (folder.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        for binary in binaries:
            if not binary.is_file():
                continue
            tool = binary.name.removeprefix("mips-ps2-decompals-")
            for prefix in _PREFIXES:
                alias = folder / (prefix + tool)
                if alias.is_symlink() and alias.resolve() == binary.resolve():
                    continue
                temporary = folder / ("." + alias.name + ".tmp")
                temporary.unlink(missing_ok=True)
                temporary.symlink_to(binary.resolve())
                temporary.replace(alias)
    return [folder, source]
