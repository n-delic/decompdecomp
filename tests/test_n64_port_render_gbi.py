"""The N64 WebAssembly port's renderer (consoles/n64/port_wasm/render.c) on display lists built in Python
(tests/n64_render_kit.py): what each microcode's commands draw, and the RDP's behaviours games rely on."""

from __future__ import annotations

import struct

import pytest

from tests.n64_render_kit import (
    FB,
    QUADRANT_COLOURS,
    SHADE,
    TEX,
    ZB,
    Gfx,
    H,
    Memory,
    W,
    build,
    combine,
    light,
    load_texture,
    matrix,
    normal,
    quadrant_texture,
    run,
    ucode_data,
    vertex,
    vi_mode,
)


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    program = build(tmp_path_factory.mktemp("render"))
    if not program:
        pytest.skip("no C compiler")
    return program


def _screen_quad(mem: Memory, colours: list[int], z: int = 0) -> int:
    """Four vertices covering the screen (clip space, identity matrices): top left, top right, bottom right,
    bottom left."""
    return mem.put(b"".join(vertex(x, y, z, s, t, c) for (x, y, s, t), c in
                            zip([(-1, 1, 0, 0), (1, 1, 8, 0), (1, -1, 8, 8), (-1, -1, 0, 8)], colours)))


def _f3dex2_start(mem: Memory, g: Gfx) -> None:
    ident = mem.put(matrix())
    g.frame().fill(0)
    g(0xDA380007, 0x80000000 | ident)  # projection: load
    g(0xDA380003, 0x80000000 | ident)  # model view: load


@pytest.mark.parametrize("ucode", ["F3DEX2", "S2DEX2"])
def test_a_tasks_partial_mode_update_uses_fresh_dither_defaults(renderer, tmp_path, ucode):
    mem = Memory()
    scene = Gfx(mem)
    scene.frame().fill(0x07C1)
    scene.othermode(0, 0)  # the preceding task leaves alpha dither enabled
    scene(0xDF000000)
    hud = Gfx(mem, ucode, at=0x2000, task=0x180)
    hud(0xE2001E01, 1)  # the HUD sets alpha compare without setting the dither bits
    hud(0xF9000000, 1)
    hud(*TEX)
    texels = mem.put(struct.pack(">64H", *([0x0000, 0xF801] * 32)))
    load_texture(hud, texels)
    hud(0xE4020020, 0)(0xE1000000, 0)(0xF1000000, 0x04000400)
    hud(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, scene.end(), hud.end())
    assert all(out.pixel(x, y) == ((31, 0, 0) if x % 2 else (0, 31, 0))
               for y in range(8) for x in range(8))


