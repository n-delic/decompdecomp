"""F3DDKR, Rare's Fast3D with its own geometry commands (consoles/n64/port_wasm/render.c, f3ddkr()): homemade display
lists, never a game's. The microcode carries no signature, so the renderer knows it by the CRC-32 of its 2 KB of
data; these tasks give it a block of data made to have that CRC.

A racing game's European release draws with it: read as F3DEX2 (the renderer's guess without a signature), its
lists ran into data and the scheduler waited for ever for an RDP that never finished."""

from __future__ import annotations

import struct
import zlib

import pytest

from tests.n64_render_kit import FB, SHADE, TEX, Gfx, H, Memory, W, build, matrix, run

F3DDKR_CRC = 0xC432D0EA
NO_DITHER = 3 << 6 | 6 << 9  # G_CD_DISABLE, G_TC_FILT
RED, GREEN, BLUE = (31, 0, 0), (0, 31, 0), (0, 0, 31)


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    program = build(tmp_path_factory.mktemp("render"))
    if not program:
        pytest.skip("no C compiler")
    return program


def _ucode_data(crc: int) -> bytes:
    """2 KB whose CRC-32 is `crc` (its last word chosen so: the CRC run back over it)."""
    data = bytearray(b"homemade microcode data, not a game's" + bytes(0x800 - 37))
    state = zlib.crc32(bytes(data[:-4])) ^ 0xFFFFFFFF
    want = crc ^ 0xFFFFFFFF
    for _ in range(32):
        want = ((want ^ 0xEDB88320) << 1 | 1) & 0xFFFFFFFF if want & 0x80000000 else want << 1 & 0xFFFFFFFF
    data[-4:] = struct.pack("<I", want ^ state)
    assert zlib.crc32(bytes(data)) == crc
    return bytes(data)


def _task(mem: Memory) -> Gfx:
    """A task running F3DDKR: the frame cleared to black, a viewport the size of the screen, no dither."""
    g = Gfx(mem, "Fast3D")
    mem.w32(g.task + 24, 0x80000000 | mem.put(_ucode_data(F3DDKR_CRC), 16))
    g.frame().fill(0)
    vp = mem.put(struct.pack(">8h", W * 2, H * 2, 511, 0, W * 2, H * 2, 511, 0))
    g(0x03800010, 0x80000000 | vp)                       # G_MOVEMEM G_MV_VIEWPORT (Fast3D's)
    g.othermode(NO_DITHER, 0)
    g(*SHADE)
    return g


def _mtx(mem: Memory, g: Gfx, slot: int = 0, sx: float = 1 / 160, dx: float = 0) -> None:
    """G_DMA_MTX: a model-view-projection matrix into `slot` (the screen's pixels to clip space, moved by dx)."""
    g(0x01000040 | slot << 22, 0x80000000 | mem.put(matrix([sx, 0, 0, 0, 0, 1 / 120, 0, 0, 0, 0, 1 / 512, 0,
                                                            dx, 0, 0, 1])))


def _vtx(mem: Memory, g: Gfx, points: list[tuple], append: bool = False, address: int | None = None) -> int:
    """G_DMA_VTX: 10-byte vertices (x, y, z, r, g, b, a). Returns where they were put."""
    a = address if address is not None else mem.put(b"".join(struct.pack(">3h4B", *p) for p in points))
    n = len(points)
    g(0x04000000 | (n - 1) << 19 | (1 << 16 if append else 0) | (18 * n + 8), 0x80000000 | a)
    return a


def _tris(mem: Memory, g: Gfx, tris: list[tuple], textured: bool = False) -> None:
    """G_DMA_TRI: 16-byte triangles (flags, three vertex slots, then each vertex's s and t in texels)."""
    data = b"".join(struct.pack(">4B6h", flags, a, b, c, *(int(v * 32) for v in st)) for flags, a, b, c, *st in
                    [t if len(t) > 4 else (*t, 0, 0, 0, 0, 0, 0) for t in tris])
    n = len(tris)
    g(0x05000000 | ((n - 1) << 4 | int(textured)) << 16 | n * 16, 0x80000000 | mem.put(data))


