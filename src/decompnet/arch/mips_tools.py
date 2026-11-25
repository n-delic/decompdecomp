"""Expose installed MIPS binutils under the prefixes public Makefiles search for, only in their build PATH."""

from __future__ import annotations

import os
import shutil
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
                if tool == "ld":
                    # these binutils know the big-endian emulation as elf32ebmip; Makefiles written for a
                    # distribution's cross ld ask for elf32btsmip (the same o32 big-endian format)
                    wrapper = (f'#!/bin/sh\nfor a; do shift; case "$a" in elf32btsmip) a=elf32ebmip;; esac; '
                               f'set -- "$@" "$a"; done\nexec "{binary.resolve()}" "$@"\n')
                    if not alias.is_file() or alias.is_symlink() or alias.read_text(errors="replace") != wrapper:
                        alias.unlink(missing_ok=True)
                        alias.write_text(wrapper, encoding="utf-8")
                        alias.chmod(0o755)
                    continue
                if alias.is_symlink() and alias.resolve() == binary.resolve():
                    continue
                temporary = folder / ("." + alias.name + ".tmp")
                temporary.unlink(missing_ok=True)
                temporary.symlink_to(binary.resolve())
                temporary.replace(alias)
        # Projects preprocess with the cross gcc's cpp, which binutils don't include: the host's cpp (clang's on a Mac)
        # does the same job, under the same prefixes; a GNU cpp (Homebrew's gcc) is preferred, as projects pass it
        # gcc-only options such as -traditional.
        gnu = sorted((m for d in ("/opt/homebrew/bin", "/usr/local/bin") for m in Path(d).glob("cpp-[0-9]*")),
                     key=lambda m: m.name)
        if host_cpp := (str(gnu[-1]) if gnu else None) or shutil.which("cpp"):
            for prefix in _PREFIXES:
                shim = folder / (prefix + "cpp")
                if not shim.exists():
                    shim.write_text(f'#!/bin/sh\nexec "{host_cpp}" "$@"\n', encoding="utf-8")
                    shim.chmod(0o755)
        # GNU coreutils' nproc (a Mac has sysctl instead), which Makefiles use to pick their -j
        if not shutil.which("nproc"):
            tool = folder / "nproc"
            if not tool.exists():
                tool.write_text('#!/bin/sh\nexec sysctl -n hw.ncpu\n', encoding="utf-8")
                tool.chmod(0o755)
        # Makefiles check the ROM with GNU coreutils' sha1sum, which a Mac lacks (shasum takes the same -c file)
        if not shutil.which("sha1sum") and (shasum := shutil.which("shasum")):
            tool = folder / "sha1sum"
            if not tool.exists():
                tool.write_text(f'#!/bin/sh\nexec "{shasum}" -a 1 "$@"\n', encoding="utf-8")
                tool.chmod(0o755)
    return [folder, source]
