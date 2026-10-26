"""The compiler a ROM project builds each function with: its compile command, from the project's config."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import toolchains, wsl
from .compiler import CompilerConfig


def compiler_for(cfg: dict, vram: int | None) -> dict:
    """The compiler a function at `vram` is built with: its address range's, for a game built by more than one (GTA
    San Andreas: EE-GCC 2.95.3, and CodeWarrior 3.0.1 -O3 for some of its code), else the project's."""
    for r in cfg.get("compiler_ranges") or []:
        if vram is not None and r["start"] <= vram < r["end"]:
            return {**cfg["compiler"], "toolchain": r["toolchain"], "flags": r["flags"]}
    return cfg["compiler"]


def unknown_compiler_message(toolchain: str) -> str:
    """What a project whose config names a compiler decompdecomp doesn't have says wherever it surfaces (compiling,
    autopilot): what's wrong and the one thing to do about it. The registry's own "known: ..." list named 50 ids and
    no way out (#118: a project from another tool opened with that error)."""
    return (f"this project's compiler {toolchain!r} isn't one decompdecomp has: pick a compiler in this project's "
            "Settings, then try again")


def compiler_config(root: Path, cfg: dict) -> CompilerConfig:
    """Build the sandboxed compile command for a project.

    Either `cmd` (a full command with {src} {obj} {include} placeholders) or `toolchain` (a registry id,
    see toolchains.toml) plus optional `flags`. With a toolchain, `{tc}` expands to its install directory,
    which is mounted read-only into the sandbox together with any runtime it requires."""
    c = cfg["compiler"]
    ro_paths = [str((root / p).resolve()) for p in c.get("ro_paths", [])]
    runtime32 = None
    linux = False
    prelude, cplusplus = "", False
    if "toolchain" in c:
        try:
            tc = toolchains.get(c["toolchain"])
        except toolchains.ToolchainError:
            raise toolchains.ToolchainError(unknown_compiler_message(c["toolchain"])) from None
        prelude, cplusplus = tc.prelude, tc.cplusplus
        linux = tc.via_wsl  # Windows: a Linux toolchain, run inside WSL 2
        if linux and not (ok := wsl.status())[0]:
            raise toolchains.ToolchainError(f"{tc.name} is a Linux program: on Windows it runs in WSL 2. {ok[1]}")
        dirs = toolchains.runtime_dirs(tc.id)
        try:
            where_tools = wsl.prepare_tools(dirs) if linux else tuple(str(directory) for directory in dirs)
        except (OSError, subprocess.SubprocessError) as error:
            raise toolchains.ToolchainError(f"{tc.name} couldn't be prepared in WSL: {error}") from None
        tc_dir = where_tools[0]
        ro_paths += [str(d) for d in dirs]
        if "runtime-i386" in tc.requires and not linux:  # in WSL its programs bring the runtime themselves
            runtime32 = str(toolchains.require("runtime-i386"))
        flags = c.get("flags", tc.flags)
        if c.get("cmd"):
            base = c["cmd"]
        elif tc.cmd:  # a full template ({flags} expands to the flags)
            base = [x for a in tc.cmd for x in (flags if a == "{flags}" else [a])]
        else:
            base = [*tc.cc, *flags, "-I{include}", "{src}", "-o", "{obj}"]
        cmd = [a.replace("{tc}", tc_dir) for a in base]
        for dep, where in zip(tc.requires, where_tools[1:]):  # {tc:<id>} is another toolchain this one needs
            cmd = [a.replace(f"{{tc:{dep}}}", where) for a in cmd]
    else:
        cmd = list(c["cmd"])
    return CompilerConfig(
        cmd=cmd,
        timeout=float(c.get("timeout", 15)),
        sandbox=c.get("sandbox", "auto"),

        ro_paths=tuple(ro_paths),
        include_dirs=tuple(str((root / p).resolve()) for p in c.get("include_dirs", [])),
        runtime32=runtime32,
        wsl=linux,
        prelude=prelude,
        cplusplus=cplusplus,
    )
