"""The N64 renderer's GPU path (consoles/n64/port_wasm/render_gpu.h), which the browser build's WebGL renderer
(web/gl.js) draws from: render.c compiled with a harness standing in for the page, which records what it's sent.
The display lists come from tests/n64_render_kit.py, as for the software renderer's tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.n64_render_kit import (
    FB,
    RENDER,
    SHADE,
    TEX,
    ZB,
    Gfx,
    Memory,
    W,
    load_texture,
    matrix,
    quadrant_texture,
    vertex,
)

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void port_render_task(uint32_t task);
void port_frame_shown(uint32_t fb, unsigned long n);
static uint8_t ram[0x400000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
static FILE *out;
static int renderer = 1, shows_gpu = 1;
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; fprintf(out, "rdram %d %d\n", w, h); }
/* The page: what it's sent, one line each (the draws by render_gpu.h's GpuDraw words, as gl.js reads them) */
int port_gpu_ready(void) { return renderer; }
int port_gpu_present(uint32_t addr, int w, int h, int flags) { fprintf(out, "present %x %d %d %d\n", addr, w, h, flags); return shows_gpu; }
void port_gpu_run(const uint32_t *c, uint32_t words, const void *draws, const float *vertices, const uint32_t *pixels) {
    for (uint32_t i = 0; i < words; i += 5) {
        if (c[i] == 1) {
            const uint32_t *p = pixels + c[i + 4];
            fprintf(out, "texture %u %u %u", c[i + 1], c[i + 2], c[i + 3]);
            for (uint32_t k = 0; k < c[i + 2] * c[i + 3] && k < 64; k++) fprintf(out, " %08x", p[k]);
            fputc('\n', out);
        } else if (c[i] == 2) {
            const uint32_t *d = (const uint32_t *)draws + c[i + 1] * 156;
            fprintf(out, "draw kind %u flags %u cimg %x zimg %x tile %d count %u textures", d[0], d[1], d[45], d[49],
                    (int)d[39], d[155]);
            for (int k = 0; k < 8; k++) fprintf(out, " %u", d[50 + k * 13]);
            fputc('\n', out);
            for (uint32_t v = 0; v < d[155]; v++) {
                const float *f = vertices + (d[154] + v) * 10;
                fprintf(out, "  v %.2f %.2f %.4f %.3f %.3f %.3f %.2f %.2f %.2f %.2f\n", f[0], f[1], f[2], f[3], f[4], f[5],
                        f[6], f[7], f[8], f[9]);
            }
        } else if (c[i] == 3) fprintf(out, "clear-depth %x\n", c[i + 1]);
    }
}
/* argv: memory in, the log out, then steps: tasks (hex), "show:ADDR", "poke:ADDR" (the CPU writes a word there),
   "renderer:N" (the page's renderer: 0 none, another number a new one), "software" (the page shows no GPU frame) */
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(ram, 1, sizeof ram, f) != sizeof ram) return 3;
    fclose(f);
    out = fopen(argv[2], "w");
    for (int i = 3; i < argc; i++) {
        if (!strncmp(argv[i], "show:", 5)) port_frame_shown((uint32_t)strtoul(argv[i] + 5, NULL, 16), 1);
        else if (!strncmp(argv[i], "poke:", 5)) { uint32_t a = (uint32_t)strtoul(argv[i] + 5, NULL, 16); ram[a] ^= 0xFF; }
        else if (!strncmp(argv[i], "renderer:", 9)) renderer = atoi(argv[i] + 9);
        else if (!strcmp(argv[i], "software")) shows_gpu = 0;
        else { fprintf(out, "task\n"); port_render_task((uint32_t)strtoul(argv[i], NULL, 16)); }
    }
    fclose(out);
    f = fopen(argv[1], "wb"); fwrite(ram, 1, sizeof ram, f); fclose(f);
    return 0;
}
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    folder = tmp_path_factory.mktemp("render-gpu")
    (folder / "harness.c").write_text(HARNESS)
    program = folder / "render"
    subprocess.run([cc, "-O1", "-o", str(program), str(folder / "harness.c"), str(RENDER), "-lm"], check=True,
                   capture_output=True, timeout=180)
    return program


def _run(program: Path, mem: Memory, folder: Path, *steps) -> tuple[list[str], Memory]:
    ram, log = folder / "ram.bin", folder / "log.txt"
    ram.write_bytes(mem.ram)
    subprocess.run([str(program), str(ram), str(log), *(s if isinstance(s, str) else f"{s:x}" for s in steps)],
                   check=True, capture_output=True, timeout=120)
    after = Memory()
    after.ram = bytearray(ram.read_bytes())
    return log.read_text().splitlines(), after


def _draws(log: list[str]) -> list[tuple[dict, list[list[float]]]]:
    draws = []
    for line in log:
        if line.startswith("draw "):
            words = line.split()
            fields = dict(zip(words[1:12:2], words[2:13:2]))
            fields["textures"] = [int(w) for w in words[14:22]]
            draws.append((fields, []))
        elif line.startswith("  v ") and draws:
            draws[-1][1].append([float(v) for v in line.split()[1:]])
    return draws


def _triangle_list(mem: Memory, g: Gfx) -> None:
    """A shaded triangle over the left half of the screen, identity matrices, the z buffer on."""
    ident = mem.put(matrix())
    g.zimage()
    g.frame(ZB).fill(0xFFFC)  # the z buffer cleared to the farthest
    g.frame()
    g(0xDA380007, 0x80000000 | ident)
    g(0xDA380003, 0x80000000 | ident)
    g.othermode(0, 0x30)  # Z_CMP | Z_UPD
    g(*SHADE)
    g(0xD9000000, 0x200001)  # smooth, G_ZBUFFER
    vtx = mem.put(b"".join(vertex(x, y, 0, rgba=c) for x, y, c in [(-1, 1, 0xFF0000FF), (0, 1, 0x00FF00FF), (-1, -1, 0x0000FFFF)]))
    g(0x01000000 | 3 << 12 | 3 << 1, 0x80000000 | vtx)
    g(0x05000204)


def test_a_triangle_goes_to_the_page_and_not_into_rdram(page, tmp_path):
    """On the GPU path the display list is read, transformed and clipped as for software, and the triangle goes to
    the page in screen space with its shade, its depth, and the frame and z images it's drawn into; the frame in RDRAM
    is left as it was (the page draws it)."""
    mem = Memory()
    g = Gfx(mem)
    _triangle_list(mem, g)
    g(0xDF000000)
    log, after = _run(page, mem, tmp_path, g.end())
    (draw, vertices), = _draws(log)
    assert draw["kind"] == "0" and int(draw["flags"]) & 1, draw  # combined, depth on
    assert draw["cimg"] == f"{FB:x}" and draw["zimg"] == f"{ZB:x}"
    assert [(v[0], v[1]) for v in vertices] == [(0, 0), (W / 2, 0), (0, 240)]
    assert [v[6:10] for v in vertices] == [[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1]]
    assert all(0 < v[2] < 1 for v in vertices)  # depth: the screen's middle
    assert not any(after.ram[FB:FB + W * 240 * 2]), "the GPU path wrote the frame in RDRAM"


def test_equal_draws_join_and_a_change_of_state_starts_another(page, tmp_path):
    """Triangles drawn with the same state go as one draw (the page's one drawArrays); a new primitive colour is
    another."""
    mem = Memory()
    g = Gfx(mem)
    _triangle_list(mem, g)
    g(0x05000402)
    g(0xFA000000, 0x808080FF)
    g(0x05000204)
    g(0xDF000000)
    log, _ = _run(page, mem, tmp_path, g.end())
    assert [int(d["count"]) for d, _ in _draws(log)] == [6, 3]


def test_a_texture_is_decoded_once_and_sent_once(page, tmp_path):
    """A tile's texels are decoded from TMEM as the sampler reads them (RGBA, 8 bits a channel) and sent once: a
    second task loading the same texels draws with the texture the page already has; other texels are sent."""
    mem = Memory()
    image = quadrant_texture(mem)
    g = Gfx(mem)
    g.frame()
    g.othermode(0, 0)
    g(*TEX)
    load_texture(g, image)
    g(0xE4000000 | (8 * 4) << 12 | 8 * 4, 0)  # G_TEXRECT, tile 0, 0,0 to 8,8
    g(0xE1000000, 0)
    g(0xF1000000, 1024 << 16 | 1024)
    g(0xDF000000)
    first = g.end()
    log, _ = _run(page, mem, tmp_path, first, first)
    textures = [line for line in log if line.startswith("texture ")]
    assert len(textures) == 1, textures
    words = textures[0].split()
    assert words[2:4] == ["8", "8"]
    # the top-left texel red, the top-right green (RGBA 5551 to 8 bits a channel, as the sampler reads them)
    assert words[4] == "ff0000ff" and words[4 + 4] == "ff00ff00", words[4:12]
    draws = _draws(log)
    assert len(draws) == 2 and draws[0][0]["textures"][0] == draws[1][0]["textures"][0] != 0
    assert draws[0][0]["kind"] == "0" and int(draws[0][0]["flags"]) & 16  # combined, textured
    # the rectangle's corners: s at the first pixel's centre is 0, as the software path samples it
    corners = draws[0][1]
    assert (corners[0][0], corners[0][1], corners[0][4], corners[0][5]) == (0, 0, -0.5, -0.5)
    # other texels: a texture of its own
    mem.w16(image, 0x003F)
    log, _ = _run(page, mem, tmp_path, first)
    assert len([line for line in log if line.startswith("texture ")]) == 1


def test_a_new_renderer_is_sent_every_texture_again(page, tmp_path):
    """The page makes a new renderer when the player changes the resolution or switches back to WebGL: it has none
    of the textures the old one had, so they are sent again."""
    mem = Memory()
    image = quadrant_texture(mem)
    g = Gfx(mem)
    g.frame()
    g.othermode(0, 0)
    g(*TEX)
    load_texture(g, image)
    g(0xE4000000 | (8 * 4) << 12 | 8 * 4, 0)
    g(0xE1000000, 0)
    g(0xF1000000, 1024 << 16 | 1024)
    g(0xDF000000)
    task = g.end()
    log, _ = _run(page, mem, tmp_path, task, "renderer:0", task, "renderer:2", task)
    assert len([line for line in log if line.startswith("texture ")]) == 2
    assert len(_draws(log)) == 2  # (without a renderer, software drew the second task)


def test_filling_the_depth_image_clears_the_pages_depth(page, tmp_path):
    """Games clear their z buffer by filling it as a colour image: on the GPU that's a clear of its depth."""
    mem = Memory()
    g = Gfx(mem)
    g.zimage()
    g.frame(ZB).fill(0xFFFC)
    g.frame().fill(0x0001)
    g(0xDF000000)
    log, _ = _run(page, mem, tmp_path, g.end())
    assert f"clear-depth {ZB:x}" in log
    (fill, corners), = _draws(log)
    assert fill["kind"] == "2" and fill["cimg"] == f"{FB:x}"  # the colour fill, edges inclusive
    assert (corners[0][0], corners[0][1], corners[2][0], corners[2][1]) == (0, 0, W, 240)


def test_the_gpus_frame_is_shown_until_the_cpu_writes_into_it(page, tmp_path):
    """The VI shows what the GPU drew while the frame buffer's RDRAM is as it was then; once the CPU writes there
    (a logo, a movie), or the page has no GPU frame for it, the frame is shown from RDRAM as in software."""
    mem = Memory()
    g = Gfx(mem)
    _triangle_list(mem, g)
    g(0xDF000000)
    task = g.end()
    log, _ = _run(page, mem, tmp_path, task, f"show:{FB:x}", f"poke:{FB + 1000:x}", f"show:{FB:x}")
    shown = [line for line in log if line.startswith(("present", "rdram"))]
    assert shown == [f"present {FB:x} 320 240 0", "rdram 320 240"]
    log, _ = _run(page, mem, tmp_path, "software", task, f"show:{FB:x}")
    assert [line for line in log if line.startswith(("present", "rdram"))] == [f"present {FB:x} 320 240 0", "rdram 320 240"]


def test_without_a_gpu_the_software_path_draws_as_before(page, tmp_path):
    """No renderer on the page (the software one chosen, the native and Miyoo hosts): nothing is sent and the
    triangle is drawn into RDRAM."""
    mem = Memory()
    g = Gfx(mem)
    _triangle_list(mem, g)
    g(0xDF000000)
    log, after = _run(page, mem, tmp_path, "renderer:0", g.end())
    assert not _draws(log)
    assert after.pixel(10, 10) != (0, 0, 0)


def test_the_shaders_follow_the_combiner_and_blender():
    """gl.js makes a shader per setting from the other modes and combine words (web_gl_shaders.mjs)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node isn't installed")
    done = subprocess.run([node, str(Path(__file__).with_name("web_gl_shaders.mjs"))], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_the_page_reads_the_draw_as_render_gpu_lays_it_out():
    """gl.js reads each draw by word (DRAW_WORDS, the fields' places): its numbers and render_gpu.h's struct agree."""
    header = RENDER.with_name("render_gpu.h").read_text()
    gl = RENDER.parent.joinpath("web", "gl.js").read_text()
    assert "sizeof(GpuDraw) == 156 * 4" in header and "const DRAW_WORDS = 156;" in gl
    assert "const VERTEX = 10;" in gl and "#define GPU_VERTEX 10" in header
