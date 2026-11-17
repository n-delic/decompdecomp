"""The N64 WebAssembly port's renderer against the RDP's own arithmetic (consoles/n64/port_wasm/render.c).

Two kinds of test. The first always run: each draws a small display list (tests/n64_render_kit.py) where the RDP's
fixed-point rules give a different picture from the floating point the renderer once used, and checks the pixel the
console draws. The second (test_scene_matches_angrylion) need a checkout of angrylion-rdp-plus, which reproduces the
RDP bit for bit, named by N64_ANGRYLION: each scene is drawn with PORT_RDPDUMP on, its RDP commands replayed through
angrylion by tools/n64_rdp_compare.py, and the two pictures compared. Without it they skip (CI has none).
"""

from __future__ import annotations

import importlib.util
import os
import struct
from pathlib import Path

import pytest

from tests.n64_render_kit import (
    FB,
    SHADE,
    TEX,
    ZB,
    Gfx,
    H,
    Memory,
    W,
    build,
    combine,
    load_texture,
    matrix,
    quadrant_texture,
    run,
    ucode_data,
    vertex,
)

NO_DITHER = 3 << 6          # other mode H: G_CD_DISABLE
FILTER = 6 << 9 | 2 << 12   # G_TC_FILT, G_TF_BILERP
PERSP = 0x80000             # G_TP_PERSP
QUARTER = [1 / 640, 0, 0, 0, 0, 1 / 480, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]  # model units: quarter pixels from the centre


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    program = build(tmp_path_factory.mktemp("render"))
    if not program:
        pytest.skip("no C compiler")
    return program


def _start(mem: Memory, g: Gfx, model: list[float] | None = None) -> None:
    ident = mem.put(matrix())
    g.frame().fill(0)
    g(0xED000000, (W * 4) << 12 | H * 4)
    g(0xDA380007, 0x80000000 | ident)
    g(0xDA380003, 0x80000000 | mem.put(matrix(model)))


def _triangles(mem: Memory, g: Gfx, points: list[tuple], first: int = 0) -> None:
    """Vertices (x, y, z, s, t, rgba) loaded at `first`, drawn three at a time."""
    v = mem.put(b"".join(vertex(x, y, z, s, t, c) for x, y, z, s, t, c in points))
    g(0x01000000 | len(points) << 12 | (first + len(points)) << 1, 0x80000000 | v)
    for i in range(0, len(points), 3):
        a, b, c = (first + i) * 2, (first + i + 1) * 2, (first + i + 2) * 2
        g(0x05000000 | a << 16 | b << 8 | c)


def _quarters(x: float, y: float) -> tuple[int, int]:
    """A screen point in pixels as model units under QUARTER."""
    return round((x - W / 2) * 4), round((H / 2 - y) * 4)


def test_a_pixel_is_drawn_when_its_top_left_corner_is_inside(renderer, tmp_path):
    """The RDP decides a pixel without anti-aliasing by its first sample point, its top-left corner. A triangle
    whose left edge is at x = 10.25 doesn't draw pixel 10 (its corner, 10.0, is outside; its centre 10.5 is
    inside), and draws 11. Sampling centres, as the renderer did, drew both."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g, QUARTER)
    g.othermode(NO_DITHER, 0)
    g(*SHADE)
    g(0xD9000000, 0x200004)
    red = 0xFF0000FF
    _triangles(mem, g, [(*_quarters(10.25, 20), 0, 0, 0, red), (*_quarters(10.25, 200), 0, 0, 0, red),
                        (*_quarters(300, 110), 0, 0, 0, red)])
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(10, 110) == (0, 0, 0)
    assert out.pixel(11, 110) == (31, 0, 0)


def test_a_fully_covered_pixel_passes_the_depth_test_only_when_nearer(renderer, tmp_path):
    """Z_CMP for whole pixels is strictly nearer: a green square at exactly the depth of the red one already drawn
    doesn't replace it. Passing anything within delta z, as the renderer did, let the later surface win."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    g.zimage()
    g(0xFF100000 | (W - 1), 0x80000000 | ZB)
    g.fill(0xFFFC)
    g.frame()
    g(0xED000000, (W * 4) << 12 | H * 4)
    g.othermode(NO_DITHER, 0x10 | 0x20)
    g(*SHADE)
    g(0xD9000000, 0x200005)
    for colour, first in ((0xFF0000FF, 0), (0x00FF00FF, 6)):
        quad = [(-1, 1, 0, 0, 0, colour), (1, 1, 0, 0, 0, colour), (1, -1, 0, 0, 0, colour),
                (-1, 1, 0, 0, 0, colour), (1, -1, 0, 0, 0, colour), (-1, -1, 0, 0, 0, colour)]
        _triangles(mem, g, quad, first)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 2, H // 2) == (31, 0, 0)


