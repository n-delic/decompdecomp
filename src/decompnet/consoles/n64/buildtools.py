"""Use an already-installed matching IDO compiler when a public project's local compiler build is absent."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ... import toolchains


def command(project, argv: list[str]) -> list[str]:
    if os.name == "nt":
        return argv  # these compiler binaries run inside WSL on Windows
    if Path(argv[0]).name not in ("make", "gmake") or any(a.startswith("IDO=") for a in argv[1:]):
        return argv
    makefile = project.root / "Makefile"
    if not makefile.is_file():
        return argv
    text = makefile.read_text(encoding="utf-8", errors="replace")
    declared = re.search(r"^IDO\s*[:?]?=\s*([^\s#]+)", text, re.M)
    if not declared:
        return argv
    path = declared[1]
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
    return [*argv, "IDO=" + (alias / "cc").relative_to(project.root).as_posix()]
