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
    Gfx,
    H,
    Memory,
    W,
    build,
    light,
    load_texture,
    matrix,
    normal,
    quadrant_texture,
    run,
    ucode_data,
    vertex,
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


def test_f3dex2_lights_land_in_their_own_slots(renderer, tmp_path):
    """gSPSetLights1: LIGHT_1 (red, toward the viewer) at G_MOVEMEM offset 48, the ambient (dark blue) at 72, one
    light. A face toward the light is red plus the ambient; a face away from it only the ambient. The renderer once
    read LIGHT_1 from offset 24 (the look-at Y), so the red light became the ambient and lit every face alike."""
    mem = Memory()
    g = Gfx(mem)
    _f3dex2_start(mem, g)
    g.othermode(0, 0)
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
    g.othermode(0, 0)
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
    g.othermode(0, 0)
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
    g.othermode(0, 0)
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
    g.othermode(0, 0)
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
    g.othermode(0, 0)
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
