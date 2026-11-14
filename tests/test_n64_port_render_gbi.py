"""The N64 WebAssembly port's renderer (consoles/n64/port_wasm/render.c) on display lists built in Python
(tests/n64_render_kit.py): what each microcode's commands draw, and the RDP's behaviours games rely on."""

from __future__ import annotations

import pytest

from tests.n64_render_kit import (FB, SHADE, H, W, Gfx, Memory, build, light, matrix, normal, run, vertex)


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