def test_the_combiners_one_is_256_and_255_is_not_one(renderer, tmp_path):
    """The combiner multiplies 8-bit values with 1 as 256: a shade of 136 by a primitive colour of 255 is
    (136 * 255 + 128) >> 8 = 135, which cuts to 16 in 5 bits. Taking 255 as 1, as the renderer did, kept 136: 17."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    g.othermode(NO_DITHER, 0)
    g(0xFA000000, 0xFFFFFFFF)
    g(*combine(4, 15, 3, 7, 7, 7, 7, 4))  # SHADE * PRIM
    g(0xD9000000, 0x200004)
    grey = 0x888888FF
    _triangles(mem, g, [(-1, 1, 0, 0, 0, grey), (1, 1, 0, 0, 0, grey), (1, -1, 0, 0, 0, grey)])
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W * 3 // 4, H // 4) == (16, 16, 16)


def test_the_alpha_dither_reaches_alpha_compare(renderer, tmp_path):
    """libultra's default alpha dither (the pattern) adds the magic square's value to the combined alpha before the
    alpha compare: an alpha of 125 against a threshold of 128 fails where the square is 0 (pixel 0, 0) and passes
    where it's 7 (pixel 3, 0). Without the dither, every pixel failed."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    g.othermode(NO_DITHER, 0x1)                 # G_AC_THRESHOLD, alpha dither: the pattern (0)
    g(0xF9000000, 0x00000080)                   # the blend colour's alpha: the threshold
    g(*SHADE)
    g(0xD9000000, 0x200004)
    c = 0xFF00007D
    _triangles(mem, g, [(-1, 1, 0, 0, 0, c), (1, 1, 0, 0, 0, c), (1, -1, 0, 0, 0, c),
                        (-1, 1, 0, 0, 0, c), (1, -1, 0, 0, 0, c), (-1, -1, 0, 0, 0, c)])
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(0, 0) == (0, 0, 0)
    assert out.pixel(3, 0) == (31, 0, 0)


