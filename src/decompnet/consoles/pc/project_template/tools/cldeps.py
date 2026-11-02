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
from pathlib import Path

NOTE = "Note: including file:"
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
    out, sep, cmd = sys.argv[1], sys.argv[2], sys.argv[3:]
    if sep != "--" or not cmd:
        sys.exit("usage: cldeps.py OUT -- COMMAND...")
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    deps: dict[str, None] = {}
    shown = []
    for line in res.stdout.splitlines():
        if line.startswith(NOTE):
            if (p := project_path(line[len(NOTE):])) is not None:
                deps[p] = None
        else:
            shown.append(line)
    if shown:
        print("\n".join(shown))
    if res.stderr:
        sys.stderr.write(res.stderr)
    if res.returncode == 0:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out + ".d").write_text(f"{escape(out)}: " + " ".join(escape(d) for d in deps) + "\n", encoding="utf-8")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
