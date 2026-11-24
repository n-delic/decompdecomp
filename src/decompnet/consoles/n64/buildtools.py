"""Use an already-installed matching IDO compiler when a public project's local compiler build is absent."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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


def command(project, argv: list[str]) -> list[str]:
    if os.name == "nt":
        return argv  # these compiler binaries run inside WSL on Windows
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
    declared = re.search(r"^[ \t]*(IDO|CC)[ \t]*[:?]?=[ \t]*([^\s#]*/(?:5\.3|7\.1)/(?:[^\s#]*/)?cc)[ \t]*(?:#.*)?$", text, re.M)
    if not declared:
        return argv
    name, path = declared[1], declared[2]
    # Resolve only unique literal declarations (usually TOOLS := tools), never Make functions or conditions.
    for variable in re.findall(r"\$\((\w+)\)", path):
        values = set(re.findall(rf"^{re.escape(variable)}\s*[:?]?=\s*([^\s#$]+)\s*$", text, re.M))
        if len(values) == 1:
            path = path.replace(f"$({variable})", values.pop())
    version = re.search(r"/(5\.3|7\.1)/", path)
    if "$" in path or not path.endswith("/cc") or not version or (project.root / path).exists():
        return argv
    tid = "ido-" + version[1]
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