def test_copy_mode_reads_past_the_tile_without_clamping(renderer, tmp_path):
    """Copy mode wraps texture coordinates by the tile's mask but never clamps them: past an 8x8 texture with no
    mask, a copied rectangle reads on through TMEM, row by row (a row is 16 bytes here), and past the texture's
    128 bytes into empty TMEM (black). The renderer clamped, and repeated the texture's last texels."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    load_texture(g, quadrant_texture(mem))
    g.othermode(NO_DITHER | 2 << 20, 0)
    g(0xE4000000 | (100 * 4) << 12 | (40 * 4), 0 << 24 | (40 * 4) << 12 | (20 * 4))
    g(0xE1000000, 0)
    g(0xF1000000, 0x10000400)                   # dsdx 4 (four texels a cycle), dtdy 1
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(45, 22) == (0, 31, 0)      # s 5, t 2: the top right quadrant
    assert out.pixel(95, 22) == (0, 0, 0)       # s 55: 142 bytes in, past the texture


def test_triangles_are_clipped_to_the_guard_band(renderer, tmp_path):
    """The microcode clips triangles at a guard band (G_MW_CLIP's ratio of w, 2 by default), so the RDP never gets
    coordinates beyond its range of about 2048 pixels. One reaching 50 screens to the right still fills the screen,
    and every triangle command it sends stays in range."""
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    g.othermode(NO_DITHER, 0)
    g(*SHADE)
    g(0xD9000000, 0x200004)
    c = 0x00FF00FF
    _triangles(mem, g, [(-1, 2, 0, 0, 0, c), (100, -1, 0, 0, 0, c), (-1, -1, 0, 0, 0, c)])
    g(0xDF000000)
    os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"] = str(dumps), "1"
    try:
        out, _ = run(renderer, mem, tmp_path, g.end())
    finally:
        del os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"]
    assert out.pixel(W - 2, H - 2) == (0, 31, 0)
    words = (dumps / "000001.cmds").read_bytes()
    words = struct.unpack(f">{len(words) // 4}I", words)
    triangles = 0
    i = 0
    while i < len(words):
        op = words[i] >> 24 & 0x3F
        if 0x08 <= op <= 0x0F:
            triangles += 1
            for x in (words[i + 2], words[i + 4], words[i + 6]):
                assert abs(struct.unpack(">i", struct.pack(">I", x))[0] / 65536) < 2048
            i += 8 + (16 if op & 4 else 0) + (16 if op & 2 else 0) + (4 if op & 1 else 0)
        else:
            i += 4 if op in (0x24, 0x25) else 2
    assert triangles >= 1


def test_full_alpha_passes_the_colour_through_the_blender(renderer, tmp_path):
    """IN * IN_A + MEM * (1 - IN_A) under force blend: with a full alpha the RDP doesn't blend at all (its partial
    reject) and writes IN as it is. Blended, IN's weight is 31/32 and a 32nd of the blue behind came through, a red
    of 248 coming out as 240 (30 in 5 bits)."""
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    g.fill(0x003F)                                          # blue behind
    g.othermode(NO_DITHER, 0x4000 | 0x40 | 1 << 22)         # force blend, image read, IN * IN_A + MEM * (1 - IN_A)
    g(*SHADE)
    g(0xD9000000, 0x200004)
    red = 0xFF0000FF
    _triangles(mem, g, [(-1, 1, 0, 0, 0, red), (1, 1, 0, 0, 0, red), (1, -1, 0, 0, 0, red)])
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W * 3 // 4, H // 4) == (31, 0, 0)


def test_s2dex_draws_through_rdp_commands(renderer, tmp_path):
    """S2DEX's sprites and backgrounds reach the RDP as the microcode sends them: texture loads, tiles and texture
    rectangles, recorded with the rest of the task (so tools/n64_rdp_compare.py can replay them). The sprite still
    lands where it should: four times larger at (100, 50), its top-left quadrant red."""
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    mem = Memory()
    g = Gfx(mem, "S2DEX2")
    g.frame().fill(0)
    g.othermode(NO_DITHER | 6 << 9, 0)
    g(*TEX)
    g(0x05000000, 0x80000000 | _obj_block(mem, quadrant_texture(mem), 8, 8))       # G_OBJ_LOADTXTR
    g(0x01000000, 0x80000000 | _obj_sprite(mem, 100, 50, 4, 8, 8))                     # G_OBJ_RECTANGLE
    g(0xDF000000)
    os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"] = str(dumps), "1"
    try:
        out, _ = run(renderer, mem, tmp_path, g.end())
    finally:
        del os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"]
    assert out.pixel(104, 54) == (31, 0, 0) and out.pixel(120, 70) == (31, 31, 31)
    words = (dumps / "000001.cmds").read_bytes()
    ops = [w >> 24 for w in struct.unpack(f">{len(words) // 4}I", words)]
    assert 0xF3 in ops and 0xE4 in ops  # the load (LoadBlock) and the rectangle
    assert (dumps / "000001.after").exists()


def _obj_block(mem: Memory, image: int, width: int, height: int) -> int:
    """A uObjTxtr loading an RGBA16 image into TMEM 0 by LoadBlock: its 8-byte words less one, and the line step."""
    words = width * height * 2 // 8
    line = width * 2 // 8
    return mem.put(struct.pack(">IIHHHHII", 0x00001033, 0x80000000 | image, 0, words - 1, (2048 + line - 1) // line,
                               0, 0, 0xFFFFFFFF))


def _obj_tile(mem: Memory, image: int, tmem: int, width_words: int, height: int) -> int:
    """A uObjTxtr loading a texture by LoadTile: its width in 8-byte words and height in lines, each times 4 less 1."""
    return mem.put(struct.pack(">IIHHHHII", 0x00FC1034, 0x80000000 | image, tmem, width_words * 4 - 1, height * 4 - 1,
                               0, 0, 0xFFFFFFFF))


def _obj_tlut(mem: Memory, palette: int, count: int) -> int:
    """A uObjTxtr loading a palette of `count` colours into TMEM's upper half (entry 256)."""
    return mem.put(struct.pack(">IIHHHHII", 0x00000030, 0x80000000 | palette, 256, count - 1, 0, 0, 0, 0xFFFFFFFF))


def _obj_sprite(mem: Memory, x: float, y: float, scale: float, width: int, height: int, flags: int = 0, fmt: int = 0,
                siz: int = 2, stride: int = 2) -> int:
    """A uObjSprite: its place (s10.2), scale (u5.10: 1024 / scale), image size (u10.5), TMEM line (`stride`
    8-byte words) at TMEM 0, format and flags (1: flipped across, 16: down)."""
    return mem.put(struct.pack(">hHHhhHHhHHBBBB", round(x * 4), round(1024 / scale), width << 5, 0, round(y * 4),
                               round(1024 / scale), height << 5, 0, stride, 0, fmt, siz, 0, flags))


