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
void port_vi_mode(uint32_t mode);
void port_vi_features(uint32_t f);
void port_vi_black(int on);
static uint8_t ram[0x400000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
static uint8_t shown[640 * 480 * 3]; static int shown_w, shown_h;
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { memcpy(shown, rgb, (size_t)w * h * 3); shown_w = w; shown_h = h; }
/* argv: memory in, memory out, the frame as the VI shows it out, then steps: tasks (hex RAM addresses), "show:ADDR"
   (the VI shows a frame), "mode:ADDR" (osViSetMode), "features:HEX" (osViSetSpecialFeatures), "black:0|1" (osViBlack) */
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(ram, 1, sizeof ram, f) != sizeof ram) return 3;
    fclose(f);
    for (int i = 4; i < argc; i++) {
        if (!strncmp(argv[i], "show:", 5)) port_frame_shown((uint32_t)strtoul(argv[i] + 5, NULL, 16), 1);
        else if (!strncmp(argv[i], "mode:", 5)) port_vi_mode((uint32_t)strtoul(argv[i] + 5, NULL, 16));
        else if (!strncmp(argv[i], "black:", 6)) port_vi_black(atoi(argv[i] + 6));
        else if (!strncmp(argv[i], "features:", 9)) port_vi_features((uint32_t)strtoul(argv[i] + 9, NULL, 16));
        else port_render_task((uint32_t)strtoul(argv[i], NULL, 16));
    }
    f = fopen(argv[2], "wb"); fwrite(ram, 1, sizeof ram, f); fclose(f);
    f = fopen(argv[3], "wb"); fwrite(&shown_w, 4, 1, f); fwrite(&shown_h, 4, 1, f);
    fwrite(shown, 1, (size_t)shown_w * shown_h * 3, f); fclose(f);
    return 0;
}
"""


def build(folder: Path, *defines: str) -> Path | None:
    """The harness around render.c, compiled into `folder`; `defines` (-D...) for the renderer's test switches."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        return None
    (folder / "harness.c").write_text(HARNESS)
    program = folder / "render"
    subprocess.run([cc, "-O1", *defines, "-o", str(program), str(folder / "harness.c"), str(RENDER), "-lm"],
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
TEX = combine(15, 15, 31, 1, 7, 7, 7, 1)         # the texel's colour and alpha (D = TEXEL0)


# Each family's signature, as its data carries it: GBI 2 versions are 2.x, GBI 1's 1.x; Fast3D has its own.
SIGNATURES = {
    "F3DEX2": "RSP Gfx ucode F3DEX       fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.",
    "F3DZEX": "RSP Gfx ucode F3DZEX.NoN  fifo 2.06H Yoshitaka Yasumoto 1998 Nintendo.",
    "S2DEX2": "RSP Gfx ucode S2DEX       fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.",
    "F3DEX": "RSP Gfx ucode F3DEX       1.23 Yoshitaka Yasumoto 1997 Nintendo.",
    "F3DLX": "RSP Gfx ucode F3DLX.Rej   1.21 Yoshitaka Yasumoto 1996 Nintendo.",
    "S2DEX": "RSP Gfx ucode S2DEX       1.07 Yoshitaka Yasumoto 1998 Nintendo.",
    "L3DEX2": "RSP Gfx ucode L3DEX       fifo 2.04  Yoshitaka Yasumoto 1998 Nintendo.",
    "L3DEX": "RSP Gfx ucode L3DEX       1.23 Yoshitaka Yasumoto 1997 Nintendo.",
    "Fast3D": "RSP SW Version: 2.0D, 04-01-96",
}


def ucode_data(mem: Memory, ucode: str) -> int:
    """A microcode's data, its signature in it: the physical address (G_LOAD_UCODE's G_RDPHALF_1)."""
    return mem.put(b"\0" * 64 + (SIGNATURES[ucode] + "\0").encode(), 16)


class Gfx:
    """Display list commands written into memory at `at`. `ucode` names the microcode the task starts with."""

    def __init__(self, mem: Memory, ucode: str = "F3DEX2", at: int = DL, task: int = TASK) -> None:
        self.mem, self.at, self.start, self.task = mem, at, at, task
        mem.w32(task + 24, 0x80000000 | ucode_data(mem, ucode))
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
        """The task's RAM address (the list ends with its own G_ENDDL, written by the test)."""
        return self.task | 0x80000000


def run(program: Path, mem: Memory, folder: Path, *steps: str | int) -> tuple[Memory, tuple[int, int, bytes]]:
    """The steps in order: tasks (RAM addresses), frame showings ("show:ADDR"), VI modes ("mode:ADDR") and
    features ("features:HEX"); memory as the renderer left it and the
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


QUADRANT_COLOURS = [0xF801, 0x07C1, 0x003F, 0xFFFF]  # red, green, blue, white (RGBA 5551)


def quadrant_texture(mem: Memory) -> int:
    """An 8x8 RGBA16 texture whose quadrants are red, green (top), blue, white (bottom)."""
    texels = b"".join(QUADRANT_COLOURS[(s >= 4) + 2 * (t >= 4)].to_bytes(2, "big") for t in range(8) for s in range(8))
    return mem.put(texels)


def load_texture(g: Gfx, image: int, tile: int = 0, tmem: int = 0, size: int = 8, cm: int = 0) -> Gfx:
    """An RGBA16 size x size image into TMEM by LoadTile, set up as `tile` (clamp/mirror bits `cm` both ways)."""
    line = size * 2 // 8
    g(0xFD100000 | (size - 1), 0x80000000 | image)
    g(0xF5100000 | line << 9 | tmem, 7 << 24)
    g(0xF4000000, 7 << 24 | ((size - 1) * 4) << 12 | (size - 1) * 4)
    g(0xF5100000 | line << 9 | tmem, tile << 24 | cm << 18 | cm << 8)
    return g(0xF2000000, tile << 24 | ((size - 1) * 4) << 12 | (size - 1) * 4)


def vi_mode(mem: Memory, width: int, lines: int, ctrl: int = 0x2) -> int:
    """An OSViMode: `ctrl` (2: 16-bit pixels; 0x8 gamma), the frame's width, X scale, and a Y scale making `lines`
    lines of NTSC's 237 active ones (as 240)."""
    mode = bytearray(80)
    struct.pack_into(">IIIIIIIII", mode, 4, ctrl, width, 0, 0, 0, 0, 0, width * 1024 // 640, 0)
    struct.pack_into(">IIIII", mode, 40, FB, lines * 1024 // 240, 0x002501FF, 0, 0)
    return mem.put(bytes(mode))
