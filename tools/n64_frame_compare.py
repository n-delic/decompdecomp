"""Compare an N64 port's frames with the original game's, run in the reversing emulator with the same input.

The reversing emulator (mupenRE, consoles/n64/emulator) runs the user's own ROM headless but draws nothing: its video
plugin only finishes each display list. What it can do is write the console's RAM at chosen retraces, and the port
writes its own the same way (PORT_RAM_DUMPS). Each dump holds the game's graphics tasks (libultra's OSTask, the
display list and the microcode's data it names); this draws them with the port's renderer
(src/decompnet/consoles/n64/port_wasm/render.c, which matches angrylion's RDP: tools/n64_rdp_compare.py), so the
console's frame and the port's are drawn by the same renderer from each one's own game state. Where they differ,
the game ran differently (the port's code, its host or its input), not the renderer.

    uv run python tools/n64_frame_compare.py ROM --port path/to/port/build/game --vis 2000,4000 \
        --input @script.txt --out REPORT

The input script is the emulator's and the port's (`vi:BUTTON+BUTTON+SX=n+SY=n,...`), timed in retraces on both
sides (the port runs with PORT_INPUT_CLOCK=vi). Without --port only the console's frames are drawn. For each retrace
and each graphics task found in RAM, it writes the console's image, the port's and their difference (brightened)
side by side, and prints how many pixels differ.

A game builds its next display list while the last one is drawn, so a dump usually holds one finished list and one
half built: the half-built one may run into old data and fail to draw, which is reported and skipped. The two runs
are only as close as their timing: the port's retraces are the host's clock, the emulator's the console's, so a
game that counts time by retraces stays in step, and one whose loading takes longer on one side drifts. Frames that
show different moments of the same scene say little; choose retraces where the game waits (menus, a paused race),
or pair each of the console's retraces with the port's at the same moment (--port-vis), found by a first run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from n64_rdp_compare import RENDER, commands, compare, images, png, rgb  # noqa: E402

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void port_render_task(uint32_t task);
static uint8_t ram[0x800000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
int port_live_active(void) { return 0; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
/* argv: a RAM dump, a task's address: the task drawn with PORT_RDPDUMP set, which writes its commands and memory */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 3;
    size_t n = fread(ram, 1, sizeof ram, f);
    fclose(f);
    if (n < 0x400000) return 3;
    port_render_task((uint32_t)strtoul(argv[2], NULL, 16));
    return 0;
}
"""

RAM = 0x800000


def build(work: Path) -> Path:
    """The port's renderer around a program that draws one task from a RAM dump."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise SystemExit("needs a C compiler (cc, clang or gcc)")
    work.mkdir(parents=True, exist_ok=True)
    (work / "frame.c").write_text(HARNESS)
    program = work / "frame_task"
    subprocess.run([cc, "-O2", "-o", str(program), str(work / "frame.c"), str(RENDER), "-lm"], check=True, timeout=600)
    return program


def _word(mem: bytes, a: int) -> int:
    return struct.unpack_from(">I", mem, a)[0]


def graphics_tasks(mem: bytes) -> list[int]:
    """The RAM addresses of what look like libultra graphics tasks (OSTask, 8-byte aligned): type M_GFXTASK (1), its
    boot code, microcode, microcode data and display list in RAM, the data at most 2 KB, as a microcode's is."""
    size = min(len(mem), RAM)

    def inside(p: int) -> bool:
        return 0x80000000 <= p < 0x80000000 + size

    found = []
    for a in range(0, size - 64, 8):
        if _word(mem, a) != 1:
            continue
        boot, code, data, data_size, dl = (_word(mem, a + 8), _word(mem, a + 16), _word(mem, a + 24),
                                           _word(mem, a + 28), _word(mem, a + 48))
        if all(inside(p) for p in (boot, code, data, dl)) and 0 < data_size <= 0x800 and dl % 8 == 0:
            found.append(0x80000000 | a)
    return found


