"""The N64 WebAssembly port's software renderer (consoles/n64/port_wasm/render.c), compiled with a small harness
that hands it one display list: a full-screen quad textured with an 8x8 image whose four quadrants have four
colours, and reads back the middle of each screen quadrant.

A racing game's cars are 4-bit skins drawn with perspective correction, and once showed only the top-left quarter of
their skin because every 4-bit texture had its coordinates halved; only coordinates drawn without perspective
correction are taken at half scale (its menu font). Its soft car shadows are 32-bit textures, which the RDP keeps
split across TMEM's halves (a tile's line counts two bytes a texel); kept whole, they came out as dark stripes."""

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

enum { TASK = 0x100, UCODE = 0x200, TLUT = 0x400, MTX = 0x600, VTX = 0x700, IMAGE = 0x2000, FB = 0x10000 };

/* argv[1]: 1 with perspective correction, 0 without; argv[2]: ci4 (LoadTile), rgba32block or rgba32tile;
   argv[3], if any: "bilerp", the RDP's bilinear filter (G_TF_BILERP) */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int persp = argv[1][0] == '1', scale = persp ? 1 : 2; /* without perspective, coordinates twice as large */
    const char *kind = argv[2];
    int bilerp = argc > 3 && !strcmp(argv[3], "bilerp");
    memcpy(ram + UCODE, "RSP Gfx ucode F3DEX2", 20);
    w32(TASK + 24, 0x80000000u | UCODE);
    w32(TASK + 48, 0x80000000u | dl);
    uint16_t colours[5] = {0, 0xF801 /* red */, 0x07C1 /* green */, 0x003F /* blue */, 0xFFFF /* white */};
    uint32_t colours32[5] = {0, 0xFF0000FF, 0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF};
    for (int i = 0; i < 16; i++) w16(TLUT + i * 2, i < 5 ? colours[i] : 0);
    for (int t = 0; t < 8; t++) /* the quadrants are colours 1 to 4 */
        for (int s = 0; s < 8; s++) {
            int v = 1 + (s >= 4) + 2 * (t >= 4);
            if (kind[0] == 'c') ram[IMAGE + t * 4 + s / 2] |= (uint8_t)(s & 1 ? v : v << 4);
            else w32(IMAGE + (t * 8 + s) * 4, colours32[v]);
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
    gfx(0xEF000000u | (persp ? 0x80000 : 0) | (bilerp ? 0x2000 : 0), 0);  /* one cycle, perspective, filter */
    gfx(0xFC000000u | 1 << 20 | 6 << 15 | 1 << 12 | 6 << 9, 15u << 28 | 7 << 15 | 7 << 12 | 7 << 9); /* TEXEL0 */
    if (kind[0] == 'c') {
        gfx(0xFD100000u, 0x80000000u | TLUT);                               /* the palette */
        gfx(0xF5000100u, 7u << 24);
        gfx(0xF0000000u, 7u << 24 | 15u << 14);
        gfx(0xFD000000u | 2 << 21 | 7, 0x80000000u | IMAGE);                /* CI4, 8 wide, one 8-byte word a row */
        gfx(0xF5000000u | 2 << 21 | 1 << 9, 0);
        gfx(0xF4000000u, (7 << 2) << 12 | 7 << 2);
    } else {
        gfx(0xFD180000u | 7, 0x80000000u | IMAGE);                          /* RGBA32, 8 wide */
        if (!strcmp(kind, "rgba32block")) {                                 /* 64 texels, 4 words a row: dxt 0x200 */
            gfx(0xF5180000u, 7u << 24);
            gfx(0xF3000000u, 7u << 24 | 63 << 12 | 0x200);
            gfx(0xF5180000u | 2 << 9, 0);                                   /* line: 8 texels, 2 bytes each */
            gfx(0xF2000000u, (7 << 2) << 12 | 7 << 2);
        } else {
            gfx(0xF5180000u | 2 << 9, 0);
            gfx(0xF4000000u, (7 << 2) << 12 | 7 << 2);
        }
    }
    gfx(0xD7000002u, 0xFFFFFFFFu);                                          /* G_TEXTURE: on, scale 1 */
    gfx(0xDA380007u, 0x80000000u | MTX);                                    /* projection: identity */
    gfx(0xDA380003u, 0x80000000u | MTX);                                    /* model view: identity */
    gfx(0x01000000u | 4 << 12 | 4 << 1, 0x80000000u | VTX);
    gfx(0x06000204u, 0x00000406u);
    gfx(0xDF000000u, 0);
    port_render_task(0x80000000u | TASK);
    int at[9][2] = {{40, 30}, {120, 90}, {200, 30}, {280, 90}, {40, 150}, {120, 210}, {200, 150}, {280, 210},
                    {140, 60}}; /* the last: s = 3.51, half way from texel 3 (red) to texel 4 (green) */
    for (int i = 0; i < 9; i++) { /* two points in each screen quadrant: an even and an odd texel row */
        uint32_t a = FB + (uint32_t)(at[i][1] * 320 + at[i][0]) * 2;
        printf("%04X\n", (unsigned)(ram[a] << 8 | ram[a + 1]));
    }
    return 0;
}
"""

QUADRANTS = [0xF801, 0xF801, 0x07C1, 0x07C1, 0x003F, 0x003F, 0xFFFF, 0xFFFF, 0xF801]


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


def drawn(program: Path, persp: bool, kind: str = "ci4", *extra: str) -> list[int]:
    out = subprocess.run([str(program), "1" if persp else "0", kind, *extra], check=True, capture_output=True,
                         text=True, timeout=60).stdout
    return [int(line, 16) for line in out.split()]


def test_a_4bit_texture_with_perspective_is_sampled_whole(renderer):
    """The car skins' case: each screen quadrant shows its own texture quadrant, not the top-left one stretched."""
    assert drawn(renderer, True) == QUADRANTS


def test_coordinates_without_perspective_are_taken_at_half_scale(renderer):
    """The menu font's case: doubled coordinates, drawn without perspective correction, cover the texture once."""
    assert drawn(renderer, False) == QUADRANTS


@pytest.mark.parametrize("kind", ["rgba32block", "rgba32tile"])
def test_a_32bit_texture_is_read_at_its_tiles_line(renderer, kind):
    """The car shadows' case: a 32-bit texture loaded by LoadBlock or LoadTile into a tile whose line counts two
    bytes a texel reads back row by row, odd rows included."""
    assert drawn(renderer, True, kind) == QUADRANTS


@pytest.mark.parametrize("kind", ["ci4", "rgba32tile"])
def test_the_rdps_bilinear_filter_blends_neighbouring_texels(renderer, kind):
    """With G_TF_BILERP (a racing game's 3D and menu text) a point half way between a red and a green texel is a
    blend of both, where point sampling gives the red one; inside a quadrant's own colour nothing changes. (The
    odd-row probes lie a sixtieth of a texel from a quadrant's edge, so the filter rightly tints them.)"""
    pixels = drawn(renderer, True, kind, "bilerp")
    assert pixels[0:8:2] == QUADRANTS[0:8:2]
    assert drawn(renderer, True, kind)[8] == 0xF801  # point sampling: the red texel
    red, green, blue = pixels[8] >> 11 & 31, pixels[8] >> 6 & 31, pixels[8] >> 1 & 31
    assert 10 <= red <= 21 and 10 <= green <= 21 and blue == 0, hex(pixels[8])
