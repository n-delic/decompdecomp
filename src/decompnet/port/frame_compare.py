"""Pictures of a port against the console's, drawn by one renderer: the engine of Check pictures (app/picture_check.py)
and of tools/n64_frame_compare.py, which keeps the command line and its long account of why this works.

The reversing emulator (consoles/n64/emulator) draws nothing, but it writes the console's RAM as the game hands over
a graphics task, and the port writes its own the same way (PORT_FRAME_DUMPS). Each dump is drawn here by the port's
renderer (port_wasm/render.c, which matches angrylion's RDP), so a difference is the game running differently (the
port's code, host or input), never the renderer."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path

from ..network import NetworkError

RENDER = Path(__file__).resolve().parents[1] / "consoles/n64/port_wasm/render.c"

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
static int load(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    memset(ram, 0, sizeof ram);
    size_t n = fread(ram, 1, sizeof ram, f);
    fclose(f);
    return n >= 0x400000;
}
/* argv: a RAM dump, a task's address: the task drawn with PORT_RDPDUMP set, which writes its commands and memory.
   Without arguments: lines "dump task" on stdin, each drawn in turn by this one process and answered "ok" or "fail": the
   RDP keeps its colour image, depth image, scissor and modes from one task to the next, as on the console, and a game
   sets them in an earlier task than the one that draws with them (a menu's colour image was only ever set once). */
int main(int argc, char **argv) {
    if (argc >= 3) {
        if (!load(argv[1])) return 3;
        port_render_task((uint32_t)strtoul(argv[2], NULL, 16));
        return 0;
    }
    char line[4096];
    while (fgets(line, sizeof line, stdin)) {
        char *space = strrchr(line, ' ');
        if (!space) { puts("fail"); fflush(stdout); continue; }
        *space = 0;
        if (!load(line)) { puts("fail"); fflush(stdout); continue; }
        port_render_task((uint32_t)strtoul(space + 1, NULL, 16));
        puts("ok"); fflush(stdout);
    }
    return 0;
}
"""


RAM = 0x800000

