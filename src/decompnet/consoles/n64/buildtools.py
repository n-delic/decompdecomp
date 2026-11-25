"""Use an already-installed matching IDO compiler when a public project's local compiler build is absent."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ... import toolchains


def _crc_tool(root: Path) -> None:
    """Public N64 projects fix the ROM's checksum with tools/n64crc, which their `make dependencies` compiles next to
    a whole IDO build. Compile just that one file when it's missing, so the final link step has its tool (a
    failure here stays silent: the project's own error then says what it lacks)."""
    source, tool = root / "tools" / "n64crc.c", root / "tools" / "n64crc"
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if source.is_file() and not tool.exists() and cc:
        try:
            subprocess.run([cc, str(source), "-o", str(tool)], capture_output=True, timeout=120,
                           stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def _ido_hint(project) -> str:
    """Which IDO a project that doesn't name it in a path wants: what its README or Makefile says, else 5.3 (most
    N64 games)."""
    said = ""
    for name in ("README.md", "Makefile"):
        try:
            said += (project.root / name).read_text(errors="replace", encoding="utf-8")
        except OSError:
            pass
    mention = re.search(r"(?i)ido[ _-]*(?:version\s*)?v?(5\.3|7\.1)", said)
    return mention[1] if mention else "5.3"


def _wrap_foreign_ido(project) -> None:
    """On a Mac a project's own copy of IDO is usually a Linux program (its CI's) its build can't run ("Exec format
    error"). Replace each such `cc` driver, once, with a script that runs the installed IDO of the version its
    path names (5.3 or 7.1); the original is kept beside it as cc.linux. Only drivers that are Linux ELF files
    are touched, so a project's own Mac build, or its own makefile's redirect, stays as it was."""
    if sys.platform != "darwin":
        return
    done = project.root / ".decomp-wtf" / "build-tools" / ".ido-wrapped"
    tools = project.root / "tools"
    if done.exists() or not tools.is_dir():
        return
    for driver in tools.glob("**/cc"):
        if len(driver.relative_to(tools).parts) > 5 or driver.is_symlink() or not driver.is_file():
            continue
        try:
            with open(driver, "rb") as f:
                magic = f.read(4)
        except OSError:
            continue
        if magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf"):
            # a project's own Mac IDO whose files lost their execute bit (a zip, or Windows, on the way to git)
            for program in driver.parent.iterdir():
                if program.is_file() and not program.is_symlink() and not program.stat().st_mode & 0o111:
                    with open(program, "rb") as f:
                        if f.read(4) == magic:
                            program.chmod(0o755)
            continue
        if magic != b"\x7fELF":
            continue
        version = re.search(r"(?<!\d)(5)[._](3)(?!\d)|(?<!\d)(7)[._](1)(?!\d)", str(driver.relative_to(tools)))
        if version:
            tid = "ido-" + ("5.3" if version[1] else "7.1")
        else:  # tools/ido/cc: the README or Makefile says which
            tid = "ido-" + _ido_hint(project)
        if toolchains.installed(tid) is None:
            continue  # compiler installation and license acceptance stay in Setup
        source = toolchains.require(tid) / "cc"
        if not source.is_file():
            continue
        driver.rename(driver.with_name("cc.linux"))
        driver.write_text(f'#!/bin/sh\nexec "{source}" "$@"\n', encoding="utf-8")
        driver.chmod(0o755)
    # The same for a project's other committed Linux programs (tools/pigment64, ...) when the host has the tool:
    # `cargo install` and the system's own packages are where those come from.
    path = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".cargo" / "bin")])
    for tool in tools.glob("*"):
        if not tool.is_file() or tool.is_symlink() or not tool.stat().st_mode & 0o111 or tool.suffix:
            continue
        try:
            with open(tool, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        host = shutil.which(tool.name, path=path)
        if host and Path(host).resolve() != tool.resolve():
            tool.rename(tool.with_name(tool.name + ".linux"))
            tool.write_text(f'#!/bin/sh\nexec "{host}" "$@"\n', encoding="utf-8")
            tool.chmod(0o755)
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_text("", encoding="utf-8")


def command(project, argv: list[str]) -> list[str]:
    argv = _redirect(project, argv)
    makefile = project.root / "Makefile"
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("RUN_CC_CHECK=") for a in argv[1:])
            and re.search(r"^RUN_CC_CHECK\s*[:?]?=", makefile.read_text(encoding="utf-8", errors="replace"), re.M)):
        argv = [*argv, "RUN_CC_CHECK=0"]  # the project's own switch for its host-gcc pass (clang answers gcc here)
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("CC_CHECK=") for a in argv[1:])
            and re.search(r"^CC_CHECK\s*[:?]?=\s*gcc\b[^\n]*-fsyntax-only",
                          makefile.read_text(encoding="utf-8", errors="replace"), re.M)):
        # a host-gcc syntax pass before each compile: clang answers `gcc` here and rejects its -m32 headers; the
        # real compile (IDO) is what builds the game
        argv = [*argv, "CC_CHECK=true"]
    return argv


def _redirect(project, argv: list[str]) -> list[str]:
    if os.name == "nt":
        return argv  # these compiler binaries run inside WSL on Windows
    _wrap_foreign_ido(project)
    if Path(argv[0]).name not in ("make", "gmake") or any(a.startswith(("IDO=", "CC=")) for a in argv[1:]):
        return argv
    makefile = project.root / "Makefile"
    if not makefile.is_file():
        return argv
    _crc_tool(project.root)
    text = makefile.read_text(encoding="utf-8", errors="replace")
    # The variable is usually IDO; some projects name it CC, indented inside an if/else (a community decomp of a
    # racing game builds its compiler in tools/ and picks it with `CC := ...` under `else`). A command-line
    # value beats the makefile's own assignment, so one override reaches both spellings.
    declared = re.search(r"^[ \t]*(IDO|CC)[ \t]*[:?]?=[ \t]*([^\s#]*(?:/(?:5\.3|7\.1)/|ido)(?:[^\s#]*/)?cc)[ \t]*(?:#.*)?$",
                         text, re.M)
    if not declared:
        return argv
    name, path = declared[1], declared[2]
    # Resolve only unique literal declarations (usually TOOLS := tools), never Make functions or conditions.
    for variable in re.findall(r"\$\((\w+)\)", path):
        values = set(re.findall(rf"^{re.escape(variable)}\s*[:?]?=\s*([^\s#$]+)\s*$", text, re.M))
        if len(values) == 1:
            path = path.replace(f"$({variable})", values.pop())
    version = re.search(r"/(5\.3|7\.1)/", path)
    if "$" in path or not path.endswith("/cc") or (project.root / path).exists():
        return argv
    tid = "ido-" + (version[1] if version else _ido_hint(project))
    if toolchains.installed(tid) is None:
        return argv  # compiler installation and license acceptance stay in Setup
    source = toolchains.require(tid)
    if not (source / "cc").is_file():
        return argv
    # A relative directory link keeps sibling compiler programs available and avoids spaces in the build command.
    tools = project.root / ".decomp-wtf" / "build-tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / ".gitignore").write_text("*\n", encoding="utf-8")
    alias = tools / tid
    if not alias.exists():
        alias.symlink_to(source, target_is_directory=True)
    if not alias.is_symlink() or alias.resolve() != source.resolve():
        return argv
    return [*argv, name + "=" + (alias / "cc").relative_to(project.root).as_posix()]
