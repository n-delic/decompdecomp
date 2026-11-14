"""A display list built in Python and drawn by the N64 WebAssembly port's software renderer
(consoles/n64/port_wasm/render.c), for the renderer's tests: `Memory` lays out the console's RAM (big-endian, at
its own addresses), `Gfx` writes commands into it, `run` compiles a small harness around render.c once and hands it
the memory, which comes back as the renderer left it (the frame and depth images are read from it, as a game would).
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

RENDER = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/render.c"
RAM = 0x400000
TASK, UCODE, DL, DATA, FB, ZB = 0x100, 0x200, 0x1000, 0x8000, 0x40000, 0x80000
W, H = 320, 240

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void port_render_task(uint32_t task);
void port_frame_shown(uint32_t fb, unsigned long n);
static uint8_t ram[0x400000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
static uint8_t shown[640 * 480 * 3]; static int shown_w, shown_h;
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { memcpy(shown, rgb, (size_t)w * h * 3); shown_w = w; shown_h = h; }
/* argv: memory in, memory out, the frame as the VI shows it out, then tasks (hex RAM addresses); "show:ADDR" shows
   a frame instead */
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(ram, 1, sizeof ram, f) != sizeof ram) return 3;
    fclose(f);
    for (int i = 4; i < argc; i++) {
        if (!strncmp(argv[i], "show:", 5)) port_frame_shown((uint32_t)strtoul(argv[i] + 5, NULL, 16), 1);
        else port_render_task((uint32_t)strtoul(argv[i], NULL, 16));
    }
    f = fopen(argv[2], "wb"); fwrite(ram, 1, sizeof ram, f); fclose(f);
    f = fopen(argv[3], "wb"); fwrite(&shown_w, 4, 1, f); fwrite(&shown_h, 4, 1, f);
    fwrite(shown, 1, (size_t)shown_w * shown_h * 3, f); fclose(f);
    return 0;
}
"""


def build(folder: Path) -> Path | None:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        return None
    (folder / "harness.c").write_text(HARNESS)
    program = folder / "render"
    subprocess.run([cc, "-O1", "-o", str(program), str(folder / "harness.c"), str(RENDER), "-lm"],
                   check=True, capture_output=True, timeout=180)
    return program


class Memory:
    def __init__(self) -> None:
        self.ram = bytearray(RAM)
        self.free = DATA

    def w8(self, a: int, v: int) -> None: self.ram[a] = v & 0xFF
    def w16(self, a: int, v: int) -> None: struct.pack_into(">H", self.ram, a, v & 0xFFFF)
    def w32(self, a: int, v: int) -> None: struct.pack_into(">I", self.ram, a, v & 0xFFFFFFFF)
    def r16(self, a: int) -> int: return struct.unpack_from(">H", self.ram, a)[0]

    def put(self, data: bytes, align: int = 8) -> int:
        """Data somewhere free, its physical address returned."""
        self.free = (self.free + align - 1) // align * align
        a = self.free
        self.ram[a:a + len(data)] = data
        self.free += len(data)
        return a

    def pixel(self, x: int, y: int, base: int = FB, width: int = W) -> tuple[int, int, int]:
        """A 16-bit frame buffer's pixel as 5-bit red, green, blue."""
        v = self.r16(base + (y * width + x) * 2)
        return v >> 11 & 31, v >> 6 & 31, v >> 1 & 31


def matrix(m: list[float] | None = None) -> bytes:
    """An N64 Mtx (s15.16: the 16 integer halves, then the 16 fractions), identity by default."""
    m = m or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    fixed = [int(round(v * 65536)) & 0xFFFFFFFF for v in m]
    return struct.pack(">16H", *(f >> 16 for f in fixed)) + struct.pack(">16H", *(f & 0xFFFF for f in fixed))


def vertex(x: int, y: int, z: int = 0, s: float = 0, t: float = 0, rgba: int = 0xFFFFFFFF) -> bytes:
    """A Vtx: position, flag, s and t (s10.5), colour or normal."""
    return struct.pack(">hhhHhhI", x, y, z, 0, int(s * 32), int(t * 32), rgba)