def build(work: Path) -> Path:
    """The port's renderer around a program that draws one task from a RAM dump."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise NetworkError("Check pictures needs a C compiler on this computer (cc, clang or gcc) to build the renderer.")
    work.mkdir(parents=True, exist_ok=True)
    (work / "frame.c").write_text(HARNESS)
    program = work / "frame_task"
    subprocess.run([cc, "-O2", "-o", str(program), str(work / "frame.c"), str(RENDER), "-lm"], check=True, timeout=600, capture_output=True)
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


class Renderer:
    """One renderer process drawing task after task, the RDP's state carried from each to the next (see HARNESS)."""

    def __init__(self, program: Path, work: Path, tag: str):
        self.dir = work / f"rdp_{tag}"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        env = {**os.environ, "PORT_RDPDUMP": str(self.dir), "PORT_RDPDUMP_TASKS": "1-999999999"}
        self.proc = subprocess.Popen([str(program)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True)
        self.count = 0

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _run(self, dump: Path, task: int):
        """One task drawn: its recorded commands and the RAM after, or None when it fails to draw."""
        try:
            self.proc.stdin.write(f"{dump} {task:x}\n")
            self.proc.stdin.flush()
            answer = self.proc.stdout.readline().strip()
        except (BrokenPipeError, OSError):
            return None
        self.count += 1
        cmds, after = self.dir / f"{self.count:06d}.cmds", self.dir / f"{self.count:06d}.after"
        try:
            if answer != "ok" or not cmds.exists() or not after.exists():
                return None
            return cmds.read_bytes(), after.read_bytes()
        finally:
            for f in self.dir.glob(f"{self.count:06d}.*"):
                f.unlink(missing_ok=True)  # 8 MB each: only the pictures are kept

    def draw(self, dump: Path, task: int, kept: dict[int, bytes] | None = None):
        """The images one task draws, as the module's `draw`, from the state the tasks before it left."""
        done = self._run(dump, task)
        return None if done is None else _images(*done, kept)

    def draw_picture(self, dump: Path, task: int, kept: dict[int, bytes] | None = None):
        """The largest picture one task draws, as (width, lines, RGB bytes), or None when it fails to draw or draws none:
        the live viewer's, which converts one image instead of every one."""
        done = self._run(dump, task)
        if done is None:
            return None
        data, mem = done
        best = None
        for addr, width, siz, lines in _colour_images(data, mem, kept):
            if best is None or width * lines >= best[1] * best[3]:
                best = (addr, width, siz, lines)
        if best is None:
            return None
        addr, width, siz, lines = best
        return width, lines, rgb_bytes(mem, addr, width, siz, lines)


def _colour_images(data: bytes, mem: bytes, kept: dict[int, bytes] | None) -> list[tuple[int, int, int, int]]:
    """The colour images a task drew to as (address, width, pixel size, lines), whole inside `mem`; `kept` takes their
    bytes and the depth buffers' as drawn (for the next task to start from)."""
    drawn = []
    depth = {w[1] & 0xFFFFFF for w in commands(data) if w[0] >> 24 & 0x3F == 0x3E}  # z images: cleared as colour
    found = images(data)
    for addr, width, siz, lines in found:
        if addr in depth:
            continue
        size = width * lines * (4 if siz == 3 else 2)
        if addr + size <= len(mem):
            drawn.append((addr, width, siz, lines))
            if kept is not None:
                kept[addr] = mem[addr:addr + size]
    if kept is not None:
        # the depth buffer too: the emulator writes no depth either, and what is drawn tests against it (a menu's frame
        # was sky only on the console, the road and cars failing a depth test against noise)
        shape = next(((w, h) for _, w, _, h in found), None)
        for addr in depth - {0}:
            if shape and addr + shape[0] * shape[1] * 2 <= len(mem):
                kept[addr] = mem[addr:addr + shape[0] * shape[1] * 2]
    return drawn


def _images(data: bytes, mem: bytes, kept: dict[int, bytes] | None) -> dict[int, tuple[int, int, list]]:
    return {addr: (width, lines, rgb(mem, addr, width, siz, lines))
            for addr, width, siz, lines in _colour_images(data, mem, kept)}


def draw(program: Path, dump: Path, task: int, work: Path,
         kept: dict[int, bytes] | None = None) -> dict[int, tuple[int, int, list]] | None:
    """The images one task draws: {address: (width, lines, RGB pixels)}, or None when it fails to draw. `kept` takes each
    image's bytes as drawn ({address: bytes}), for the next task to start from (frames, `carry`)."""
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
    return _images(cmds.read_bytes(), after.read_bytes(), kept)


TASK_AT = 0x807FFFC0  # where a dump's own task is put for drawing: the last 64 bytes of the 8 MB


def with_task(dump: Path, work: Path) -> tuple[Path, int] | None:
    """The dump with the task its `.task` file holds placed at TASK_AT, and that address; None when the dump has no
    such file. Both the emulator's video plugin and the port's host write the task the RSP was given beside each
    frame dump: a game keeps its own copy wherever it likes, so scanning RAM for one (graphics_tasks) can find
    nothing, or something that only looks like a task. The emulator's holds physical pointers; the renderer reads
    pointers as the game's, so they get the 0x80000000 a game's own task has."""
    sidecar = dump.with_name(dump.name + ".task")
    if not sidecar.is_file() or sidecar.stat().st_size != 64:
        return None
    words = list(struct.unpack(">16I", sidecar.read_bytes()))
    for i in (2, 4, 6, 8, 10, 12):  # boot, microcode, its data, the DRAM stack, the output buffer, the list
        if words[i] and words[i] < 0x80000000:
            words[i] |= 0x80000000
    mem = bytearray(dump.read_bytes().ljust(RAM, b"\0"))
    mem[TASK_AT & 0x7FFFFF:(TASK_AT & 0x7FFFFF) + 64] = struct.pack(">16I", *words)
    placed = work / (dump.name + ".withtask")
    placed.write_bytes(mem)
    return placed, TASK_AT


def frames(program: Path, dump: Path, work: Path,
           carry: dict[int, bytes] | None = None, renderer: Renderer | None = None,
           base: dict[int, bytes] | None = None) -> dict[tuple[int, int], tuple[int, int, list]]:
    """Every graphics task in a dump drawn: {(task, image): (width, lines, pixels)}; tasks that fail are skipped.
    `base` ({address: bytes}) takes what each buffer held when its first task of the window began to draw into it.
    `carry` ({address: bytes}) is the pictures the tasks before this one drew: the emulator draws nothing, so its RAM holds
    no pixels of what the game drew earlier into the same buffer (a menu's text, drawn by an earlier task, was noise on
    the console and there on the port), and each is put back before this task draws over it. It is updated with this
    task's own pictures."""
    out = {}
    own = with_task(dump, work)
    if own:  # the task the console or the port was running, not a guess from RAM
        dump, tasks = own[0], [own[1]]
    else:
        tasks = graphics_tasks(dump.read_bytes())
    if carry:
        mem = bytearray(dump.read_bytes())
        for addr, raw in carry.items():
            at = addr & 0x7FFFFF
            mem[at:at + len(raw)] = raw
        dump = dump.with_name(dump.name + ".carried")
        dump.write_bytes(mem)
    blob = dump.read_bytes() if base is not None else b""
    for task in tasks:
        kept: dict[int, bytes] = {}
        drawn = renderer.draw(dump, task, kept) if renderer else draw(program, dump, task, work, kept)
        for addr, raw in kept.items() if base is not None else ():  # what a buffer held before the window's first task drew in it
            base.setdefault(addr, blob[addr & 0x7FFFFF:(addr & 0x7FFFFF) + len(raw)])
        if carry is not None:
            for addr, raw in kept.items():  # a task that draws fewer lines than an earlier one leaves the rest as it was
                carry[addr] = raw + carry.get(addr, b"")[len(raw):]
        if drawn is None:
            pass  # a list still being built: skipped
            continue
        for image, picture in drawn.items():
            out[(task, image)] = picture
    return out

def commands(data: bytes):
    """The recorded commands, each as its words."""
    words = struct.unpack(f">{len(data) // 4}I", data[: len(data) // 4 * 4])
    i = 0
    while i < len(words):
        op = words[i] >> 24 & 0x3F
        n = 2
        if 0x08 <= op <= 0x0F:
            n = 8 + (16 if op & 4 else 0) + (16 if op & 2 else 0) + (4 if op & 1 else 0)
        elif op in (0x24, 0x25):
            n = 4
        yield words[i:i + n]
        i += n


def images(data: bytes) -> list[tuple[int, int, int, int]]:
    """The colour images drawn to: address, width, pixel size (2: 16-bit, 3: 32-bit), lines (the scissor's)."""
    found: dict[int, list[int]] = {}
    current = None
    lines = 240
    for w in commands(data):
        op = w[0] >> 24 & 0x3F
        if op == 0x3F:
            current = (w[1] & 0xFFFFFF, (w[0] & 0xFFF) + 1, w[0] >> 19 & 3)
        elif op == 0x2D:
            lines = (w[1] & 0xFFF) >> 2
        elif current and (0x08 <= op <= 0x0F or op in (0x24, 0x25, 0x36)):
            seen = found.setdefault(current[0], [current[1], current[2], 0])
            seen[2] = max(seen[2], lines)
    return [(a, w, siz, h) for a, (w, siz, h) in found.items() if siz in (2, 3) and h > 0]


def rgb(mem: bytes, addr: int, width: int, siz: int, lines: int) -> list[tuple[int, int, int]]:
    out = []
    for i in range(width * lines):
        if siz == 3:
            a = addr + i * 4
            out.append((mem[a], mem[a + 1], mem[a + 2]))
        else:
            v = mem[addr + i * 2] << 8 | mem[addr + i * 2 + 1]
            out.append(((v >> 11 & 31) << 3, (v >> 6 & 31) << 3, (v >> 1 & 31) << 3))
    return out


def _plane(table) -> bytes:
    return bytes(table(i) for i in range(256))


_R = _plane(lambda h: h >> 3 << 3)                    # a 5551 pixel's high byte: red
_G_HIGH, _G_LOW = _plane(lambda h: (h & 7) << 5), _plane(lambda lo: lo >> 6 << 3)  # green is split over both bytes
_B = _plane(lambda lo: (lo >> 1 & 31) << 3)           # the low byte: blue


def rgb_bytes(mem: bytes, addr: int, width: int, siz: int, lines: int) -> bytes:
    """`rgb`'s picture as flat R,G,B bytes, made a plane at a time (a frame of it was most of the live viewer's time)."""
    n = width * lines
    out = bytearray(3 * n)
    if siz == 3:
        raw = mem[addr:addr + 4 * n]
        out[0::3], out[1::3], out[2::3] = raw[0::4], raw[1::4], raw[2::4]
    else:
        raw = mem[addr:addr + 2 * n]
        high, low = raw[0::2], raw[1::2]
        green = int.from_bytes(high.translate(_G_HIGH), "big") | int.from_bytes(low.translate(_G_LOW), "big")
        out[0::3], out[1::3], out[2::3] = high.translate(_R), green.to_bytes(n, "big"), low.translate(_B)
    return bytes(out)


def png(path: Path, pixels: list[tuple[int, int, int]], width: int, height: int) -> None:
    raw = b"".join(b"\0" + bytes(c for p in pixels[y * width:(y + 1) * width] for c in p) for y in range(height))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def compare(ours: list, theirs: list) -> dict:
    """Pixels that differ, those differing by more than one 5-bit step, the largest difference, the mean."""
    differ = more = 0
    largest = total = 0
    for a, b in zip(ours, theirs):
        d = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        if d:
            differ += 1
            total += d
            largest = max(largest, d)
            if d > 8:
                more += 1
    n = len(ours) or 1
    return {"pixels": len(ours), "differ": differ, "differ_pct": 100 * differ / n, "beyond_one_step": more,
            "beyond_pct": 100 * more / n, "largest": largest, "mean": total / n}


def retraces_per_second(rom: Path) -> int:
    """50 for a PAL game, 60 otherwise, by the header's region (in any of the three byte orders)."""
    head = rom.read_bytes()[:0x40]
    if head[:4] == b"\x37\x80\x40\x12":
        head = bytes(b for i in range(0, 0x40, 2) for b in (head[i + 1], head[i]))
    elif head[:4] == b"\x40\x12\x37\x80":
        head = b"".join(head[i:i + 4][::-1] for i in range(0, 0x40, 4))
    return 50 if chr(head[0x3E]) in "DFHIPSUWXY" else 60


def prime_save(game: Path, rom: Path, inputs: str, folder: Path, clock: str = "vi", seconds: int = 100, *,
               extra_env: dict[str, str] | None = None) -> bool:
    """Run the port once through the same input script in `folder` (where it keeps game.eep) before the run that is compared,
    unless a save is there already. Observed on a racing game: a port started from no save, from an all-zero one or from the
    save of a short boot had its car launch slower than the emulator's (687 units to the port's 116 in the first 200 tasks
    of the race, 2,000 and more units behind from then on), while one started from the save a full run of the script had
    written was at the console's position to the unit at every task sampled, and the race matched the emulator to the pixel
    (seven of eight windows 0.00 percent differing). Why the emulator's blank save behaves like that one is not known. Returns
    whether it ran."""
    if (folder / "game.eep").exists():
        return False
    env = {k: v for k, v in os.environ.items() if k not in ("PORT_INPUT", "PORT_RAM_DUMPS", "PORT_FRAME_DUMPS", "PORT_VI_DUMPS")}
    env["PORT_INPUT_CLOCK"] = clock
    if inputs:
        env["PORT_INPUT"] = inputs
    env.update(extra_env or {})
    subprocess.run([str(game.resolve()), str(rom.resolve()), str(seconds)], cwd=folder, env=env, capture_output=True,
                   timeout=seconds * 6 + 120)
    return (folder / "game.eep").exists()


HEADER = 16  # bytes at the start of a save a game fills in with what it takes from the clock (a racing game's first 8 differed)


def console_save(dump: Path, port_save: bytes) -> bytes | None:
    """The save the emulator's game holds, taken from a console RAM dump taken after the game has read its EEPROM: the game keeps
    a copy of the whole chip in RAM, so the port's save (from a run of its own) is looked for there by its body, past the first
    HEADER bytes (a time-seeded stamp and a counter differ), and the console's bytes at that place are the console's save.
    None when the body isn't found once. A port started from these bytes is at the console's position to the unit through a
    racing game's whole race (a fresh port, whose boot writes a different header and counter, launched slower): see prime_save."""
    if len(port_save) < HEADER + 32:
        return None
    body = port_save[HEADER:HEADER + 32]
    mem = dump.read_bytes()
    at = mem.find(body)
    if at < HEADER or mem.find(body, at + 1) != -1 or at - HEADER + len(port_save) > len(mem):
        return None
    return mem[at - HEADER:at - HEADER + len(port_save)]


def run_port(game: Path, rom: Path, points: list[int], inputs: str, folder: Path, clock: str = "vi", *,
             stop=lambda: False, extra_env: dict[str, str] | None = None) -> dict[int, Path]:
    """The port run (in real time) until its RAM is written at each point: retraces (PORT_RAM_DUMPS) or, with `clock`
    "frames", the game's graphics tasks (PORT_FRAME_DUMPS), its input timed the same way. Stopped once the last is
    written."""
    dumps = {n: folder / f"port_{clock}_{n}.bin" for n in points}
    for path in dumps.values():
        path.unlink(missing_ok=True)
    var = "PORT_FRAME_DUMPS" if clock == "frames" else "PORT_RAM_DUMPS"
    env = {**os.environ, var: ",".join(f"{n}:{p}" for n, p in dumps.items()), "PORT_INPUT_CLOCK": clock}
    for key in ("PORT_INPUT", "PORT_RAM_DUMPS", "PORT_FRAME_DUMPS"):
        if key != var:
            env.pop(key, None)
    if inputs:
        env["PORT_INPUT"] = inputs
    env.update(extra_env or {})
    # a frame takes at most 6 retraces (10 frames a second) in any game worth comparing
    seconds = max(points) * (6 if clock == "frames" else 1) / retraces_per_second(rom) + 10
    proc = subprocess.Popen([str(game.resolve()), str(rom.resolve()), f"{seconds:.0f}"], cwd=folder, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    try:
        while proc.poll() is None and time.monotonic() - started < seconds + 60 and not stop():
            if all(p.exists() and p.stat().st_size >= RAM for p in dumps.values()):
                break
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return {n: p for n, p in dumps.items() if p.exists() and p.stat().st_size >= RAM}


def run_console(rom: Path, points: list[int], inputs: str, folder: Path, clock: str = "vi", *, stop=lambda: False,
                extra_env: dict[str, str] | None = None, seeds_at: int | None = None) -> dict[int, Path]:
    """The ROM in the reversing emulator, its RAM written at each point (retraces, or the game's frames). With `seeds_at` (the
    address of the game's seeding function) the first argument of each call is written to `folder/seeds.txt`, one a line in call
    order, for the port's PORT_SEED_REPLAY (tools/n64_record_seeds.py)."""
    from decompnet.consoles.n64 import emulator

    dumps = {n: folder / f"console_{clock}_{n}.bin" for n in points}
    for path in dumps.values():
        path.unlink(missing_ok=True)
    if clock == "frames":
        seeding = {"breaks": [seeds_at], "max_hits": 1_000_000} if seeds_at else {}
        run = emulator.run(rom, max(points) * 6 + 1200, inputs=inputs, frame_dumps=dumps, input_clock="frames", stop=stop,
                           extra_env=extra_env, **seeding)
        if seeds_at:
            (folder / "seeds.txt").write_text("".join(f"{int(h.args[0]) & 0xFFFFFFFF}\n" for h in run.hits))
            (folder / "seed_vis.txt").write_text("".join(f"{h.vi}\n" for h in run.hits))
    else:
        emulator.run(rom, max(points) + 1, inputs=inputs, dumps=dumps, stop=stop, extra_env=extra_env)
    return {n: p for n, p in dumps.items() if p.exists()}


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


def unmatched(a: list, b: list, width: int, lines: int, radius: int) -> float:
    """The percentage of a's pixels with no equal pixel of b within `radius` pixels: what's left of a difference once a
    small shift is allowed. A scene drawn the same but a pixel or two apart (a camera, a scroll, an animation caught a
    frame earlier, as moving scenes are when the console and the port run apart) scores near 0, where a difference in what
    is drawn stays high."""
    bad = 0
    for y in range(lines):
        for x in range(width):
            p = a[y * width + x]
            found = False
            for yy in range(max(0, y - radius), min(lines, y + radius + 1)):
                row = yy * width
                if p in b[row + max(0, x - radius):row + min(width, x + radius + 1)]:
                    found = True
                    break
            bad += not found
    return 100.0 * bad / max(width * lines, 1)


def difference(a: list, b: list) -> list:
    return [tuple(min(255, abs(x - y) * 4) for x, y in zip(p, q)) for p, q in zip(a, b)]



PROBE_SECONDS = 24  # virtual seconds the probe run lasts: a run of 10 reached only task 408 on one route, before the first seeding call


def seed_offset(port_calls: dict[int, int], task_vi: dict[int, int], seed_vis: list[int], first: int = 200, last: int = 700) -> int:
    """How many more seeding calls the port has made than the console's game at the same graphics task, early in the run: the usual
    difference over the tasks from `first` to `last` (a call made a hair after a task starts is a gap of zero at an odd task, and the logic lead's
    drift grows later; an extra call the port makes at boot is the same gap at nearly every task). port_calls: {task: calls
    so far} (PORT_SEED_LOG), task_vi: {task: the console's retrace at its start} (the trace), seed_vis: the retrace of each of the console's calls.
    Replaying the console's seeds in order from the port's call k hands it the console's call k: one call too many in the port hands every later draw the
    previous seed (a race start's opponents were other cars than the console's until a seed was added at the front: 0.00% at every task)."""
    import bisect
    gaps = []
    for n in sorted(port_calls):
        if first <= n <= last and n in task_vi:
            mine, theirs = port_calls[n], bisect.bisect_right(seed_vis, task_vi[n])
            if mine or theirs:  # before either has called there is nothing to be ahead by
                gaps.append(mine - theirs)
    import statistics
    return int(statistics.median_low(gaps)) if gaps else 0  # a call made a hair after a task starts shows as a gap of zero at the odd task: the usual one counts


def _shifted(seeds: list[str], shift: int) -> list[str]:
    return ([seeds[0]] * shift + seeds) if shift > 0 and seeds else seeds[-shift:] if shift < 0 else seeds


def shift_inputs(text: str, shifts: dict[str, int], after: int = 0) -> str:
    """An input script ("task:BUTTON,..." ) with each press of a button moved by `shifts[button]` tasks from task `after` on, and
    the release that follows it moved with it. The console and the port take a controller edge a different number of tasks after
    it is given (found on a racing game: START 5 tasks on the console and 6 on the port, A 10 on the console and 6 on the port), a
    difference of the game's own state that no single delay of the script absorbs; the picture window after each edge shows which
    button needs which shift (docs/PORTING.md)."""
    out, moved = [], 0
    for item in text.split(","):
        frame, button = item.split(":", 1)
        frame = int(frame)
        if frame >= after:
            if button != "-":
                moved = shifts.get(button.split("+")[0], 0)
            frame += moved
        out.append(f"{max(frame, 0)}:{button}")
    return ",".join(out)


def align_seeds(game: Path, rom: Path, inputs: str, work: Path, pace_log: Path, env: dict[str, str], stop=lambda: False) -> int:
    """Shift work/seeds.txt so the port's k-th seeding call gets the console's k-th seed. Short runs of the port in a scratch folder (its
    save is a copy) count its seeding calls at each graphics task for each candidate shift until one keeps going to task 600 (its call counts do not depend on
    the file's contents, only on the game running on), and the gap between those counts and the console's is the shift made: a seed file out of line leaves a game at a few hundred of a thousand graphics tasks, which a single
    probe cannot measure (it reaches no first call; found on a route where a probe of the raw file logged 196 tasks and no gap). Returns the shift made
    (+n: n copies of the first seed put in front)."""
    import re
    import tempfile
    seeds_file, vis_file = work / "seeds.txt", work / "seed_vis.txt"
    if not seeds_file.exists() or not vis_file.exists() or not pace_log.exists():
        return 0
    task_vi: dict[int, int] = {}
    for line in pace_log.read_text().split("\n"):
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            task_vi[int(parts[0])] = int(parts[1])
    vis = [int(x) for x in vis_file.read_text().split()]
    original = seeds_file.read_text().split()
    probe_file = work / "seeds_probe.txt"
    probe_env = {**os.environ, **env, "PORT_SEED_LOG": "1", "PORT_SEED_REPLAY": str(probe_file), "PORT_INPUT_CLOCK": "frames"}
    if inputs:
        probe_env["PORT_INPUT"] = inputs
    for key in ("PORT_RAM_DUMPS", "PORT_FRAME_DUMPS"):
        probe_env.pop(key, None)
    best = (0, 0)  # (tasks reached, shift) when nothing keeps step
    for shift in (1, 0, 2, -1, 3):
        if stop():
            break
        probe_file.write_text("".join(f"{x}\n" for x in _shifted(original, shift)))
        with tempfile.TemporaryDirectory(prefix="seed-probe-") as scratch:
            if (work / "game.eep").exists():
                shutil.copy(work / "game.eep", Path(scratch) / "game.eep")
            try:
                done = subprocess.run([str(game.resolve()), str(rom.resolve()), str(PROBE_SECONDS)], cwd=scratch, env=probe_env,
                                      capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                continue
        calls = {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"SEEDS task (\d+) calls (\d+)", done.stderr)}
        reached = max(calls) if calls else 0
        if reached > best[0]:
            best = (reached, shift)
        if reached >= 600:  # a run that kept going counts its calls: its gap to the console's is the shift the file needs
            best = (reached, seed_offset(calls, task_vi, vis, last=reached))
            break
    probe_file.unlink(missing_ok=True)
    shift = best[1]
    if shift:
        seeds_file.write_text("".join(f"{x}\n" for x in _shifted(original, shift)))
    return shift


def compare_run(game: Path, rom: Path, points: list[int], inputs: str, work: Path, out: Path, *, tolerance: int = 3,
                pace: bool = True, seeds_at: int | None = None, port_shifts: dict[str, int] | None = None, after: int = 0, stop=lambda: False,
                progress=lambda text, done, total: None) -> list[dict]:
    """The port's pictures against the console's at the game's graphics tasks `points` (counted from 1), both run with
    `inputs` (timed in graphics tasks) and drawn by the port's renderer. One entry for each task and picture:
    {"task", "image", "width", "lines", "differ_pct", "beyond_pct", "near_pct", "file"} (the side-by-side PNG in `out`:
    console, port, their difference x4), or {"task", "missing": "console"|"port"|"size"} when a side drew nothing or another size.
    What makes the two runs one (found on a racing game, docs/PORTING.md): the port starts from the console's own save
    (console_save), `port_shifts` (button: tasks, from task `after`) moves the port's presses of a button, and with `pace` its clock replays the retrace the console was at for each task (PORT_VTIME_TRACE)."""
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    early = min(600, max(points))
    pace_log = work / "tasks.log"
    pace_log.unlink(missing_ok=True)
    progress("Building the renderer", 0, 4)
    program = build(work)
    progress("Running the original game in the emulator", 1, 4)
    console = run_console(rom, sorted(set(points) | {early}), inputs, work, "frames", stop=stop,
                          extra_env={"EMU_TASK_LOG": str(pace_log)} if pace else None, seeds_at=seeds_at)
    if stop():
        return []
    progress("Giving the port the console's save", 2, 4)
    prime_save(game, rom, inputs, work, "frames", extra_env={"PORT_VTIME": "0"})
    save = work / "game.eep"
    if early in console and save.exists():
        taken = console_save(console[early], save.read_bytes())
        if taken:
            save.write_bytes(taken)
    progress("Running the port", 3, 4)
    pacing = {"PORT_VTIME": "1", "PORT_VTIME_TRACE": str(pace_log)} if pace and pace_log.exists() else {}
    if seeds_at and (work / "seeds.txt").exists():  # a game that seeds from the clock draws the console's numbers
        align_seeds(game, rom, inputs, work, pace_log, pacing, stop)
        pacing["PORT_SEED_REPLAY"] = str(work / "seeds.txt")
    wanted = sorted(set(points))
    port: dict[int, Path] = {}
    for attempt in range(3):
        # The port's virtual time waits for its threads to idle in wall-clock time, so a loaded computer (a load average of 12 to 40 while other
        # jobs ran) leaves it at a few hundred of a thousand graphics tasks, differently each time: the same run on a quiet one reached all of them.
        port = run_port(game, rom, wanted, shift_inputs(inputs, port_shifts, after) if port_shifts else inputs, work, "frames", stop=stop, extra_env=pacing)
        if len(port) * 2 >= len(wanted) or stop():
            break
        progress(f"The port stalled ({len(port)} of {len(wanted)} frames reached): running it again", 3, 4)
    results: list[dict] = []
    runs: list[list[int]] = []  # the consecutive tasks taken at each moment
    for n in points:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])

    def play(run: list[int], seed: dict[int, bytes]):
        """The run's tasks drawn on each side, each side from a renderer of its own (the RDP starts again, with nothing of
        the tasks before): [(task, console's pictures, port's pictures)] with the pictures paired, what each side's buffers
        held when the window began to draw into them, and what they hold at its end. `seed` is put into the console's
        buffers first."""
        carried: tuple[dict, dict] = (dict(seed), {})
        started: tuple[dict, dict] = ({}, {})
        drawn = []
        pair = (Renderer(program, work, "console"), Renderer(program, work, "port"))
        try:
            for n in run:
                if stop():
                    break
                theirs = frames(program, console[n], work, carried[0], pair[0], started[0])
                ours = frames(program, port[n], work, carried[1], pair[1], started[1])
                only_a, only_b = [k for k in theirs if k not in ours], [k for k in ours if k not in theirs]
                if only_a and len(only_a) == len(only_b):
                    # the console and the port lay their buffers out differently (the same two pictures at 0x315280 and
                    # 0x27F1C0 on one, 0x27F240 and 0x3152C0 on the other): a task's pictures are paired in drawing order
                    moved = {k: ours.pop(k) for k in only_b}
                    ours.update({a: moved[b] for a, b in zip(only_a, only_b)})
                drawn.append((n, theirs, ours))
        finally:
            for r in pair:
                r.close()
        return drawn, started, carried

    for run in runs:
        if stop():
            break
        progress(f"Comparing frames {run[0]} to {run[-1]}", len(results), len(points))
        gone = [n for n in run if n not in console or n not in port]
        if gone:
            results += [{"task": n, "missing": "console" if n not in console else "port"} for n in gone]
            continue
        drawn, started, last = play(run, {})
        # The emulator draws nothing, so before the window its buffers hold noise where the game had drawn (a menu's
        # background) and the port's hold the real pictures. A second pass starts the console's buffers from the port's
        # contents at the window's start (its first dump, as far as any task of the window draws into each), so each side
        # draws the window over the same background and a difference is the window's own drawing. The buffers are paired
        # by the order the first pass drew them. (A menu's second task differed 69% until its lower half, which the first
        # task never draws, was seeded too and kept when the first task's shorter picture was carried on.)
        seed: dict[int, bytes] = {}
        first = port[run[0]].read_bytes()
        for a, b in zip(started[0], started[1]):
            size = max(len(started[0][a]), len(started[1][b]), len(last[0].get(a, b"")), len(last[1].get(b, b"")))
            seed[a] = first[b & 0x7FFFFF:(b & 0x7FFFFF) + size]
        seeded = bool(seed) and len(started[0]) == len(started[1])
        if seeded:
            drawn, started, last = play(run, seed)
        warm = set(run[:len(run) // 2])  # drawn for what they leave in the buffers, not compared
        for n, theirs, ours in drawn:
            if n in warm:
                continue
            if not theirs and not ours:  # a task that draws no picture (the game's other tasks): said, so it isn't read as a match
                results.append({"task": n, "missing": "neither"})
                continue
            for key in sorted(set(theirs) | set(ours)):
                a, b = theirs.get(key), ours.get(key)
                name = out / f"frame{n:06d}_image{key[1]:06X}.png"
                if not (a and b):
                    side_by_side(name, [a, b])
                    results.append({"task": n, "image": key[1], "missing": "port" if a else "console"})
                elif a[:2] != b[:2]:
                    results.append({"task": n, "image": key[1], "missing": "size"})
                else:
                    c = compare(b[2], a[2])
                    side_by_side(name, [a, b, (a[0], a[1], difference(a[2], b[2]))])
                    results.append({"task": n, "image": key[1], "width": a[0], "lines": a[1], "differ_pct": c["differ_pct"],
                                    "beyond_pct": c["beyond_pct"], "file": name.name, "seeded": seeded,
                                    "near_pct": unmatched(a[2], b[2], a[0], a[1], tolerance) if tolerance else None})
    return results
