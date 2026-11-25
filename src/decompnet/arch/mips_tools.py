"""Expose installed MIPS binutils under the prefixes public Makefiles search for, only in their build PATH."""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

from .. import toolchains

_LOCK = threading.Lock()
_PREFIXES = ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-")
_ASSEMBLER_DRIVER = '''#!{python}
import os, subprocess, sys, tempfile
args, out, sources, cpp, asm = sys.argv[1:], None, [], [], []
i = 0
while i < len(args):
    a = args[i]
    if a == "-o":
        out = args[i + 1]; i += 1
    elif a in ("-x",):
        i += 1
    elif a in ("-I", "-MF", "-MT", "-MQ", "-include", "-isystem", "-iquote", "-D", "-U"):
        cpp += [a, args[i + 1]]; i += 1
        if a == "-I":
            asm += ["-I", args[i]]
    elif a == "-G":
        asm += [a, args[i + 1]]; i += 1
    elif a.startswith(("-D", "-U", "-I", "-MD", "-MMD", "-MP", "-nostdinc", "-include")):
        cpp.append(a)
        if a.startswith("-I"):  # (the assembler reads .include files too)
            asm.append(a)
    elif a.startswith(("-march=", "-mabi=", "-mtune=", "-mips", "-EB", "-EL")):
        asm.append(a)
    elif a.startswith("-"):
        pass
    else:
        sources.append(a)
    i += 1
if not out or len(sources) != 1:
    sys.exit("mips gcc shim: needs -o and one source")
with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as t:
    done = subprocess.run(["{cpp}", "-x", "assembler-with-cpp", *cpp, sources[0]], stdout=t)
if done.returncode:
    sys.exit(done.returncode)
code = subprocess.run(["{assembler}", *asm, "-o", out, t.name]).returncode
os.unlink(t.name)
sys.exit(code)
'''


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
        # `mips-linux-gnu-gcc -x assembler-with-cpp -c x.s`: the gcc driver's one job in these builds, which binutils
        # alone can't do: preprocess with the host's cpp, then assemble with the pinned `as`
        if host_cpp:
            for prefix in _PREFIXES:
                driver = folder / (prefix + "gcc")
                if not driver.exists():
                    driver.write_text(_ASSEMBLER_DRIVER.format(python=sys.executable, cpp=str(host_cpp),
                                                               assembler=str(folder / (prefix + "as"))), encoding="utf-8")
                    driver.chmod(0o755)
        # GNU coreutils' nproc (a Mac has sysctl instead), which Makefiles use to pick their -j
        if not shutil.which("nproc"):
            tool = folder / "nproc"
            if not tool.exists():
                tool.write_text('#!/bin/sh\nexec sysctl -n hw.ncpu\n', encoding="utf-8")
                tool.chmod(0o755)
        # Makefiles check the ROM with GNU coreutils' sha1sum, which a Mac lacks (shasum takes the same -c file)
        # (macOS's own /sbin/sha1sum has no --check, so the shim goes first there too)
        if (not shutil.which("sha1sum") or sys.platform == "darwin") and (shasum := shutil.which("shasum")):
            tool = folder / "sha1sum"
            if not tool.exists():
                tool.write_text(f'#!/bin/sh\nfor a; do shift; case "$a" in --check) a=-c;; --quiet) a=-q;; esac; '
                                f'set -- "$@" "$a"; done\nexec "{shasum}" -a 1 "$@"\n', encoding="utf-8")
                tool.chmod(0o755)
    return [folder, source]
