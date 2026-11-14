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
void port_frame_shown(uint32_t fb, unsigned long n);
static int live; /* the frame as the VI shows it, filtered: captured here */
static uint8_t shown[320 * 240 * 3];
int port_live_active(void) { return live; }
void port_live_frame(const uint8_t *rgb, int w, int h) { if (w == 320 && h == 240) memcpy(shown, rgb, sizeof shown); }

static void w32(uint32_t a, uint32_t v) { ram[a] = v >> 24; ram[a + 1] = v >> 16; ram[a + 2] = v >> 8; ram[a + 3] = v; }
static void w16(uint32_t a, uint16_t v) { ram[a] = v >> 8; ram[a + 1] = v; }
static uint32_t dl = 0x1000;
static void gfx(uint32_t w0, uint32_t w1) { w32(dl, w0); w32(dl + 4, w1); dl += 8; }

enum { TASK = 0x100, UCODE = 0x200, TLUT = 0x400, MTX = 0x600, VTX = 0x700, IMAGE = 0x2000, FB = 0x10000 };

/* argv[1]: 1 with perspective correction, 0 without; argv[2]: ci4 (LoadTile), rgba32block, rgba32tile, sprite
   (S2DEX: the CI4 image drawn by G_OBJ_SPRITE through a 2D matrix), or aa / aa-edge (anti-aliased, untextured red;
   the frame as the VI filter shows it);
   argv[3], if any: "bilerp", the RDP's bilinear filter (G_TF_BILERP); or "fog": two cycles, the blender's first
   fogging (fog colour * shade alpha + in * (1 - shade alpha)), G_FOG on, the fog colour blue, the RSP's fog alpha 128;
   or "pass": force blend with the blender's selectors IN * 0 + IN * 1 (G_RM_PASS), and half-transparent 32-bit texels */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int persp = argv[1][0] == '1', scale = persp ? 1 : 2; /* without perspective, coordinates twice as large */
    const char *kind = argv[2];
    int bilerp = argc > 3 && !strcmp(argv[3], "bilerp"), fog = argc > 3 && !strcmp(argv[3], "fog");
    int pass = argc > 3 && !strcmp(argv[3], "pass");
    int sprite = !strcmp(kind, "sprite"); /* S2DEX: G_OBJ_SPRITE through a 2D matrix */
    int aa = !strncmp(kind, "aa", 2); /* anti-aliased, red, untextured: "aa" both triangles, "aa-edge" one */
    memcpy(ram + UCODE, sprite ? "RSP Gfx ucode S2DEX " : "RSP Gfx ucode F3DEX2", 20);
    w32(TASK + 24, 0x80000000u | UCODE);
    w32(TASK + 48, 0x80000000u | dl);
    uint16_t colours[5] = {0, 0xF801 /* red */, 0x07C1 /* green */, 0x003F /* blue */, 0xFFFF /* white */};
    uint32_t colours32[5] = {0, 0xFF0000FF, 0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF};
    if (pass) for (int i = 1; i < 5; i++) colours32[i] = (colours32[i] & ~0xFFu) | 0x80; /* half transparent */
    for (int i = 0; i < 16; i++) w16(TLUT + i * 2, i < 5 ? colours[i] : 0);
    for (int t = 0; t < 8; t++) /* the quadrants are colours 1 to 4 */
        for (int s = 0; s < 8; s++) {
            int v = 1 + (s >= 4) + 2 * (t >= 4);
            if (kind[0] == 'c' || sprite) ram[IMAGE + t * 4 + s / 2] |= (uint8_t)(s & 1 ? v : v << 4);
            else w32(IMAGE + (t * 8 + s) * 4, colours32[v]);
        }
    if (sprite) { /* the CI4 image into TMEM; a uObjMtx turning it 90 degrees, ten times larger; G_OBJ_SPRITE */
        enum { OBJMTX = 0x800, SPRITE = 0x900 };
        w32(OBJMTX, 0); w32(OBJMTX + 4, (uint32_t)(-10 * 65536)); w32(OBJMTX + 8, 10 * 65536); w32(OBJMTX + 12, 0);
        w16(OBJMTX + 16, 100 * 4); w16(OBJMTX + 18, 50 * 4); w16(OBJMTX + 20, 1024); w16(OBJMTX + 22, 1024);
        w16(SPRITE, 0); w16(SPRITE + 2, 1024); w16(SPRITE + 4, 8 << 5); w16(SPRITE + 8, 0); w16(SPRITE + 10, 1024);
        w16(SPRITE + 12, 8 << 5); w16(SPRITE + 16, 1); w16(SPRITE + 18, 0);
        ram[SPRITE + 20] = 2; ram[SPRITE + 21] = 0; ram[SPRITE + 22] = 0; /* CI4, palette 0 */
        gfx(0xFF100000u | 319, 0x80000000u | FB);
        gfx(0xEF000000u, 0);
        gfx(0xFC000000u | 1 << 20 | 6 << 15 | 1 << 12 | 6 << 9 | 15 << 5 | 31,
            15u << 28 | 7 << 15 | 7 << 12 | 7 << 9 | 15u << 24 | 7 << 21 | 7 << 18 | 7 << 3);
        gfx(0xFD100000u, 0x80000000u | TLUT);
        gfx(0xF5000100u, 7u << 24);
        gfx(0xF0000000u, 7u << 24 | 15u << 14);
        gfx(0xFD000000u | 2 << 21 | 7, 0x80000000u | IMAGE);
        gfx(0xF5000000u | 2 << 21 | 1 << 9, 0);
        gfx(0xF4000000u, (7 << 2) << 12 | 7 << 2);
        gfx(0xDC170000u, 0x80000000u | OBJMTX);                              /* G_OBJ_MOVEMEM: the uObjMtx */
        gfx(0x02000000u, 0x80000000u | SPRITE);                              /* G_OBJ_SPRITE */
        gfx(0xDF000000u, 0);
        port_render_task(0x80000000u | TASK);
        /* texel (s, t) lands at (100 - 10 t, 50 + 10 s): each quadrant at its own corner */
        int at[4][2] = {{95, 55}, {95, 115}, {25, 55}, {25, 115}};
        for (int i = 0; i < 4; i++) {
            uint32_t a = FB + (uint32_t)(at[i][1] * 320 + at[i][0]) * 2;
            printf("%04X\n", (unsigned)(ram[a] << 8 | ram[a + 1]));
        }
        return 0;
    }
    for (int i = 0; i < 4; i++) w16(MTX + i * 10, 1); /* identity: the diagonal's integer parts, no fractions */
    int16_t quad[4][4] = {{-1, 1, 0, 0}, {1, 1, 8, 0}, {1, -1, 8, 8}, {-1, -1, 0, 8}}; /* x, y, s, t */
    for (int i = 0; i < 4; i++) {
        uint32_t v = VTX + i * 16;
        w16(v, (uint16_t)quad[i][0]); w16(v + 2, (uint16_t)quad[i][1]); w16(v + 4, 0);
        w16(v + 8, (uint16_t)(quad[i][2] * 32 * scale)); w16(v + 10, (uint16_t)(quad[i][3] * 32 * scale));
        w32(v + 12, aa ? 0xFF0000FFu : 0xFFFFFFFFu);
    }
    gfx(0xFF100000u | 319, 0x80000000u | FB);                               /* G_SETCIMG: 320 wide */
    if (aa) { /* AA_EN, the depth buffer, the shade's colour: two triangles' shared edge, or one's silhouette */
        gfx(0xEF000000u, 0x8 | 0x10 | 0x20);
        gfx(0xFC000000u | 15 << 20 | 31 << 15 | 7 << 12 | 7 << 9 | 15 << 5 | 31,
            15u << 28 | 4 << 15 | 7 << 12 | 4 << 9 | 15u << 24 | 7 << 21 | 7 << 18 | 7 << 3);
        gfx(0xD7000000u, 0);
        gfx(0xD9FFFFFFu, 0x00000001u);                                       /* G_ZBUFFER */
        gfx(0xDA380007u, 0x80000000u | MTX);
        gfx(0xDA380003u, 0x80000000u | MTX);
        gfx(0x01000000u | 4 << 12 | 4 << 1, 0x80000000u | VTX);
        if (!strcmp(kind, "aa")) gfx(0x06000204u, 0x00000406u); else gfx(0x05000204u, 0);
        gfx(0xDF000000u, 0);
        port_render_task(0x80000000u | TASK);
        live = 1;
        port_frame_shown(0x80000000u | FB, 1);
        /* the shared diagonal (y = 0.75 x) at two places, inside the first triangle, inside the second */
        int at[4][2] = {{100, 75}, {200, 150}, {240, 40}, {40, 200}};
        for (int i = 0; i < 4; i++) {
            const uint8_t *p = shown + (at[i][1] * 320 + at[i][0]) * 3;
            printf("%02X%02X%02X\n", p[0], p[1], p[2]);
        }
        return 0;
    }
    gfx(0xEF000000u | (persp ? 0x80000 : 0) | (bilerp ? 0x2000 : 0) | (fog ? 1 << 20 : 0),
        fog ? 3u << 30 | 2u << 26 | 1u << 20 | 1u << 16                 /* cycles, perspective, filter, blender */
            : pass ? 0x4000u | 3u << 26 | 2u << 18 | 3u << 24 | 2u << 16 : 0);
    gfx(0xFC000000u | 1 << 20 | 6 << 15 | 1 << 12 | 6 << 9 | 15 << 5 | 31,  /* TEXEL0, then the combined colour */
        15u << 28 | 7 << 15 | 7 << 12 | 7 << 9 | 15u << 24 | 7 << 21 | 7 << 18 | 7 << 3);
    if (fog) {
        gfx(0xF8000000u, 0x0000FFFFu);                                      /* G_SETFOGCOLOR: blue */
        gfx(0xDB080000u, 0x00000080u);                                      /* G_MW_FOG: multiplier 0, offset 128 */
        gfx(0xD9FFFFFFu, 0x00010000u);                                      /* G_FOG */
    }
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


