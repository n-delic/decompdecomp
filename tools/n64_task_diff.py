"""n64_task_diff.py CONSOLE_DUMP PORT_DUMP [--work FOLDER]: what differs between the display lists the console and the port
ran for the same graphics task, with the pointers left out.

A picture that differs (tools/n64_frame_compare.py) says that the game ran differently, not where. Each dump has its task
beside it (NAME.task, from --frames); the task's display list is run through the port's renderer on each side
(PORT_DLTRACE prints every command), and the commands are compared with their address words masked ("PTR") (frame buffers,
vertex lists and textures sit elsewhere on each side): what's left is values the game computed. The first differing
commands are printed, so the one function that wrote them can be found (the emulator's --watch / --break, with
`python -m decompnet.consoles.n64.emulator`)."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n64_frame_compare import build, with_task  # noqa: E402

VIRTUAL = re.compile(r"^80[0-7][0-9A-F]{5}$")  # a RAM address as the game's lists hold it
SET_IMAGE = {"FD", "FE", "FF"}  # G_SETTIMG, G_SETZIMG, G_SETCIMG: their second word is an address (physical, too)


def commands(trace: str) -> list[tuple[str, str]]:
    """[(w0, w1)] from PORT_DLTRACE's "ADDRESS W0 W1" lines, a word that is a RAM address masked as "PTR"."""
    out = []
    for line in trace.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(re.fullmatch(r"[0-9A-Fa-f]{8}", p) for p in parts):
            w0, w1 = parts[1].upper(), parts[2].upper()
            out.append((w0, "PTR" if VIRTUAL.match(w1) or w0[:2] in SET_IMAGE else w1))
    return out


def differences(a: list, b: list) -> list[tuple[int, tuple, tuple]]:
    """[(index, console's command, port's)] where the two lists differ, a missing command as ("-", "-")."""
    gap = ("-", "-")
    return [(i, a[i] if i < len(a) else gap, b[i] if i < len(b) else gap) for i in range(max(len(a), len(b)))
            if (a[i] if i < len(a) else gap) != (b[i] if i < len(b) else gap)]


def trace(program: Path, dump: Path, work: Path) -> str:
    placed = with_task(dump, work)
    if not placed:
        raise SystemExit(f"{dump}: no {dump.name}.task beside it (dumps from tools/n64_frame_compare.py --frames have one)")
    env = {**os.environ, "PORT_DLTRACE": "1"}
    done = subprocess.run([str(program), str(placed[0]), f"{placed[1]:x}"], env=env, capture_output=True, text=True,
                          timeout=120)
    return done.stdout + done.stderr


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare the display lists the console and the port ran for one task.")
    p.add_argument("console", type=Path)
    p.add_argument("port", type=Path)
    p.add_argument("--work", type=Path, help="where the renderer is built (default: a temporary folder)")
    p.add_argument("--show", type=int, default=25, help="how many differing commands to print (default 25)")
    args = p.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="n64-task-diff-") as tmp:
        work = args.work or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        program = build(work)
        a, b = commands(trace(program, args.console, work)), commands(trace(program, args.port, work))
    found = differences(a, b)
    print(f"{len(a)} commands on the console, {len(b)} in the port, {len(found)} differ (addresses masked)")
    for i, x, y in found[:args.show]:
        print(f"command {i}: {x[0]} {x[1]}  |  {y[0]} {y[1]}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