def _obj_bg(mem: Memory, image: int, width: int, height: int, frame: tuple, at: tuple = (0, 0),
            scale: tuple = (1.0, 1.0)) -> int:
    """A uObjBg of an RGBA16 image: drawn into the frame rectangle (x, y, w, h, pixels), from image position `at`
    (texels, wrapping) at `scale` texels a pixel."""
    x, y, w, h = frame
    return mem.put(struct.pack(">HHhHHHhHIHBBHHHHiI", round(at[0] * 32), width * 4, x * 4, w * 4, round(at[1] * 32),
                               height * 4, y * 4, h * 4, 0x80000000 | image, 0, 0, 2, 0, 0, round(scale[0] * 1024),
                               round(scale[1] * 1024), 0, 0))


# ---------------------------------------------------------------- the same scenes through angrylion

def _scenes() -> dict:
    """Display lists covering the RDP's pipeline: each a function filling a Gfx."""
    def gradient(mem, g):
        g.othermode(NO_DITHER, 0)
        g(*SHADE)
        g(0xD9000000, 0x200004)
        _triangles(mem, g, [(-1, 1, 0, 0, 0, 0x2040A0FF), (1, 1, 0, 0, 0, 0xA02040FF), (1, -1, 0, 0, 0, 0x40A020FF),
                            (-1, 1, 0, 0, 0, 0x2040A0FF), (1, -1, 0, 0, 0, 0x40A020FF),
                            (-1, -1, 0, 0, 0, 0xF0F0F0FF)], 20)

    def scaled(mem, g):
        g(0xDA380003, 0x80000000 | mem.put(matrix([0.01, 0, 0, 0, 0, 0.01, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])))

    def shade(mem, g):
        g.othermode(NO_DITHER, 0)
        g(*SHADE)
        g(0xD9000000, 0x200004)
        scaled(mem, g)
        _triangles(mem, g, [(-90, 70, 0, 0, 0, 0xFF0000FF), (80, 33, 0, 0, 0, 0x00FF00FF),
                            (-20, -95, 0, 0, 0, 0x0000FFFF)])

    def textured(persp: bool, filtered: bool, tilted: bool):
        def scene(mem, g):
            tex = quadrant_texture(mem)
            if tilted:
                g(0xDA380007, 0x80000000 | mem.put(matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -0.2, 0])))
                g(0xDA380003, 0x80000000 | mem.put(matrix([1.2, 0, 0, 0, 0, 0.9, 0.3, 0, 0, 0, 1, 0, 0.1, 0, -1.6, 1])))
            else:
                g(0xDA380003, 0x80000000 | mem.put(matrix([0.6, 0, 0, 0, 0, 0.6, 0, 0, 0, 0, 1, 0, 0.05, 0.03, 0, 1])))
            g.othermode(NO_DITHER | 6 << 9 | (PERSP if persp else 0) | (2 << 12 if filtered else 0), 0)
            g(*TEX)
            g(0xD7000002, 0xFFFFFFFF)
            g(0xD9000000, 0x200004)
            load_texture(g, tex)
            white = 0xFFFFFFFF
            q = [(-1, 1, 0, 0, 0, white), (1, 1, 0, 8, 0, white), (1, -1, 0, 8, 8, white), (-1, -1, 0, 0, 8, white)]
            _triangles(mem, g, [q[0], q[1], q[2], q[0], q[2], q[3]])
        return scene

    def depth(mem, g):
        g.zimage()
        g(0xFF100000 | (W - 1), 0x80000000 | ZB)
        g.fill(0xFFFC)
        g.frame()
        g(0xED000000, (W * 4) << 12 | H * 4)
        g.othermode(NO_DITHER, 0x10 | 0x20)
        g(*SHADE)
        g(0xD9000000, 0x200005)
        _triangles(mem, g, [(-1, 1, -1, 0, 0, 0xFF0000FF), (1, 0, 1, 0, 0, 0xFF8000FF), (-1, -1, -1, 0, 0, 0xFF0040FF),
                            (1, 1, -1, 0, 0, 0x00FF00FF), (-1, 0, 1, 0, 0, 0x00FF80FF), (1, -1, -1, 0, 0, 0x40FF00FF)])

    def rect(mem, g):
        gradient(mem, g)
        load_texture(g, quadrant_texture(mem))
        g.othermode(NO_DITHER | FILTER, 0)
        g(*TEX)
        g(0xE4000000 | (200 * 4 + 1) << 12 | (150 * 4 + 3), 0 << 24 | (21 * 4 + 2) << 12 | (17 * 4 + 1))
        g(0xE1000000, 0x00100008)
        g(0xF1000000, 0x00F000C0)
        g.othermode(NO_DITHER | 2 << 20 | 6 << 9, 0)
        g(0xE4000000 | (300 * 4) << 12 | (230 * 4), 0 << 24 | (230 * 4) << 12 | (160 * 4))
        g(0xE1000000, 0)
        g(0xF1000000, 0x10000400)

    def blend(mem, g):
        gradient(mem, g)
        load_texture(g, quadrant_texture(mem))
        g.othermode(NO_DITHER | FILTER, 0x4000 | 0x40 | 1 << 22)  # IN * IN_A + MEM * (1 - IN_A)
        g(*combine(1, 15, 4, 7, 1, 7, 4, 7))                       # TEXEL0 * SHADE
        g(0xD7000002, 0xFFFFFFFF)
        scaled(mem, g)
        _triangles(mem, g, [(-80, 60, 0, 0, 0, 0xFFFFFF80), (90, 20, 0, 8, 0, 0xFFC0FF40), (-10, -90, 0, 4, 8, 0x80FFFFC0)])

    def fog(mem, g):
        load_texture(g, quadrant_texture(mem))
        g(0xF8000000, 0x8090A0FF)
        g.othermode(NO_DITHER | FILTER | 1 << 20, 3 << 30 | 2 << 26 | 1 << 20 | 1 << 16)
        g(0xFC000000 | 1 << 20 | 4 << 15 | 7 << 12 | 7 << 9 | 15 << 5 | 31,
          15 << 28 | 15 << 24 | 7 << 21 | 7 << 18 | 7 << 15 | 7 << 12 | 4 << 9 | 0 << 6 | 7 << 3 | 0)
        g(0xD7000002, 0xFFFFFFFF)
        g(0xD9000000, 0x200004)
        scaled(mem, g)
        _triangles(mem, g, [(-95, 80, 0, 0, 0, 0xFFFFFF00), (95, 70, 0, 8, 0, 0xFFFFFF80), (0, -95, 0, 4, 8, 0xFFFFFFFF)])

    def combined(mem, g):
        load_texture(g, quadrant_texture(mem))
        g(0xFA000000, 0x3060C0A0)
        g(0xFB000000, 0xC0402060)
        g.othermode(FILTER, 0)                                     # dithered by the magic square
        g(*combine(1, 5, 4, 3, 1, 5, 4, 3))                        # (TEXEL0 - ENV) * SHADE + PRIM
        g(0xD7000002, 0xFFFFFFFF)
        g(0xD9000000, 0x200004)
        scaled(mem, g)
        _triangles(mem, g, [(-95, 80, 0, 0, 0, 0x204080FF), (95, 70, 0, 8, 0, 0xF0F0F0FF), (0, -95, 0, 4, 8, 0x80FF2080)])

    def antialiased(mem, g):
        g.zimage()
        g(0xFF100000 | (W - 1), 0x80000000 | ZB)
        g.fill(0xFFFC)
        g.frame()
        g(0xED000000, (W * 4) << 12 | H * 4)
        gradient(mem, g)
        g.othermode(NO_DITHER, 0x8 | 0x10 | 0x20 | 0x40)        # AA, z compare and update, image read
        g(*SHADE)
        g(0xD9000000, 0x200005)
        scaled(mem, g)
        a, b, c, d = ((-90, 70, 0, 0, 0, 0xFF2020FF), (85, 40, 0, 0, 0, 0x20FF20FF), (-30, -85, 0, 0, 0, 0x2020FFFF),
                      (70, -60, 0, 0, 0, 0xFFFF20FF))
        _triangles(mem, g, [a, b, c, b, d, c])

    def random_triangles(aa: bool):
        """Random triangles: slivers, near-flat edges, ones past the screen's edges, smooth-shaded."""
        def scene(mem, g):
            import random
            rnd = random.Random(1 if aa else 2)
            ortho = mem.put(matrix([1 / 640, 0, 0, 0, 0, -1 / 480, 0, 0, 0, 0, 1, 0, -1, 1, 0, 1]))
            g(0xDA380007, 0x80000000 | ortho)  # vertices in quarter pixels across and down the frame
            g(0xDA380003, 0x80000000 | mem.put(matrix()))
            g.othermode(NO_DITHER, 0x8 if aa else 0)
            g(*SHADE)
            g(0xD9000000, 0x200000)
            for _ in range(20):
                points = []
                for _ in range(10):
                    kind = rnd.random()
                    x, y = rnd.randrange(-200, 1480), rnd.randrange(-200, 1160)
                    if kind < 0.3:
                        a, b = (x + rnd.randrange(-3, 4), y + rnd.randrange(-600, 600)), (x + rnd.randrange(-2, 3), y + rnd.randrange(-600, 600))
                    elif kind < 0.5:
                        a, b = (x + rnd.randrange(-900, 900), y + rnd.randrange(-2, 3)), (x + rnd.randrange(-300, 300), y + rnd.randrange(-400, 400))
                    else:
                        a, b = (rnd.randrange(-400, 1680), rnd.randrange(-400, 1360)), (rnd.randrange(-400, 1680), rnd.randrange(-400, 1360))
                    points += [(x, y, 0, 0, 0, rnd.getrandbits(32) | 0xFF), (*a, 0, 0, 0, rnd.getrandbits(32) | 0xFF),
                               (*b, 0, 0, 0, rnd.getrandbits(32) | 0xFF)]
                _triangles(mem, g, points)
        return scene

    def two_cycle(mem, g):
        """Two cycles, as the RDP pipelines them: the second combiner cycle mixing in TEXEL1 (the next pixel's
        TEXEL0), alpha compare on a cut-out alpha (the next pixel's), the first blender cycle mixing with memory (the
        previous pixel's) and the second with its own, dithered."""
        gradient(mem, g)
        texels = b"".join((0xF801 if (s ^ t) & 2 else 0x07FE | ((s + t) & 1)).to_bytes(2, "big")
                          for t in range(8) for s in range(8))
        load_texture(g, mem.put(texels), tile=0, tmem=0)
        load_texture(g, quadrant_texture(mem), tile=1, tmem=64)
        g(0xFB000000, 0x8040C0A0)                                  # the environment: its alpha mixes TEXEL1 in
        g(0xF9000000, 0x00000060)                                  # the alpha compare's threshold
        g.othermode(1 << 20 | 1 << 6 | FILTER | PERSP, 0x4000 | 0x40 | 0x1 | 1 << 22 | 1 << 20)
        g(0xFC000000 | 1 << 20 | 4 << 15 | 1 << 12 | 4 << 9 | 2 << 5 | 12,
          15 << 28 | 0 << 24 | 7 << 21 | 7 << 18 | 7 << 15 | 7 << 12 | 7 << 9 | 0 << 6 | 7 << 3 | 0)
        g(0xD7000002, 0xFFFFFFFF)
        g(0xD9000000, 0x200004)
        g(0xDA380007, 0x80000000 | mem.put(matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -0.2, 0])))
        g(0xDA380003, 0x80000000 | mem.put(matrix([1.2, 0, 0, 0, 0, 0.9, 0.3, 0, 0, 0, 1, 0, 0.1, 0, -1.6, 1])))
        q = [(-1, 1, 0, 0, 0, 0xFFFFFFFF), (1, 1, 0, 8, 0, 0xC0FFFFC0), (1, -1, 0, 8, 8, 0xFFC0FF80),
             (-1, -1, 0, 0, 8, 0xFFFFC0FF)]
        _triangles(mem, g, [q[0], q[1], q[2], q[0], q[2], q[3]])

    def copy_scissor(mem, g):
        """Copy mode against a scissor narrower than the frame: the rectangle's span includes the scissor's
        right edge, and a rectangle wholly beyond it draws nothing."""
        gradient(mem, g)
        load_texture(g, quadrant_texture(mem))
        g(0xED000000, (200 * 4) << 12 | 180 * 4)
        g.othermode(NO_DITHER | 2 << 20 | 6 << 9, 0)
        for x0, x1, y in ((150, 260, 20), (210, 300, 60), (190, 199, 100)):
            g(0xE4000000 | (x1 * 4) << 12 | (y + 30) * 4, 0 << 24 | (x0 * 4) << 12 | y * 4)
            g(0xE1000000, 0)
            g(0xF1000000, 0x10000400)

    def depth_slopes(mem, g):
        """Depth that barely changes across the triangles (slopes of a fraction of a unit, negative ones too): the
        RDP's delta z from those slopes' integer parts, and the blender's delta-z shifts from it."""
        g.zimage()
        g(0xFF100000 | (W - 1), 0x80000000 | ZB)
        g.fill(0xFFFC)
        g.frame()
        g(0xED000000, (W * 4) << 12 | H * 4)
        gradient(mem, g)
        g.othermode(NO_DITHER, 0x4000 | 0x8 | 0x10 | 0x20 | 0x40 | 1 << 22 | 1 << 18)  # force blend, AA, z: IN * IN_A + MEM * MEM_A
        g(*SHADE)
        g(0xD9000000, 0x200005)
        scaled(mem, g)
        _triangles(mem, g, [(-90, 70, 1, 0, 0, 0xFF2020C0), (85, 40, 0, 0, 0, 0x20FF20C0), (-30, -85, 1, 0, 0, 0x2020FFC0),
                            (-80, -70, 0, 0, 0, 0xFFFF20C0), (90, -30, 1, 0, 0, 0x20FFFFC0), (10, 90, 0, 0, 0, 0xFF20FFC0)])

    def s2dex(mem, g):
        """S2DEX2's objects, as RDP commands: a background copied and one scaled (both wrapping round their image),
        a palette and a 4-bit texture loaded by tile and drawn as a flipped, scaled rectangle, and an RGBA texture
        loaded as a block and drawn as a sprite turned by the 2D matrix."""
        gradient(mem, g)
        g(0xE1000000, 0x80000000 | ucode_data(mem, "S2DEX2"))
        g(0xDD000FFF, 0)
        image = mem.put(b"".join(((x * 7 + y * 3) % 31 << 11 | (x * 2) % 31 << 6 | (y * 3) % 31 << 1 | 1).to_bytes(2, "big")
                                 for y in range(24) for x in range(40)))
        g.othermode(NO_DITHER | 2 << 20, 0)
        g(0x0A000000, 0x80000000 | _obj_bg(mem, image, 40, 24, (8, 6, 64, 40), at=(13, 5)))      # G_OBJ_BGRECTCOPY
        g.othermode(NO_DITHER | 6 << 9, 0)                       # G_TC_FILT: no YUV conversion
        g(*TEX)
        g(0x09000000, 0x80000000 | _obj_bg(mem, image, 40, 24, (90, 8, 120, 90), at=(7.5, 3), scale=(0.75, 0.6)))
        palette = mem.put(b"".join(((i * 2) << 11 | (31 - i * 2) << 6 | (i * 5 % 31) << 1 | (i > 0)).to_bytes(2, "big")
                                   for i in range(16)))
        ci4 = mem.put(bytes(((x + y) % 16) << 4 | ((x * 3 + y) % 16) for y in range(8) for x in range(8)))
        g.othermode(NO_DITHER | 6 << 9 | 2 << 14, 0)               # G_TT_RGBA16
        g(0x05000000, 0x80000000 | _obj_tlut(mem, palette, 16))
        g(0x05000000, 0x80000000 | _obj_tile(mem, ci4, 0, 1, 8))
        g(0x01000000, 0x80000000 | _obj_sprite(mem, 20.5, 120.25, 3, 16, 8, flags=1, fmt=2, siz=0, stride=1))
        g.othermode(NO_DITHER | 6 << 9, 0)
        g(0x05000000, 0x80000000 | _obj_block(mem, quadrant_texture(mem), 8, 8))
        g(0xDC170000, 0x80000000 | mem.put(struct.pack(">iiiihhHH", 56756, -32768, 32768, 56756, 250 * 4, 150 * 4,
                                                         1024, 1024)))
        g(0x02000000, 0x80000000 | _obj_sprite(mem, -20, -20, 5, 8, 8))

    def yuv(two: bool):
        """A 16x16 YUV image (U Y V Y pairs) through the texture convert stage, tilted so the filter's fractions
        vary: one cycle converting each texel (G_TC_CONV) and the combiner finishing with K4 and K5
        (G_CC_1CYUV2RGB), or two, the first filtering and the second converting its result (G_TC_FILTCONV)."""
        def scene(mem, g):
            data = bytearray()
            for t in range(16):
                for p in range(8):
                    y0, y1 = 40 + t * 7 + p * 10, 45 + t * 7 + p * 10 + (t & 3) * 3   # video's range, 16 to 235
                    data += bytes([70 + p * 14 + t * 2, y0, 190 - t * 7 + p * 3, y1])
            image = mem.put(bytes(data))
            g(0xFD000000 | 1 << 21 | 2 << 19 | 15, 0x80000000 | image)               # YUV, 16-bit, 16 wide
            g(0xF5000000 | 1 << 21 | 2 << 19 | 2 << 9, 7 << 24)                      # the load tile
            g(0xF4000000, 7 << 24 | (15 * 4) << 12 | 15 * 4)                         # LoadTile
            g(0xF5000000 | 1 << 21 | 2 << 19 | 2 << 9, 4 << 14 | 4 << 4)            # tile 0: 16x16, wrapped
            g(0xF2000000, (15 * 4) << 12 | 15 * 4)
            k = [175, -43, -89, 222, 114, 42]                                        # libultra's G_CV_K0 to K5
            k = [v & 0x1FF for v in k]
            g(0xEC000000 | k[0] << 13 | k[1] << 4 | k[2] >> 5, (k[2] & 31) << 27 | k[3] << 18 | k[4] << 9 | k[5])
            g(0xDA380007, 0x80000000 | mem.put(matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -0.2, 0])))
            g(0xDA380003, 0x80000000 | mem.put(matrix([1.2, 0, 0, 0, 0, 0.9, 0.3, 0, 0, 0, 1, 0, 0.1, 0, -1.6, 1])))
            if two:
                g.othermode(NO_DITHER | 1 << 20 | 5 << 9 | 2 << 12 | PERSP, 0)
                # cycle 0: (TEXEL1 - K4) * K5 + TEXEL1 (TEXEL1 the converted texel); cycle 1: the combined colour
                g(0xFC000000 | 2 << 20 | 15 << 15 | 7 << 12 | 7 << 9 | 15 << 5 | 31,
                  7 << 28 | 15 << 24 | 7 << 21 | 7 << 18 | 2 << 15 | 7 << 12 | 4 << 9 | 0 << 6 | 7 << 3 | 0)
            else:
                g.othermode(NO_DITHER | 0 << 9 | 2 << 12 | PERSP, 0)
                g(*combine(1, 7, 15, 1, 7, 7, 7, 4))                                  # (TEXEL0 - K4) * K5 + TEXEL0
            g(0xD7000002, 0xFFFFFFFF)
            g(0xD9000000, 0x200004)
            white = 0xFFFFFFFF
            q = [(-1, 1, 0, 0, 0, white), (1, 1, 0, 20, 0, white), (1, -1, 0, 20, 18, white), (-1, -1, 0, 0, 18, white)]
            _triangles(mem, g, [q[0], q[1], q[2], q[0], q[2], q[3]])
        return scene

    return {"yuv": yuv(False), "yuv-filtconv": yuv(True), "random": random_triangles(False), "random-aa": random_triangles(True), "shade": shade, "flat": textured(False, False, False), "flat-persp": textured(True, False, False),
            "flat-bilerp": textured(False, True, False), "tilted-point": textured(True, False, True),
            "tilted-bilerp": textured(True, True, True), "depth": depth, "rect": rect, "blend": blend, "fog": fog,
            "combine": combined, "antialiased": antialiased, "two-cycle": two_cycle, "copy-scissor": copy_scissor,
            "depth-slopes": depth_slopes, "s2dex": s2dex}