def test_fog_blends_the_fog_colour_by_the_rsps_fog_alpha(renderer):
    """A racing game fogs most of its races: G_FOG makes each vertex's shade alpha the fog's, from its depth and
    G_MW_FOG (here 128 everywhere), and the blender's first cycle mixes the fog colour in by it. The red quadrant comes
    out half red, half the blue fog; without fog it would be red."""
    pixels = drawn(renderer, True, "ci4", "fog")
    red, green, blue = pixels[0] >> 11 & 31, pixels[0] >> 6 & 31, pixels[0] >> 1 & 31
    assert 13 <= red <= 18 and green == 0 and 13 <= blue <= 18, hex(pixels[0])
    assert drawn(renderer, True, "ci4")[0] == 0xF801


def test_a_force_blend_that_passes_its_input_draws_it_opaque(renderer):
    """The blender does what its selectors say: IN * 0 + IN * 1 is the incoming colour, opaque, even under force
    blend and with half-transparent texels (a racing game's menu layers, dimmed by their primitive colour, not their
    alpha). The renderer once blended every force-blend draw by alpha whatever its selectors."""
    assert drawn(renderer, True, "rgba32tile", "pass")[0] == 0xF801


def test_a_sprite_turns_and_scales_with_the_2d_matrix(renderer):
    """G_OBJ_SPRITE draws through the 2D matrix (G_OBJ_MOVEMEM's uObjMtx): here turned 90 degrees and ten times
    larger, each quadrant of the image lands at its own corner. A racing game's rev counter needle turns this way;
    the renderer once drew every sprite unrotated, a small square at its own place."""
    assert drawn(renderer, True, "sprite") == [0xF801, 0x07C1, 0x003F, 0xFFFF]


def test_anti_aliasing_joins_a_meshs_triangles_without_a_seam(renderer):
    """Anti-aliased, the pixels two triangles share along their edge each get part of both: at the same depth the
    second blends with the first by their coverages, which add up to a whole pixel. The mesh shows no seam: a shared
    edge's pixels never blend with what's behind the mesh (here black), which would darken it."""
    assert drawn(renderer, True, "aa") == [0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000]


def test_anti_aliasing_softens_a_silhouettes_edge(renderer):
    """One triangle's edge over the background: its edge pixels keep their partial coverage, and the VI's filter
    blends them toward the background around them (black), between the two; inside, red; outside, black."""
    pixels = drawn(renderer, True, "aa-edge")
    for edge in pixels[:2]:
        red = edge >> 16
        assert 0x20 <= red <= 0xE0 and edge & 0xFFFF == 0, hex(edge)
    assert pixels[2:] == [0xFF0000, 0x000000]