def normal(nx: int, ny: int, nz: int, a: int = 255) -> int:
    return (nx & 0xFF) << 24 | (ny & 0xFF) << 16 | (nz & 0xFF) << 8 | a


def light(r: int, g: int, b: int, dx: int = 0, dy: int = 0, dz: int = 0) -> bytes:
    """A Light: its colour twice and its direction (a look-at entry is the same shape)."""
    return bytes([r, g, b, 0, r, g, b, 0, dx & 0xFF, dy & 0xFF, dz & 0xFF, 0, 0, 0, 0, 0])


def combine(a: int, b: int, c: int, d: int, aa: int, ab: int, ac: int, ad: int) -> tuple[int, int]:
    """G_SETCOMBINE's two words, the same (A - B) * C + D in both cycles."""
    w0 = a << 20 | c << 15 | aa << 12 | ac << 9 | a << 5 | c
    w1 = b << 28 | b << 24 | aa << 21 | ac << 18 | d << 15 | ab << 12 | ad << 9 | d << 6 | ab << 3 | ad
    return 0xFC000000 | w0, w1


SHADE = combine(15, 15, 31, 4, 7, 7, 7, 4)       # the shade's colour and alpha
TEXEL0 = combine(1, 15, 31, 7, 1, 7, 7, 7)       # (TEXEL0 - 0) * ... : TEXEL0 as D would be 1: here A * C with C = 1
TEX = combine(15, 15, 31, 1, 7, 7, 7, 1)         # D = TEXEL0, alpha D = TEXEL0


class Gfx:
    """Display list commands written into memory at `at`. `ucode` names the microcode as its data does."""

    def __init__(self, mem: Memory, ucode: str = "F3DEX2", at: int = DL, task: int = TASK) -> None:
        self.mem, self.at, self.start, self.task = mem, at, at, task
        data = mem.put(("RSP Gfx ucode " + ucode + " 2.05 Yoshitaka Yasumoto 1998 Nintendo.\0").encode(), 16)
        mem.w32(task + 24, 0x80000000 | data)
        mem.w32(task + 48, 0x80000000 | at)

    def __call__(self, w0: int, w1: int = 0) -> Gfx:
        self.mem.w32(self.at, w0)
        self.mem.w32(self.at + 4, w1)
        self.at += 8
        return self

    def frame(self, fb: int = FB, width: int = W) -> Gfx:
        return self(0xFF100000 | (width - 1), 0x80000000 | fb)

    def zimage(self, zb: int = ZB) -> Gfx:
        return self(0xFE000000, 0x80000000 | zb)

    def othermode(self, h: int, l: int) -> Gfx:  # noqa: E741
        return self(0xEF000000 | h, l)

    def fill(self, colour16: int, x0: int = 0, y0: int = 0, x1: int = W - 1, y1: int = H - 1) -> Gfx:
        self.othermode(3 << 20, 0)
        self(0xF7000000, colour16 << 16 | colour16)
        return self(0xF6000000 | (x1 * 4) << 12 | y1 * 4, (x0 * 4) << 12 | y0 * 4)

    def end(self) -> int:
        """G_ENDDL (F3DEX2's or GBI 1's, by the microcode the list was made for); the task's RAM address."""
        return self.task | 0x80000000


def run(program: Path, mem: Memory, folder: Path, *steps: str | int) -> tuple[Memory, tuple[int, int, bytes]]:
    """The tasks (RAM addresses) and frame showings ("show:ADDR") in order; memory as the renderer left it and the
    last frame the VI showed (width, height, RGB)."""
    src, dst, shown = folder / "in.bin", folder / "out.bin", folder / "shown.bin"
    src.write_bytes(mem.ram)
    subprocess.run([str(program), str(src), str(dst), str(shown), *(s if isinstance(s, str) else f"{s:x}" for s in steps)],
                   check=True, capture_output=True, timeout=120)
    out = Memory()
    out.ram = bytearray(dst.read_bytes())
    raw = shown.read_bytes()
    w, h = struct.unpack_from("<ii", raw)
    return out, (w, h, raw[8:])