def test_f3dex2_lights_land_in_their_own_slots(renderer, tmp_path):
    """gSPSetLights1: LIGHT_1 (red, toward the viewer) at G_MOVEMEM offset 48, the ambient (dark blue) at 72, one
    light. A face toward the light is red plus the ambient; a face away from it only the ambient. The renderer once
    read LIGHT_1 from offset 24 (the look-at Y), so the red light became the ambient and lit every face alike."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(6 << 9, 0)
    g(*SHADE)
    g(0xD9000000, 0x00020000 | 0x200000)                    # G_LIGHTING, smooth: only these
    g(0xDB020000, 24)                                       # G_MW_NUMLIGHT: one light
    sun, ambient = mem.put(light(255, 0, 0, 0, 0, 127)), mem.put(light(0, 0, 64))
    g(0xDC080000 | (48 // 8) << 8 | 10, 0x80000000 | sun)   # LIGHT_1
    g(0xDC080000 | (72 // 8) << 8 | 10, 0x80000000 | ambient)  # LIGHT_2: the ambient
    toward, away = normal(0, 0, 127), normal(0, 0, -127)
    vtx = mem.put(b"".join(vertex(x, y, 0, rgba=n) for x, y, n in
                           [(-1, 1, toward), (0, 1, toward), (0, -1, toward), (-1, -1, toward),
                            (0, 1, away), (1, 1, away), (1, -1, away), (0, -1, away)]))
    g(0x01000000 | 8 << 12 | 8 << 1, 0x80000000 | vtx)
    g(0x06000204, 0x00000406)                               # the left half: toward the light
    g(0x06080A0C, 0x00080C0E)                               # the right half: away from it
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    lit, unlit = out.pixel(W // 4, H // 2), out.pixel(3 * W // 4, H // 2)
    assert lit[0] == 31 and lit[1] == 0 and 6 <= lit[2] <= 9, lit
    assert unlit[0] == 0 and unlit[1] == 0 and 6 <= unlit[2] <= 9, unlit

RED, GREEN, BLUE, WHITE = QUADRANT_COLOURS


def _gbi1_start(mem: Memory, g: Gfx) -> None:
    ident = mem.put(matrix())
    g.frame().fill(0)
    g(0x01030040, 0x80000000 | ident)  # G_MTX: projection, load
    g(0x01020040, 0x80000000 | ident)  # model view, load
    g.othermode(6 << 9, 0)
    g(*SHADE)


def test_fast3d_draws_with_its_own_opcodes(renderer, tmp_path):
    """Fast3D (the earliest GBI, signed "RSP SW Version"): G_VTX with the count and first index in nibbles,
    G_TRI1 with indices counted by ten, G_ENDDL at 0xB8. Read as F3DEX2, its 0x04 was a branch and nothing drew."""
    mem = Memory()
    g = Gfx(mem, "Fast3D")
    _gbi1_start(mem, g)
    vtx = _screen_quad(mem, [0xFF0000FF] * 4)
    g(0x04000000 | 3 << 20 | 0 << 16 | 64, 0x80000000 | vtx)
    g(0xBF000000, 0 << 16 | 10 << 8 | 20)
    g(0xBF000000, 0 << 16 | 20 << 8 | 30)
    g(0xB8000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 2, H // 2) == (31, 0, 0)
    assert out.pixel(10, H - 10) == (31, 0, 0)


def test_f3dex_1x_draws_triangle_pairs_and_quads_and_its_lights(renderer, tmp_path):
    """F3DEX 1.x: G_VTX with a doubled first index, G_TRI2 (0xB1), G_QUAD (0xB5), lights through G_MOVEMEM's
    index (G_MV_L0 0x86 the light, 0x88 the ambient) and GBI 1's G_MW_NUMLIGHT ((n + 1) * 32 + 0x80000000).
    The left half (G_TRI2, unlit) is green, the right half (G_QUAD, lit by red toward it) red plus the ambient."""
    mem = Memory()
    g = Gfx(mem, "F3DEX")
    _gbi1_start(mem, g)
    g(0xB6000000, 0xFFFFFFFF)                               # every geometry bit off
    g(0xB7000000, 0x200)                                    # GBI 1's G_SHADING_SMOOTH
    vtx = mem.put(b"".join(vertex(x, y, 0, rgba=0x00FF00FF) for x, y in [(-1, 1), (0, 1), (0, -1), (-1, -1)]))
    g(0x04000000 | 0 << 16 | 4 << 10 | (4 * 16 - 1), 0x80000000 | vtx)
    g(0xB1000000 | 0 << 16 | 2 << 8 | 4, 0 << 16 | 4 << 8 | 6)
    g(0xBC000002, 0x80000000 + 2 * 32)                      # G_MW_NUMLIGHT: one light
    sun, ambient = mem.put(light(255, 0, 0, 0, 0, 127)), mem.put(light(0, 0, 64))
    g(0x03860010, 0x80000000 | sun)
    g(0x03880010, 0x80000000 | ambient)
    g(0xB7000000, 0x20000)                                  # G_LIGHTING
    toward = normal(0, 0, 127)
    lit = mem.put(b"".join(vertex(x, y, 0, rgba=toward) for x, y in [(0, 1), (1, 1), (1, -1), (0, -1)]))
    g(0x04000000 | 8 << 16 | 4 << 10 | (4 * 16 - 1), 0x80000000 | lit)   # into slots 4 to 7
    g(0xB5000000, 8 << 24 | 10 << 16 | 12 << 8 | 14)
    g(0xB8000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 4, H // 2) == (0, 31, 0)
    red, green, blue = out.pixel(3 * W // 4, H // 2)
    assert red == 31 and green == 0 and 6 <= blue <= 9, (red, green, blue)


def _sprite(mem: Memory, x: int, y: int, scale: int) -> int:
    """A uObjSprite of the 8x8 RGBA16 texture at TMEM 0: its place (s10.2), scale (u5.10: 1024 / scale)."""
    return mem.put(struct.pack(">hHHhhHHhHHBBBB", x * 4, 1024 // scale, 8 << 5, 0, y * 4, 1024 // scale, 8 << 5, 0,
                               2, 0, 0, 2, 0, 0))


def _obj_matrix(mem: Memory) -> int:
    """A uObjMtx: A = D = 1 (s15.16), B = C = 0, X = Y = 0, base scales 1."""
    return mem.put(struct.pack(">iiiihhHH", 65536, 0, 0, 65536, 0, 0, 1024, 1024))


def test_s2dex_1x_draws_objects_with_its_own_opcodes(renderer, tmp_path):
    """S2DEX 1.x numbers its object commands differently from S2DEX2: G_OBJ_MOVEMEM 0x05 (the matrix by its
    length, 23), G_OBJ_SPRITE 0x04, G_ENDDL 0xB8. The sprite, four times larger at (100, 50), shows each quadrant."""
    mem = Memory()
    g = Gfx(mem, "S2DEX")
    g.frame().fill(0)
    g.othermode(6 << 9, 0)
    g(*TEX)
    load_texture(g, quadrant_texture(mem))
    g(0x05170000, 0x80000000 | _obj_matrix(mem))
    g(0x04000000, 0x80000000 | _sprite(mem, 100, 50, 4))
    g(0xB8000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    at = [(108, 58), (124, 58), (108, 74), (124, 74)]
    assert [out.r16(FB + (y * W + x) * 2) for x, y in at] == QUADRANT_COLOURS


def test_a_list_switches_microcode_with_g_load_ucode(renderer, tmp_path):
    """G_LOAD_UCODE (its data's address in G_RDPHALF_1) changes the microcode in the middle of a list, as a game
    draws a 2D background with S2DEX inside its 3D frame: F3DEX2, then S2DEX2's sprite, then F3DEX2 again for a
    red triangle. Chosen once per task, the S2DEX2 commands were read as F3DEX2's."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(6 << 9, 0)
    g(*TEX)
    load_texture(g, quadrant_texture(mem))
    g(0xE1000000, 0x80000000 | ucode_data(mem, "S2DEX2"))
    g(0xDD000FFF, 0)
    g(0xDC170000, 0x80000000 | _obj_matrix(mem))              # S2DEX2's G_OBJ_MOVEMEM, the matrix
    g(0x02000000, 0x80000000 | _sprite(mem, 100, 50, 4))      # S2DEX2's G_OBJ_SPRITE
    g(0xE1000000, 0x80000000 | ucode_data(mem, "F3DZEX"))
    g(0xDD000FFF, 0)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    vtx = mem.put(b"".join(vertex(x, y, 0, rgba=0xFF0000FF) for x, y in [(0, 0), (1, 0), (1, -1)]))
    g(0x01000000 | 3 << 12 | 3 << 1, 0x80000000 | vtx)
    g(0x05000204)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (58 * W + 108) * 2) == RED and out.r16(FB + (74 * W + 124) * 2) == WHITE
    assert out.pixel(300, 130) == (31, 0, 0)


