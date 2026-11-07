#!/usr/bin/env python3
"""Run Visual C++ with /showIncludes and write the project headers it read as a depfile, for ninja.

    python tools/cldeps.py OUT -- cl.exe ... /showIncludes /FoOUT SOURCE

The compiler names every file it includes ("Note: including file: include\\core/maths.h"); those inside the project
(include/, harness/, other units' sources a driver includes) go into OUT.d, so ninja rebuilds an object when one of
them changes and only then. The compiler's and the SDK's own headers are left out. The notes aren't shown; the rest of
the compiler's output is. Standard library only. Made by decompdecomp.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

NOTE = "Note: including file:"
SECONDS = 300  # tools/compile.py's COMPILE_SECONDS (each file of a project's tools runs on its own)
ROOT = Path.cwd().resolve()


def project_path(raw: str) -> str | None:
    """The included file as a path relative to the project, or None outside it."""
    path = raw.strip().replace("\\", "/")
    if sys.platform != "win32" and path[:2].lower() == "z:":
        path = path[2:]  # wibo shows the host's / as drive Z: (files a source includes by its own folder)
    p = Path(path)
    if not p.is_absolute() and not (len(path) > 1 and path[1] == ":"):
        p = ROOT / p
    try:
        rel = Path(os.path.normpath(p)).resolve().relative_to(ROOT)
    except (ValueError, OSError):
        return None
    return rel.as_posix() if (ROOT / rel).is_file() else None


def escape(path: str) -> str:
    return path.replace("\\", "\\\\").replace(" ", "\\ ").replace("#", "\\#").replace("$", "$$")


def main() -> int:
    out, rest = sys.argv[1], sys.argv[2:]
    seconds = SECONDS
    if rest[:1] == ["--timeout"] and len(rest) > 1:
        seconds, rest = float(rest[1]), rest[2:]
    sep, cmd = rest[:1], rest[1:]
    if sep != ["--"] or not cmd:
        sys.exit("usage: cldeps.py OUT [--timeout SECONDS] -- COMMAND...")
    # the compiler's output to a file, not a pipe: under wine, the services the first program after a new server
    # starts keep what it was given as long as the server runs, and given a pipe they kept this waiting for its end
    # (tools/compile.py). Its own time limit: the compiler is this one's child, and stopped here it's really stopped
    # (a limit on this script alone left a compiler stuck in a loop running on)
    with tempfile.TemporaryFile() as said:
        try:
            code = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=said, stderr=subprocess.STDOUT,
                                  timeout=seconds).returncode
        except subprocess.TimeoutExpired:
            code = None
        said.seek(0)
        text = said.read().decode("utf-8", "replace")
    deps: dict[str, None] = {}
    shown = []
    for line in text.splitlines():
        if line.startswith(NOTE):
            if (p := project_path(line[len(NOTE):])) is not None:
                deps[p] = None
        else:
            shown.append(line)
    if shown:
        print("\n".join(shown))
    if code is None:
        print(f"cldeps.py: the compiler took over {seconds:g} seconds and was stopped", file=sys.stderr)
        return 1
    if code == 0:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out + ".d").write_text(f"{escape(out)}: " + " ".join(escape(d) for d in deps) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