SQUARE = [(-40, 40, 0, 255, 0, 0, 255), (40, 40, 0, 255, 0, 0, 255), (40, -40, 0, 255, 0, 0, 255),
          (-40, -40, 0, 255, 0, 0, 255)]  # x and y in the screen's pixels from its centre, y up: red
CENTRE = (W // 2, H // 2)


def _draw(renderer, tmp_path, mem: Memory, g: Gfx) -> Memory:
    g(0xB8000000)  # G_ENDDL (GBI 1)
    out, _ = run(renderer, mem, tmp_path, g.end())
    return out


def test_its_triangles_draw_through_the_matrix_slot_and_cull_by_their_own_flag(renderer, tmp_path):
    """Two red squares from the same vertices: one with its triangles facing the camera, the other drawn from
    behind, which the microcode culls unless the triangle's flag 0x40 says to draw it anyway."""
    mem = Memory()
    g = _task(mem)
    _mtx(mem, g, dx=-0.5)  # the first square left of the centre
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0, 0, 3, 2), (0, 0, 2, 1)])               # facing: drawn
    _mtx(mem, g, slot=1, dx=0.5)                              # slot 1, right of the centre
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0, 0, 1, 2), (0, 0, 2, 3)])               # from behind: culled
    _mtx(mem, g, slot=2, dx=0.5)
    g(0x01000040 | 2 << 22, 0x80000000 | mem.put(matrix([1 / 160, 0, 0, 0, 0, 1 / 120, 0, 0, 0, 0, 1 / 512, 0,
                                                          0.5, -0.5, 0, 1])))  # right and below
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0x40, 0, 1, 2), (0x40, 0, 2, 3)])         # from behind, flagged: drawn
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(CENTRE[0] - 80, CENTRE[1]) == RED
    assert out.pixel(CENTRE[0] + 80, CENTRE[1]) == (0, 0, 0)
    assert out.pixel(CENTRE[0] + 80, CENTRE[1] + 60) == RED
    assert out.pixel(CENTRE[0], CENTRE[1]) == (0, 0, 0)


def test_moveword_selects_a_matrix_slot_and_offsets_move_the_addresses(renderer, tmp_path):
    """Slot 0 places a square left, slot 1 right; G_MOVEWORD 0x0A picks slot 1 again after slot 0 was loaded last.
    G_DMA_OFFSETS (0xBF) adds to the vertices' address: the list names 16 bytes before them."""
    mem = Memory()
    g = _task(mem)
    _mtx(mem, g, slot=1, dx=0.5)
    _mtx(mem, g, slot=0, dx=-0.5)
    g(0xBC00000A, 1 << 6)                                     # G_MW_MVPMATRIX: slot 1
    g(0xBF000000, 16)                                         # G_DMA_OFFSETS: matrices 0, vertices 16
    a = mem.put(bytes(16) + b"".join(struct.pack(">3h4B", *p) for p in SQUARE))
    _vtx(mem, g, SQUARE, address=a)
    _tris(mem, g, [(0, 0, 3, 2), (0, 0, 2, 1)])
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(CENTRE[0] + 80, CENTRE[1]) == RED
    assert out.pixel(CENTRE[0] - 80, CENTRE[1]) == (0, 0, 0)