@pytest.mark.parametrize("zval, near", [(600, True), (400, False)])
def test_branch_z_draws_the_near_model_when_the_vertex_is_near(renderer, tmp_path, zval, near):
    """G_BRANCH_Z (gSPBranchLessZ): the list in G_RDPHALF_1 replaces the rest when the vertex's screen depth (here
    511 of 1023) is below zval: the near model (green) for a far threshold, the far model (red) for a near one.
    Without it every model was drawn at its far detail."""
    mem = Memory()
    near_dl = 0x3000
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(6 << 9, 0)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    green, red = _screen_quad(mem, [0x00FF00FF] * 4), _screen_quad(mem, [0xFF0000FF] * 4)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | green)
    g(0xE1000000, 0x80000000 | near_dl)
    g(0x04000000 | (0 * 5) << 12 | 0 * 2, zval << 16)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | red)
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    n = Gfx(mem, at=near_dl, task=0x180)
    n(0x06000204, 0x00000406)
    n(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 2, H // 2) == ((0, 31, 0) if near else (31, 0, 0))


def test_g_mw_matrix_rewrites_one_element_of_the_combined_matrix(renderer, tmp_path):
    """G_MW_MATRIX writes the combined matrix's halves directly: x's scale made 0.5 (integer half 0, fraction
    0x8000), the screen-wide quad covers only the middle half."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(6 << 9, 0)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    g(0xDB000000 | 0, 0x00000000)          # the integer halves of m00 and m01: both 0
    g(0xDB000000 | 32, 0x80000000)         # their fractions: 0.5 and 0
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF0000FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 2, H // 2) == (31, 0, 0)
    assert out.pixel(20, H // 2) == (0, 0, 0)


# ---------------------------------------------------------------- the RDP

NO_DITHER = 3 << 6 | 6 << 9  # other mode H: G_CD_DISABLE, and G_TC_FILT as games set it (0 is YUV convert)


def _texrect(g: Gfx, tile: int, x0: int, y0: int, x1: int, y1: int, s: float = 0, t: float = 0,
             dsdx: float = 1, dtdy: float = 1) -> None:
    """G_TEXRECT and its two halves: corners in 10.2, s and t in s10.5, steps in s5.10."""
    g(0xE4000000 | (x1 * 4) << 12 | y1 * 4, tile << 24 | (x0 * 4) << 12 | y0 * 4)
    g(0xE1000000, (int(s * 32) & 0xFFFF) << 16 | (int(t * 32) & 0xFFFF))
    g(0xF1000000, (int(dsdx * 1024) & 0xFFFF) << 16 | (int(dtdy * 1024) & 0xFFFF))


def _solid_texture(mem: Memory, colour: int, size: int = 8) -> int:
    return mem.put(colour.to_bytes(2, "big") * size * size)


def test_a_pixel_on_a_shared_edge_is_drawn_once(renderer, tmp_path):
    """A half-transparent square (x scaled by 0.75: 40 to 280 by 0 to 240) over black: the pixels whose centres lie
    exactly on its diagonal (x = y + 40) belong to one of its triangles, so they're as red as the rest. Drawn by
    both, they were blended twice: a bright seam."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g(0xDA380003, 0x80000000 | mem.put(matrix([0.75, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])))
    g.othermode(NO_DITHER, 0x4000 | 1 << 22 | 1 << 20)       # force blend: IN * IN_A + MEM * (1 - IN_A)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF000080] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    inside = out.pixel(100, 150)
    assert 14 <= inside[0] <= 17, inside
    for y in (3, 60, 200):
        assert out.pixel(y + 40, y) == inside, y


def test_a_clamped_tile_mirrors_inside_its_mask(renderer, tmp_path):
    """A tile 16 texels wide, clamped and mirrored with an 8-texel mask: texels 8 to 15 are the first 8 mirrored
    (texel 12 shows texel 3, red; texel 9 texel 6, green). Taking clamp as "clamp only" read them raw from TMEM."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER, 0)
    g(*TEX)
    load_texture(g, quadrant_texture(mem))
    g(0xF5100000 | 2 << 9, 0 << 24 | 3 << 18 | 3 << 14 | 3 << 8 | 3 << 4)  # mirror + clamp, masks 3 (8 texels)
    g(0xF2000000, 0 << 24 | (15 * 4) << 12 | 7 * 4)
    _texrect(g, 0, 0, 0, 16, 4)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (2 * W + 12) * 2) == RED
    assert out.r16(FB + (2 * W + 9) * 2) == GREEN


def _two_tiles(mem: Memory, g: Gfx) -> None:
    """Tile 0: the quadrant texture; tile 1: solid blue, 16 words further in TMEM."""
    load_texture(g, quadrant_texture(mem), tile=0, tmem=0)
    load_texture(g, _solid_texture(mem, BLUE), tile=1, tmem=16)


def test_texel1_is_the_next_tiles_texel(renderer, tmp_path):
    """The combiner's TEXEL1 reads G_TEXTURE's tile + 1 (two textures on one surface: water's two layers, flames).
    The renderer once gave TEXEL0 for it."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER, 0)
    g(*combine(15, 15, 31, 2, 7, 7, 7, 2))
    _two_tiles(mem, g)
    _texrect(g, 0, 0, 0, 8, 8)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (2 * W + 2) * 2) == BLUE


@pytest.mark.parametrize("lod, colour", [(True, BLUE), (False, RED)])
def test_the_level_of_detail_picks_the_mip_level(renderer, tmp_path, lod, colour):
    """With G_TL_LOD, a rectangle stepping two texels a pixel draws from the next level (tile 1, blue), G_TEXTURE
    giving one level beyond the first; without it, from tile 0 (red at the top left)."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER | (0x10000 if lod else 0), 0)
    g(0xD7000002 | 1 << 11, 0xFFFFFFFF)                      # G_TEXTURE: one more level
    g(*TEX)
    _two_tiles(mem, g)
    _texrect(g, 0, 0, 0, 4, 4, dsdx=2, dtdy=2)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (1 * W + 1) * 2) == colour


def _depth_frame(g: Gfx, z_fill: int) -> None:
    """The z image filled with `z_fill` (drawn into as a colour image), then the frame black."""
    g.frame(ZB).fill(z_fill)
    g.frame().zimage().fill(0)


def test_depth_is_kept_in_the_games_z_image(renderer, tmp_path):
    """A triangle drawn with Z_UPD leaves its depth in the z image in RDRAM, in the RDP's format: depth 511 of 1023
    is 0x1FF00 in 18 bits, exponent 0 and mantissa 0x7FC. The renderer once kept depth in a buffer of its own."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    _depth_frame(g, 0xFFFC)
    g.othermode(NO_DITHER, 0x10 | 0x20)                       # Z_CMP, Z_UPD
    g(*SHADE)
    g(0xD9000000, 0x200000 | 1)                               # G_ZBUFFER
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF0000FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    word = out.r16(ZB + (100 * W + 100) * 2)
    assert word >> 13 == 0 and (word >> 2) & 0x7FF == 0x7FC, hex(word)
    assert out.pixel(100, 100) == (31, 0, 0)


def test_a_depth_the_game_wrote_hides_what_is_behind_it(renderer, tmp_path):
    """The game draws into its z image itself (a lens's circle, a prerendered room's depth): here filled with the
    nearest depth, the triangle behind it doesn't draw. Fill-mode clears were all the renderer's depth once saw."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    _depth_frame(g, 0x0000)
    g.othermode(NO_DITHER, 0x10 | 0x20)
    g(*SHADE)
    g(0xD9000000, 0x200000 | 1)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF0000FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(100, 100) == (0, 0, 0)


@pytest.mark.parametrize("prim_z, drawn", [(0x0000, True), (0x7FFF, False)])
def test_a_rectangle_takes_the_primitives_depth(renderer, tmp_path, prim_z, drawn):
    """G_ZS_PRIM with G_SETPRIMDEPTH: a 2D rectangle compared at the primitive's depth against a z image half way
    (0x1FF00): in front, it draws; behind, it doesn't. Rectangles were never depth-tested."""
    mem = Memory()
    g = Gfx(mem)
    _depth_frame(g, 0x7FC << 2)
    g.othermode(NO_DITHER, 0x10 | 4)                          # Z_CMP, G_ZS_PRIM
    g(*TEX)
    g(0xEE000000, prim_z << 16)
    load_texture(g, quadrant_texture(mem))
    _texrect(g, 0, 0, 0, 4, 4)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert (out.r16(FB + (1 * W + 1) * 2) == RED) == drawn


@pytest.mark.parametrize("z, drawn", [(0, True), (-1, False)])
def test_a_decal_draws_only_on_a_surface_at_its_depth(renderer, tmp_path, z, drawn):
    """ZMODE_DEC (a shadow or a mark on a wall): after a green surface at depth 511, a red decal at the same depth
    draws over it, one nearer (z = -1, depth 0) doesn't. Without decal mode it passed wherever it was in front."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    _depth_frame(g, 0xFFFC)
    g(*SHADE)
    g(0xD9000000, 0x200000 | 1)
    g.othermode(NO_DITHER, 0x10 | 0x20)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0x00FF00FF] * 4))
    g(0x06000204, 0x00000406)
    g.othermode(NO_DITHER, 0x10 | 3 << 10)                    # Z_CMP, ZMODE_DEC
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF0000FF] * 4, z=z))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(100, 100) == ((31, 0, 0) if drawn else (0, 31, 0))


def test_texture_generation_follows_the_normal(renderer, tmp_path):
    """G_TEXTURE_GEN (chrome, metal): s and t from the normal along the look-at X and Y axes, (d + 1) / 2 of
    G_TEXTURE's scale >> 6 texels. A normal toward -X takes s = 0 (red, the left of the top row), toward +X s = 7
    (green). The vertex's own s and t (here 0) were used before."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(NO_DITHER | 0x80000, 0)                       # perspective correction
    g(*TEX)
    load_texture(g, quadrant_texture(mem))
    g(0xD7000002, 0x01C001C0)                                 # G_TEXTURE: scale 0x1C0, 7 texels across
    g(0xDC080000 | 0 << 8 | 10, 0x80000000 | mem.put(light(0, 0, 0, 127, 0, 0)))   # look-at X
    g(0xDC080000 | 3 << 8 | 10, 0x80000000 | mem.put(light(0, 0, 0, 0, 127, 0)))   # look-at Y
    g(0xDB020000, 0)                                          # no lights
    g(0xD9000000, 0x20000 | 0x40000 | 0x200000)               # G_LIGHTING, G_TEXTURE_GEN, smooth
    left, right = normal(-127, 0, 0), normal(127, 0, 0)
    vtx = mem.put(b"".join(vertex(x, y, 0, rgba=n) for x, y, n in
                           [(-1, 1, left), (0, 1, left), (0, -1, left), (-1, -1, left),
                            (0, 1, right), (1, 1, right), (1, -1, right), (0, -1, right)]))
    g(0x01000000 | 8 << 12 | 8 << 1, 0x80000000 | vtx)
    g(0x06000204, 0x00000406)
    g(0x06080A0C, 0x00080C0E)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (120 * W + 80) * 2) == RED
    assert out.r16(FB + (120 * W + 240) * 2) == GREEN