def draw(program: Path, dump: Path, task: int, work: Path) -> dict[int, tuple[int, int, list]] | None:
    """The images one task draws: {address: (width, lines, RGB pixels)}, or None when it fails to draw."""
    out = work / f"task_{task:08X}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    env = {**os.environ, "PORT_RDPDUMP": str(out), "PORT_RDPDUMP_TASKS": "1"}
    try:
        done = subprocess.run([str(program), str(dump), f"{task:x}"], env=env, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    cmds, after = out / "000001.cmds", out / "000001.after"
    if done.returncode or not cmds.exists() or not after.exists():
        return None
    mem = after.read_bytes()
    drawn = {}
    data = cmds.read_bytes()
    depth = {w[1] & 0xFFFFFF for w in commands(data) if w[0] >> 24 & 0x3F == 0x3E}  # z images: cleared as colour
    for addr, width, siz, lines in images(data):
        if addr in depth:
            continue
        if addr + width * lines * (4 if siz == 3 else 2) <= len(mem):
            drawn[addr] = (width, lines, rgb(mem, addr, width, siz, lines))
    return drawn


def frames(program: Path, dump: Path, work: Path) -> dict[tuple[int, int], tuple[int, int, list]]:
    """Every graphics task in a dump drawn: {(task, image): (width, lines, pixels)}; tasks that fail are reported."""
    out = {}
    mem = dump.read_bytes()
    for task in graphics_tasks(mem):
        drawn = draw(program, dump, task, work)
        if drawn is None:
            print(f"  {dump.name}: the task at {task:08X} didn't draw (a list still being built?)")
            continue
        for image, picture in drawn.items():
            out[(task, image)] = picture
    return out


def retraces_per_second(rom: Path) -> int:
    """50 for a PAL game, 60 otherwise, by the header's region (in any of the three byte orders)."""
    head = rom.read_bytes()[:0x40]
    if head[:4] == b"\x37\x80\x40\x12":
        head = bytes(b for i in range(0, 0x40, 2) for b in (head[i + 1], head[i]))
    elif head[:4] == b"\x40\x12\x37\x80":
        head = b"".join(head[i:i + 4][::-1] for i in range(0, 0x40, 4))
    return 50 if chr(head[0x3E]) in "DFHIPSUWXY" else 60


def run_port(game: Path, rom: Path, vis: list[int], inputs: str, folder: Path) -> dict[int, Path]:
    """The port run (in real time) until the last retrace asked for, writing RAM at each."""
    dumps = {vi: folder / f"port_{vi}.bin" for vi in vis}
    env = {**os.environ, "PORT_RAM_DUMPS": ",".join(f"{vi}:{p}" for vi, p in dumps.items()), "PORT_INPUT_CLOCK": "vi"}
    env.pop("PORT_INPUT", None)
    if inputs:
        env["PORT_INPUT"] = inputs
    seconds = max(vis) / retraces_per_second(rom) + 3
    subprocess.run([str(game.resolve()), str(rom.resolve()), f"{seconds:.0f}"], cwd=folder, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=seconds * 3 + 120)
    return {vi: p for vi, p in dumps.items() if p.exists()}


def run_console(rom: Path, vis: list[int], inputs: str, folder: Path) -> dict[int, Path]:
    from decompnet.consoles.n64 import emulator

    dumps = {vi: folder / f"console_{vi}.bin" for vi in vis}
    emulator.run(rom, max(vis) + 1, inputs=inputs, dumps=dumps)
    return {vi: p for vi, p in dumps.items() if p.exists()}


def side_by_side(path: Path, pictures: list[tuple[int, int, list] | None]) -> None:
    """Pictures left to right (a missing one black), each the first's size."""
    width, lines = next((p[0], p[1]) for p in pictures if p)
    rows = []
    for y in range(lines):
        row = []
        for p in pictures:
            row += p[2][y * width:(y + 1) * width] if p and p[0] == width and p[1] == lines else [(0, 0, 0)] * width
        rows += row
    png(path, rows, width * len(pictures), lines)


def difference(a: list, b: list) -> list:
    return [tuple(min(255, abs(x - y) * 4) for x, y in zip(p, q)) for p, q in zip(a, b)]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare an N64 port's frames with the original game's, run in the "
                                            "reversing emulator with the same input.")
    p.add_argument("rom", type=Path)
    p.add_argument("--port", type=Path, help="the port's native build (port/wasm/build/game); without it, only the "
                                             "console's frames")
    p.add_argument("--vis", required=True, help="retraces to compare at, comma-separated")
    p.add_argument("--port-vis", help="the port's retraces to compare with each of --vis, when the two runs drift "
                                      "apart (the port loads in no time and never drops a frame): the same count")
    p.add_argument("--input", default="", help="vi:BUTTON+BUTTON+SX=n+SY=n,... or @file, timed in retraces")
    p.add_argument("--port-input", help="the port's own script, when the runs drift apart (default: --input's)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--work", type=Path, help="where the renderer is built and the dumps kept (default: a temporary "
                                             "folder)")
    args = p.parse_args(argv)
    vis = [int(v) for v in args.vis.split(",") if v.strip()]
    port_vis = [int(v) for v in args.port_vis.split(",") if v.strip()] if args.port_vis else vis
    if len(port_vis) != len(vis):
        raise SystemExit("--port-vis needs one retrace for each of --vis")
    pairs = dict(zip(vis, port_vis))
    def script(text: str) -> str:
        return Path(text[1:]).read_text().strip() if text.startswith("@") else text
    inputs = script(args.input)
    port_inputs = script(args.port_input) if args.port_input is not None else inputs
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="n64-frames-") as tmp:
        work = args.work or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        program = build(work)
        console = run_console(args.rom, vis, inputs, work)
        port = run_port(args.port, args.rom, sorted(set(port_vis)), port_inputs, work) if args.port else {}
        for vi in vis:
            pv = pairs[vi]
            if vi not in console:
                print(f"VI {vi}: the emulator wrote no RAM (did it run that far?)")
                continue
            ours = frames(program, port[pv], work) if pv in port else {}
            if args.port and pv not in port:
                print(f"VI {pv}: the port wrote no RAM (did it run that far?)")
            theirs = frames(program, console[vi], work)
            for key in sorted(set(theirs) | set(ours)):
                task, image = key
                a, b = theirs.get(key), ours.get(key)
                name = args.out / f"vi{vi:06d}{f'_port{pv:06d}' if pv != vi else ''}_task{task:08X}_image{image:06X}.png"
                if a and b and a[:2] == b[:2]:
                    c = compare(b[2], a[2])
                    side_by_side(name, [a, b, (a[0], a[1], difference(a[2], b[2]))])
                    print(f"VI {vi}{f' (port {pv})' if pv != vi else ''} task {task:08X} image {image:06X} {a[0]}x{a[1]}: {c['differ_pct']:.2f}% of "
                          f"pixels differ, {c['beyond_pct']:.2f}% by more than one step")
                else:
                    side_by_side(name, [a, b])
                    where = "the console's" if a and not b else "the port's" if b and not a else "both (sizes differ)"
                    print(f"VI {vi}{f' (port {pv})' if pv != vi else ''} task {task:08X} image {image:06X}: drawn in {where} only")
        print(f"images in {args.out}: the console's, the port's, their difference (x4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
