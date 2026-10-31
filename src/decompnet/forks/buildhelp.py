"""Actionable hints for build errors without changing a project's tools or files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..hostenv import NO_WINDOW

_MISSING = re.compile(r"No such file or directory|command not found|not found|is not recognized as an internal or external command", re.I)
_CPP = re.compile(r"[\w./\\+-]+-cpp(?:\.exe)?", re.I)


def explain(root: Path, executable: str, output: str) -> str:
    """Explain missing cross tools seen in output; inspect Makefiles only for Make-specific clues."""
    hints = []
    for line in output.splitlines():
        if not _MISSING.search(line):
            continue
        for tool in _CPP.findall(line):
            name = Path(tool.replace('\\', '/')).name
            hints.append(f"Missing cross preprocessor: {name}. It is part of the project's cross-GCC toolchain, "
                         "not binutils. Install/configure the cross toolchain required by this project's build "
                         "instructions. decompdecomp has not substituted another preprocessor or compiler.")

    if Path(executable).stem.lower() not in ('make', 'gmake', 'mingw32-make'):
        return '\n\n'.join(dict.fromkeys(hints))
    try:
        text = (root / 'Makefile').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '\n\n'.join(dict.fromkeys(hints))

    # Old Make skips != side effects. Only suggest checking it when the build mentions a missing path or target.
    if re.search(r'^\s*[\w.-]+\s*!=', text, re.M) and re.search(
            r'No such file or directory|No rule to make target|cannot create', output, re.I):
        try:
            result = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                    errors='replace', timeout=5, creationflags=NO_WINDOW)
            version = re.search(r'GNU Make (\d+)\.(\d+)', result.stdout)
            if version and tuple(map(int, version.groups())) < (4, 0):
                hints.append(f"Check the GNU Make version: this Makefile uses shell assignments (!=), which "
                             f"require GNU Make 4.0 or newer; the selected tool reports {version[0]}. "
                             "An older Make may skip commands that prepare build directories or tools.")
        except (OSError, subprocess.TimeoutExpired):
            pass

    # A declaration in an inactive Make branch is not evidence. Require the missing path in the failure output.
    for value in re.findall(r'^\s*(?:CC|IDO)\s*[:?]?=\s*([^\s#$]+)\s*$', text, re.M):
        if ('/' in value or '\\' in value) and not Path(value).is_absolute() and not (root / value).is_file():
            if any(value in line and _MISSING.search(line) for line in output.splitlines()):
                hints.append(f"The build could not find its declared compiler at {value}. Follow the project's "
                             "setup instructions for the exact compiler and version; this path alone does not "
                             "identify a matching compiler release.")
    return '\n\n'.join(dict.fromkeys(hints))