@pytest.mark.parametrize("dither, first_red, second_red", [(0, 17, 16), (NO_DITHER, 16, 16)])
def test_colours_are_dithered_as_they_are_cut_to_5_bits(renderer, tmp_path, dither, first_red, second_red):
    """The RDP cuts each 8-bit channel to 5 bits rounding up when the 3 bits cut off are above the dither value (the
    magic square by default, 0 at the first pixel and 6 at the second): a flat 132 (16.5 in 5 bits, 4 cut off)
    comes out 17 then 16; with G_CD_DISABLE it never rounds up. Adding the dither value and truncating, as this did,
    gave the mirror image: 16 then 17."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(dither, 0)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0x848484FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(0, 0)[0] == first_red
    assert out.pixel(1, 0)[0] == second_red


def test_alpha_times_coverage_drops_a_cut_outs_clear_texels(renderer, tmp_path):
    """CVG_X_ALPHA with anti-aliasing (a texture's cut-out edge: leaves, fences): coverage times alpha, so a clear
    texel covers nothing and isn't drawn, while an opaque one is. It was drawn whole before."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER, 0x8 | 0x1000)                      # AA_EN, CVG_X_ALPHA
    g(*TEX)
    clear_left = b"".join((RED if s >= 4 else RED & ~1).to_bytes(2, "big") for _ in range(8) for s in range(8))
    load_texture(g, mem.put(clear_left))
    _texrect(g, 0, 0, 0, 8, 8)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (2 * W + 1) * 2) == 0
    assert out.r16(FB + (2 * W + 6) * 2) == RED


