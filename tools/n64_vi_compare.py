"""n64_vi_compare.py ROM --port PORT_BUILD [--tasks N] [--input vi:BUTTON,...|@file]: when the console's VI showed nothing
(blanked) and when the port's did, in graphics tasks, from the same input script timed in graphics tasks.

The reversing emulator's video plugin logs the VI's registers each time they change (EMU_VI_LOG). libultra's osViBlack
blanks the picture by writing 0 to the horizontal video register, so the screen is blank while that register's start and
end are the same. The port's PORT_SWAP_LOG says, for each frame it hands the VI, whether osViBlack
had blanked it. A game blanks the VI at start and around a change of video mode, so the two lists of intervals should
agree in number and in length (not in where they start: see differences): that is what keeps the flashes of an
unfinished buffer off the screen. Exit 1 when they don't."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOLERANCE = 12  # tasks a blank may last longer or shorter in the port than on the console


def console_blank(text: str) -> list[tuple[int, int | None]]:
    """[(first task, task it ended at or None)] from EMU_VI_LOG's "task N ... hvideo H" lines: blank while the register's
    start (low 10 bits) and end (the 10 bits from bit 16) are the same."""
    runs, start = [], None
    for m in re.finditer(r"^task (\d+) .* hvideo ([0-9A-Fa-f]+)", text, re.M):
        h = int(m[2], 16)
        task, blank = int(m[1]), (h & 0x3FF) == (h >> 16) & 0x3FF
        if blank and start is None:
            start = task
        elif not blank and start is not None:
            runs.append((start, task))
            start = None
    if start is not None:
        runs.append((start, None))
    return runs


def port_blank(text: str) -> list[tuple[int, int | None]]:
    """The same from PORT_SWAP_LOG's "frame N task T ... black B" lines."""
    runs, start = [], None
    for m in re.finditer(r"^frame \d+ task (\d+) .* black (\d)", text, re.M):
        task, blank = int(m[1]), m[2] == "1"
        if blank and start is None:
            start = task
        elif not blank and start is not None:
            runs.append((start, task))
            start = None
    if start is not None:
        runs.append((start, None))
    return runs


def differences(console: list, port: list, until: int) -> list[str]:
    """What disagrees: a different number of blanks, or a blank that lasts longer or shorter by more than TOLERANCE
    tasks. Where a blank starts isn't compared: a menu advances when the game polls the pad, which differs a little
    between runs (a screen can come hundreds of tasks apart), but a blank's length is the game's own. Only blanks
    that began before task `until` count (the port wasn't run further)."""
    console = [r for r in console if r[0] < until]
    port = [r for r in port if r[0] < until]
    problems = []
    if len(console) != len(port):
        problems.append(f"{len(console)} blanks on the console, {len(port)} in the port")
    for i, (a, b) in enumerate(zip(console, port), 1):
        if (a[1] is None) != (b[1] is None) or (a[1] is not None and abs((a[1] - a[0]) - (b[1] - b[0])) > TOLERANCE):
            problems.append(f"blank {i}: console tasks {a[0]}-{a[1]}, port {b[0]}-{b[1]}")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare when the console's VI and the port's were blanked.")
    p.add_argument("rom", type=Path)
    p.add_argument("--port", type=Path, required=True, help="the port's native build (port/wasm/build/game)")
    p.add_argument("--tasks", type=int, default=3000, help="how many graphics tasks to compare (default 3000)")
    p.add_argument("--input", default="", help="vi:BUTTON+BUTTON,... or @file, timed in graphics tasks on both sides")
    args = p.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decompnet.consoles.n64 import emulator
    script = Path(args.input[1:]).read_text().strip() if args.input.startswith("@") else args.input
    with tempfile.TemporaryDirectory(prefix="n64-vi-") as tmp:
        tmp = Path(tmp)
        os.environ["EMU_VI_LOG"] = str(tmp / "vi.log")
        vis = args.tasks * 2 + 600
        emulator.run(args.rom, vis, inputs=script, input_clock="frames")
        env = {**os.environ, "PORT_SWAP_LOG": str(tmp / "swaps.log"), "PORT_INPUT_CLOCK": "frames"}
        env.pop("EMU_VI_LOG", None)
        if script:
            env["PORT_INPUT"] = script
        subprocess.run([str(args.port.resolve()), str(args.rom.resolve()), str(vis // 40 + 20)], cwd=tmp, env=env,
                       capture_output=True, timeout=vis + 600)
        console = console_blank((tmp / "vi.log").read_text()) if (tmp / "vi.log").exists() else []
        port = port_blank((tmp / "swaps.log").read_text()) if (tmp / "swaps.log").exists() else []
    print(f"console blank (tasks): {console}\nport blank (tasks):    {port}")
    found = differences(console, port, args.tasks)
    for line in found:
        print(line)
    print("the console and the port blank the VI at the same points" if not found else f"{len(found)} blanks differ")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