def test_a_billboard_is_drawn_at_its_anchors_place(renderer, tmp_path):
    """Billboarding on: the first vertex is an anchor (here 80 pixels right of the centre), and the appended ones,
    transformed by the billboard matrix, are moved by its place in clip space, w too (so a sprite shrinks with its
    anchor's distance): with a matrix that leaves w 0, the square is drawn around the anchor at its own size."""
    mem = Memory()
    g = _task(mem)
    _mtx(mem, g)
    _vtx(mem, g, [(80, 0, 0, 0, 0, 0, 255)])                  # the anchor, in slot 0
    g(0x01000040 | 2 << 22, 0x80000000 | mem.put(matrix([1 / 160, 0, 0, 0, 0, 1 / 120, 0, 0, 0, 0, 1 / 512, 0,
                                                          0, 0, 0, 0])))  # the billboard's: w 0
    g(0xBC000002, 1)                                          # billboarding on
    _vtx(mem, g, SQUARE, append=True)                         # slots 1 to 4
    _tris(mem, g, [(0, 1, 4, 3), (0, 1, 3, 2)])
    g(0xBC000002, 0)
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(CENTRE[0] + 80, CENTRE[1]) == RED
    assert out.pixel(CENTRE[0], CENTRE[1]) == (0, 0, 0)


def test_a_counted_list_runs_its_commands_then_returns(renderer, tmp_path):
    """G_DMA_DL runs n commands with no G_ENDDL to end them, then the list goes on: the square drawn inside it,
    and a second one after it."""
    mem = Memory()
    g = _task(mem)
    sub = Gfx(mem, "Fast3D", at=0x3000, task=0x180)
    _mtx(mem, sub, dx=-0.5)
    _vtx(mem, sub, SQUARE)
    _tris(mem, sub, [(0, 0, 3, 2), (0, 0, 2, 1)])
    sub(0xDEADBEEF, 0xDEADBEEF)                               # past its count: never run
    g(0x07000000 | 3 << 16 | 3 * 8, 0x80000000 | 0x3000)      # G_DMA_DL: 3 commands
    _mtx(mem, g, dx=0.5)
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0, 0, 3, 2), (0, 0, 2, 1)])
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(CENTRE[0] - 80, CENTRE[1]) == RED
    assert out.pixel(CENTRE[0] + 80, CENTRE[1]) == RED


def test_a_textured_batch_takes_each_triangles_own_texture_coordinates(renderer, tmp_path):
    """A batch with its texture bit set draws with the tile G_TEXTURE names even after G_TEXTURE turned texturing
    off (as the game's lists do), at the texture coordinates each triangle carries: here a 4-texel blue square, the
    combiner taking the texel."""
    mem = Memory()
    g = _task(mem)
    image = mem.put((0x003F).to_bytes(2, "big") * 16)         # blue, 4 x 4 RGBA16
    g(0xFD100003, 0x80000000 | image)
    g(0xF5100200, 7 << 24)
    g(0xF4000000, 7 << 24 | (3 * 4) << 12 | 3 * 4)
    g(0xF5100200, 0)
    g(0xF2000000, (3 * 4) << 12 | 3 * 4)
    g(0xBB000000, 0)                                          # G_TEXTURE: off, tile 0
    g(*TEX)
    _mtx(mem, g)
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0, 0, 3, 2, 0, 0, 0, 3, 3, 3), (0, 0, 2, 1, 0, 0, 3, 3, 3, 0)], textured=True)
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(*CENTRE) == BLUE


def test_the_frame_buffer_is_named_by_a_segment(renderer, tmp_path):
    """The racing game names its frame buffer by segment 1 (0x01000000): the colour image's address goes through the
    segments, as every microcode resolves it. Taken as it was, the game drew outside RAM and showed black."""
    mem = Memory()
    g = _task(mem)
    g(0xBC000406, FB)                                         # G_MW_SEGMENT: segment 1 at the frame
    g(0xFF10013F, 0x01000000)                                 # the colour image by segment
    _mtx(mem, g)
    _vtx(mem, g, SQUARE)
    _tris(mem, g, [(0, 0, 3, 2), (0, 0, 2, 1)])
    out = _draw(renderer, tmp_path, mem, g)
    assert out.pixel(*CENTRE) == RED