def test_an_ia16_palette_reads_intensity_and_alpha(renderer, tmp_path):
    """G_TT_IA16 (other mode H): a colour-indexed texture's palette entries are intensity and alpha, not RGBA 5551.
    Entry 0x80FF is a half grey; read as RGBA it was red with a little green and full blue."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER | 3 << 14, 0)
    g(*TEX)
    tlut = mem.put((0x80FF).to_bytes(2, "big") * 16)
    g(0xFD100000, 0x80000000 | tlut)
    g(0xF5000100, 7 << 24)
    g(0xF0000000, 7 << 24 | 15 << 14)
    image = mem.put(b"\x00" * 32)                              # CI4, 8x8, every texel entry 0
    g(0xFD000000 | 2 << 21 | 7, 0x80000000 | image)
    g(0xF5000000 | 2 << 21 | 1 << 9, 0)
    g(0xF4000000, (7 << 2) << 12 | 7 << 2)
    _texrect(g, 0, 0, 0, 8, 8)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    r, gr, b = out.pixel(2, 2)
    assert r == gr == b and 15 <= r <= 16, (r, gr, b)


def test_a_32_bit_colour_image_gets_32_bit_pixels(renderer, tmp_path):
    """G_SETCIMG's size 32: each pixel is RGBA 8888, its alpha byte the coverage the RDP keeps (full: 7, shifted up
    5, 0xE0). Drawn as 16-bit pixels, the image came out half its width and the wrong colours."""
    mem = Memory()
    g = Gfx(mem)
    ident = mem.put(matrix())
    g(0xFF180000 | (W - 1), 0x80000000 | FB)
    g.othermode(NO_DITHER, 0)
    g(0xDA380007, 0x80000000 | ident)
    g(0xDA380003, 0x80000000 | ident)
    g(*SHADE)
    g(0xD9000000, 0x200000)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0xFF0000FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    at = FB + (100 * W + 300) * 4
    assert bytes(out.ram[at:at + 4]) == b"\xff\x00\x00\xe0"


# ---------------------------------------------------------------- the VI


def test_the_vi_shows_the_frame_at_its_modes_size(renderer, tmp_path):
    """osViSetMode's mode gives the frame's size: a game drawing at 400 x 300 is shown at 400 x 300. The size was
    guessed from the width (wider than 320: 480 lines), so its frame was read past its end."""
    mem = Memory()
    g = Gfx(mem)
    g.frame(width=400)
    g(0xED000000, (400 * 4) << 12 | 300 * 4)                   # G_SETSCISSOR: the whole 400 x 300
    g.fill(0xF801, x1=399, y1=299)
    g(0xDF000000)
    mode = vi_mode(mem, 400, 300)
    _, (w, h, rgb) = run(renderer, mem, tmp_path, g.end(), f"mode:{mode:x}", f"show:{FB:x}")
    assert (w, h) == (400, 300)
    assert rgb[(299 * 400 + 399) * 3] == 255


def test_the_vis_gamma_brightens_as_the_console_does(renderer, tmp_path):
    """A mode with gamma on shows each channel through the VI's square-root curve: 66 of 255 comes out about 129.
    osViSetSpecialFeatures(OS_VI_GAMMA_OFF) turns it off again."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0x4210)                                     # 8 of 31 in each channel: 66 of 255
    g(0xDF000000)
    mode = vi_mode(mem, W, H, ctrl=0x2 | 0x8)
    _, (_, _, on) = run(renderer, mem, tmp_path, g.end(), f"mode:{mode:x}", f"show:{FB:x}")
    _, (_, _, off) = run(renderer, mem, tmp_path, g.end(), f"mode:{mode:x}", "features:2", f"show:{FB:x}")
    assert 125 <= on[0] <= 132, on[0]
    assert 64 <= off[0] <= 68, off[0]


