"""The N64 WebAssembly port's software renderer (consoles/n64/port_wasm/render.c), compiled with a small harness
that hands it one display list: a full-screen quad textured with a 4-bit colour-indexed image whose four quadrants
have four colours. A racing game's cars are 4-bit skins drawn with perspective correction, and once showed only the
top-left quarter of their skin because every 4-bit texture had its coordinates halved; only coordinates drawn
without perspective correction are taken at half scale (its menu font)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RENDER = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/render.c"

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
void port_render_task(uint32_t task);
static uint8_t ram[0x100000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
int port_live_active(void) { return 0; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }

static void w32(uint32_t a, uint32_t v) { ram[a] = v >> 24; ram[a + 1] = v >> 16; ram[a + 2] = v >> 8; ram[a + 3] = v; }
static void w16(uint32_t a, uint16_t v) { ram[a] = v >> 8; ram[a + 1] = v; }
static uint32_t dl = 0x1000;
static void gfx(uint32_t w0, uint32_t w1) { w32(dl, w0); w32(dl + 4, w1); dl += 8; }

enum { TASK = 0x100, UCODE = 0x200, TLUT = 0x400, IMAGE = 0x500, MTX = 0x600, VTX = 0x700, FB = 0x10000 };

int main(int argc, char **argv) {
    int persp = argv[1][0] == '1', scale = persp ? 1 : 2; /* without perspective, coordinates twice as large */
    memcpy(ram + UCODE, "RSP Gfx ucode F3DEX2", 20);
    w32(TASK + 24, 0x80000000u | UCODE);
    w32(TASK + 48, 0x80000000u | dl);
    uint16_t colours[5] = {0, 0xF801 /* red */, 0x07C1 /* green */, 0x003F /* blue */, 0xFFFF /* white */};
    for (int i = 0; i < 16; i++) w16(TLUT + i * 2, i < 5 ? colours[i] : 0);
    for (int t = 0; t < 8; t++) /* 8x8 texels, 4 bits each: the quadrants are indices 1 to 4 */
        for (int s = 0; s < 8; s += 2) {
            int v = 1 + (s >= 4) + 2 * (t >= 4);
            ram[IMAGE + t * 4 + s / 2] = (uint8_t)(v << 4 | v);
        }
    for (int i = 0; i < 4; i++) w16(MTX + i * 10, 1); /* identity: the diagonal's integer parts, no fractions */
    int16_t quad[4][4] = {{-1, 1, 0, 0}, {1, 1, 8, 0}, {1, -1, 8, 8}, {-1, -1, 0, 8}}; /* x, y, s, t */
    for (int i = 0; i < 4; i++) {
        uint32_t v = VTX + i * 16;
        w16(v, (uint16_t)quad[i][0]); w16(v + 2, (uint16_t)quad[i][1]); w16(v + 4, 0);
        w16(v + 8, (uint16_t)(quad[i][2] * 32 * scale)); w16(v + 10, (uint16_t)(quad[i][3] * 32 * scale));
        w32(v + 12, 0xFFFFFFFFu);
    }
    gfx(0xFF100000u | 319, 0x80000000u | FB);                               /* G_SETCIMG: 320 wide */
    gfx(0xEF000000u | (persp ? 0x80000 : 0), 0);                            /* one cycle, perspective or not */
    gfx(0xFC000000u | 1 << 20 | 6 << 15 | 1 << 12 | 6 << 9, 15u << 28 | 7 << 15 | 7 << 12 | 7 << 9); /* TEXEL0 */
    gfx(0xFD100000u, 0x80000000u | TLUT);                                   /* the palette */
    gfx(0xF5000100u, 7u << 24);
    gfx(0xF0000000u, 7u << 24 | 15u << 14);
    gfx(0xFD000000u | 2 << 21 | 7, 0x80000000u | IMAGE);                    /* CI4, 8 wide */
    gfx(0xF5000000u | 2 << 21 | 1 << 9, 0);
    gfx(0xF4000000u, 0u << 24 | (7 << 2) << 12 | 7 << 2);
    gfx(0xD7000002u, 0xFFFFFFFFu);                                          /* G_TEXTURE: on, scale 1 */
    gfx(0xDA380007u, 0x80000000u | MTX);                                    /* projection: identity */
    gfx(0xDA380003u, 0x80000000u | MTX);                                    /* model view: identity */
    gfx(0x01000000u | 4 << 12 | 4 << 1, 0x80000000u | VTX);
    gfx(0x06000204u, 0x00000406u);
    gfx(0xDF000000u, 0);
    port_render_task(0x80000000u | TASK);
    int at[4][2] = {{80, 60}, {240, 60}, {80, 180}, {240, 180}}; /* the screen's quadrants */
    for (int i = 0; i < 4; i++) {
        uint32_t a = FB + (uint32_t)(at[i][1] * 320 + at[i][0]) * 2;
        printf("%04X\n", (unsigned)(ram[a] << 8 | ram[a + 1]));
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    folder = tmp_path_factory.mktemp("render")
    (folder / "harness.c").write_text(HARNESS)
    program = folder / "render"
    subprocess.run([cc, "-O1", "-o", str(program), str(folder / "harness.c"), str(RENDER), "-lm"],
                   check=True, capture_output=True, timeout=120)
    return program


def quadrants(program: Path, persp: bool) -> list[int]:
    out = subprocess.run([str(program), "1" if persp else "0"], check=True, capture_output=True, text=True,
                         timeout=60).stdout
    return [int(line, 16) for line in out.split()]


def test_a_4bit_texture_with_perspective_is_sampled_whole(renderer):
    """The car skins' case: each screen quadrant shows its own texture quadrant, not the top-left one stretched."""
    assert quadrants(renderer, True) == [0xF801, 0x07C1, 0x003F, 0xFFFF]


def test_coordinates_without_perspective_are_taken_at_half_scale(renderer):
    """The menu font's case: doubled coordinates, drawn without perspective correction, cover the texture once."""
    assert quadrants(renderer, False) == [0xF801, 0x07C1, 0x003F, 0xFFFF]