SCENES = _scenes()
# What a scene may still differ by, in percent of its pixels (any difference, and more than one step): none now.
TOLERANCE: dict = {}


@pytest.fixture(scope="module")
def angrylion(tmp_path_factory):
    checkout = os.environ.get("N64_ANGRYLION")
    if not checkout:
        pytest.skip("N64_ANGRYLION (a checkout of angrylion-rdp-plus) not set")
    spec = importlib.util.spec_from_file_location("n64_rdp_compare", Path(__file__).parents[1] / "tools" / "n64_rdp_compare.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool, tool.build(Path(checkout), tmp_path_factory.mktemp("angrylion"))


@pytest.mark.parametrize("name", sorted(SCENES))
def test_scene_matches_angrylion(renderer, angrylion, tmp_path, name):
    tool, replay = angrylion
    mem = Memory()
    g = Gfx(mem)
    _start(mem, g)
    SCENES[name](mem, g)
    g(0xDF000000)
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"] = str(dumps), "1"
    try:
        run(renderer, mem, tmp_path, g.end())
    finally:
        del os.environ["PORT_RDPDUMP"], os.environ["PORT_RDPDUMP_TASKS"]
    import subprocess
    theirs = tmp_path / "angrylion.bin"
    subprocess.run([str(replay), str(dumps / "000001.before"), str(dumps / "000001.cmds"), str(theirs),
                    str(dumps / "000001.hidden")], check=True,
                   timeout=120)
    ours_mem, theirs_mem = (dumps / "000001.after").read_bytes(), theirs.read_bytes()
    for addr, width, siz, lines in tool.images((dumps / "000001.cmds").read_bytes()):
        r = tool.compare(tool.rgb(ours_mem, addr, width, siz, lines), tool.rgb(theirs_mem, addr, width, siz, lines))
        differ, beyond = TOLERANCE.get(name, (0.0, 0.0))
        assert r["differ_pct"] <= differ and r["beyond_pct"] <= beyond, (name, hex(addr), r)
    assert FB  # the frame the scenes draw to