def test_the_vis_dedither_gives_back_what_the_dither_cut(renderer, tmp_path):
    """The VI's dedither, on a frame of random 16-bit pixels, all fully covered: each channel's 5 bits widened, then
    moved by how many of its eight neighbours are above it less how many are below, as the filter's definition says.
    The renderer compares the three channels at once now (the browser build's costliest step was this filter, one
    channel and one neighbour at a time): this holds it to the definition."""
    import random

    def f32(v):
        return struct.unpack("<f", struct.pack("<f", v))[0]

    widened = [int(f32(f32(i / 31) * 255)) for i in range(32)]  # (uint8_t)(i / 31.0f * 255), as the VI reads it
    rnd = random.Random(7)
    mem = Memory()
    frame = [[rnd.randrange(0x10000) | 1 for _ in range(W)] for _ in range(H)]
    for y, row in enumerate(frame):
        for x, v in enumerate(row):
            mem.w16(FB + (y * W + x) * 2, v)
    _, (w, h, rgb) = run(renderer, mem, tmp_path, "features:62", f"show:{FB:x}")  # dedither on, gamma and divot off
    assert (w, h) == (W, H)
    for y in range(1, H - 1, 7):
        for x in range(1, W - 1, 5):
            for ch, shift in enumerate((11, 6, 1)):
                c = widened[(frame[y][x] >> shift) & 31] >> 3
                around = [widened[(frame[y + dy][x + dx] >> shift) & 31] >> 3
                          for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]
                out = (c << 3) + (c << 3 >> 5) + sum(n > c for n in around) - sum(n < c for n in around)
                assert rgb[(y * W + x) * 3 + ch] == max(0, min(255, out)), (x, y, ch)


def test_one_cycle_mode_runs_the_combiners_second_cycle(renderer, tmp_path):
    """In one cycle the RDP runs the second cycle's combiner settings (angrylion's renderer does, from the
    hardware): here the first cycle says the shade (green) and the second the primitive colour (red)."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(NO_DITHER, 0)
    shade_first = combine(15, 15, 31, 4, 7, 7, 7, 4)[1] & ~(7 << 6 | 7) | 3 << 6 | 3
    g(SHADE[0], shade_first)
    g(0xFA000000, 0xFF0000FF)                               # G_SETPRIMCOLOR: red
    g(0xD9000000, 0x200000)
    g(0x01000000 | 4 << 12 | 4 << 1, 0x80000000 | _screen_quad(mem, [0x00FF00FF] * 4))
    g(0x06000204, 0x00000406)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W // 2, H // 2) == (31, 0, 0)


@pytest.mark.parametrize("ucode", ["L3DEX2", "L3DEX", "Fast3D"])
def test_lines_are_drawn(renderer, tmp_path, ucode):
    """G_LINE3D (gSPLineW3D, width 2: 2.5 pixels): a horizontal red line through the middle and a vertical green
    one at a quarter of the width, from F3DEX2's line microcode (0x08), F3DEX 1.x's (0xB5, which F3DEX itself
    takes as G_QUAD) and Fast3D (0xB5, indices by ten). Lines weren't drawn; F3DEX2's 0x08 went to the RDP."""
    mem = Memory()
    g = Gfx(mem, ucode)
    if ucode == "L3DEX2":
        _f3dex2_start(mem, g)
    else:
        _gbi1_start(mem, g)
    g.othermode(NO_DITHER, 0)
    g(*SHADE)
    left = mem.put(b"".join(vertex(x, y, 0, rgba=0x00FF00FF) for x, y in [(0, 1), (0, -1)]))
    red = mem.put(b"".join(vertex(x, 0, 0, rgba=0xFF0000FF) for x in (-1, 1)))
    lines = [(red, 0), (left, 1)]
    for at, which in lines:
        if which:  # move the green line to x = -0.5
            shift = mem.put(matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -0.5, 0, 0, 1]))
            g(0xDA380003 if ucode == "L3DEX2" else 0x01020040, 0x80000000 | shift)
        if ucode == "L3DEX2":
            g(0x01000000 | 2 << 12 | 2 << 1, 0x80000000 | at)
            g(0x08000000 | 0 << 16 | 2 << 8 | 2)
        elif ucode == "L3DEX":
            g(0x04000000 | 0 << 16 | 2 << 10 | (2 * 16 - 1), 0x80000000 | at)
            g(0xB5000000, 0 << 16 | 2 << 8 | 2)
        else:
            g(0x04000000 | 1 << 20 | 0 << 16 | 32, 0x80000000 | at)
            g(0xB5000000, 0 << 16 | 10 << 8 | 2)
    g(0xDF000000 if ucode == "L3DEX2" else 0xB8000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(W * 3 // 4, H // 2) == (31, 0, 0)
    assert out.pixel(W * 3 // 4, H // 2 - 3) == (0, 0, 0)
    assert out.pixel(W // 4, H // 4) == (0, 31, 0)
    assert out.pixel(W // 4 + 3, H // 4) == (0, 0, 0)


def test_fill_mode_fills_the_scissors_right_edge_and_frames_taller_than_240_lines(renderer, tmp_path):
    """A PAL racing game's frames are 320 x 264 with its scissor ending at 319: the console fills column 319 (in
    fill mode the scissor's right edge is drawn, as in copy mode) and the lines below 240. The renderer left both
    unfilled (last frame's pixels showed there)."""
    mem = Memory()
    g = Gfx(mem)
    g.frame()
    g(0xED000000, (319 * 4) << 12 | 264 * 4)                 # the scissor: 0 to 319 by 0 to 264
    g.othermode(3 << 20, 0)
    g(0xF7000000, 0xF801F801)                                 # red
    g(0xF6000000 | (319 * 4) << 12 | 263 * 4, 0)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.r16(FB + (10 * W + 319) * 2) == 0xF801
    assert out.r16(FB + (255 * W + 100) * 2) == 0xF801


def test_a_32_bit_block_loaded_past_tmems_low_half_wraps_inside_its_halves(renderer, tmp_path):
    """A racing game loads its sky's second RGBA32 texture at TMEM 0x180 (byte 0xC00): red and green wrap into the
    low half, blue and alpha into the high, where the tile reads them. The load once stopped at the low half's end
    and loaded nothing, and the sky drew with the other texture's texels."""
    mem = Memory()
    g = Gfx(mem)
    g.frame().fill(0)
    g.othermode(NO_DITHER | 2 << 20, 0)                       # copy mode: the texel as it is
    image = mem.put((0x00FF00FF).to_bytes(4, "big") * 16)     # green, 4 x 4 RGBA32
    g(0xFD180000, 0x80000000 | image)
    g(0xF5180180, 7 << 24)
    g(0xF3000000, 7 << 24 | 15 << 12 | 0x200)                 # LoadBlock: 16 texels
    g(0xF5180000 | 1 << 9 | 0x180, 0)                         # tile 0: RGBA32, line 1, TMEM 0x180
    g(0xF2000000, (3 * 4) << 12 | 3 * 4)
    g.othermode(NO_DITHER, 0)
    g(*TEX)
    _texrect(g, 0, 0, 0, 4, 4)
    g(0xDF000000)
    out, _ = run(renderer, mem, tmp_path, g.end())
    assert out.pixel(1, 1) == (0, 31, 0)
