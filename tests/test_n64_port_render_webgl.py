"""The browser build's WebGL renderer (consoles/n64/port_wasm/web/gl.js) against the software renderer (render.c):
the RDP test scenes (test_n64_port_render_rdp.py) drawn both ways, gl.js in a headless Chrome at the console's
resolution from what render_gpu.h sends it, and compared in the frame buffer's 5-bit channels.

The two can't agree everywhere: GL rasterizes edges and interpolates shade and texture coordinates by its own rules,
draws in 8 bits without the RDP's dither, and can't read the next pixel of a span (two cycles' pipeline), TMEM past a
tile, or the stored delta z. What they must agree on is the arithmetic of each pixel (the combiner, the filter, the
blender, alpha compare): a step's difference across a surface, or a surface gone, is the shader's bug. Skips without
Chrome (N64_CHROME, or the usual places)."""

from __future__ import annotations

import base64
import functools
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tests import test_n64_port_render_rdp as rdp
from tests.n64_render_kit import FB, RENDER, Gfx, H, Memory, W, build, run

GL = RENDER.parent / "web" / "gl.js"

# render.c's GPU path with a page that writes what it's sent to a file: each run of commands with the draws,
# vertices and texels they name, and each frame shown.
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
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
int port_gpu_ready(void) { return 1; }
int port_gpu_present(uint32_t addr, int w, int h, int flags) {
    uint32_t r[5] = {2, addr, (uint32_t)w, (uint32_t)h, (uint32_t)flags};
    fwrite(r, 4, 5, out);
    return 1;
}
void port_gpu_run(const uint32_t *c, uint32_t words, const void *draws, const float *vertices, const uint32_t *pixels) {
    uint32_t nd = 0, nv = 0, np = 0;
    for (uint32_t i = 0; i < words; i += 5) {
        if (c[i] == 2) {
            const uint32_t *d = (const uint32_t *)draws + c[i + 1] * 162;
            if (c[i + 1] + 1 > nd) nd = c[i + 1] + 1;
            if (d[160] + d[161] > nv) nv = d[160] + d[161];
        }
        if (c[i] == 1 && c[i + 4] + c[i + 2] * c[i + 3] > np) np = c[i + 4] + c[i + 2] * c[i + 3];
    }
    uint32_t h[5] = {1, words, nd, nv, np};
    fwrite(h, 4, 5, out); fwrite(c, 4, words, out); fwrite(draws, 4, nd * 162, out);
    fwrite(vertices, 4, nv * 14, out); fwrite(pixels, 4, np, out);
}
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(ram, 1, sizeof ram, f) != sizeof ram) return 3;
    fclose(f);
    out = fopen(argv[2], "wb");
    port_render_task((uint32_t)strtoul(argv[3], NULL, 16));
    port_frame_shown((uint32_t)strtoul(argv[4], NULL, 16), 1);
    fclose(out);
    return 0;
}
"""

# The page: each scene's recording given to a GpuRenderer at 1x, the frame shown read back.
PAGE = """<!doctype html><html><body><canvas id=c></canvas><pre id=out>pending</pre>
<script type=module>
import { GpuRenderer } from "./gl.js";
const out = document.getElementById("out");
try {
  const results = {};
  for (const name of await (await fetch("scenes.json")).json()) {
    const raw = await (await fetch(name + ".gpu")).arrayBuffer(), v = new DataView(raw);
    const r = new GpuRenderer(document.getElementById("c"), 1, false);
    let p = 0, shown = 0;
    while (p < raw.byteLength) {
      const tag = v.getUint32(p, true), a = v.getUint32(p + 4, true), b = v.getUint32(p + 8, true);
      const c = v.getUint32(p + 12, true), d = v.getUint32(p + 16, true);
      p += 20;
      if (tag === 1) { // words, draws, vertices, texels
        const draws = p + a * 4, vertices = draws + b * 162 * 4, pixels = vertices + c * 14 * 4, end = pixels + d * 4;
        r.run(raw.slice(0, end), p, a, draws, vertices, pixels);
        p = end;
      } else { r.present(a, b, c, d); shown = a; }
    }
    const gl = r.gl, t = r.targets.get(shown), px = new Uint8Array(t.w * t.h * 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
    gl.readPixels(0, 0, t.w, t.h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let s = "";
    for (let y = t.h - 1; y >= 0; y--)
      for (let x = 0; x < t.w; x++) { const i = (y * t.w + x) * 4; s += String.fromCharCode(px[i], px[i + 1], px[i + 2]); }
    results[name] = { w: t.w, h: t.h, rgb: btoa(s) };
    r.dispose();
  }
  out.textContent = JSON.stringify(results);
} catch (e) { out.textContent = "ERROR " + e.stack; }
</script></body></html>"""

# Per scene, in percent of pixels: how many may differ by more than one step, and why. The rest of each scene's
# differences are a step (shade and texture coordinates interpolated in floats, no dither).
TOLERANCE = {
    "copy-scissor": (3.0, "copy mode's span runs to the scissor's right edge inclusive; GL's rectangle stops before it"),
    "depth-slopes": (45.0, "the RDP's delta z, which GL's depth test has no place for"),
    "rect": (10.0, "a copy-mode rectangle reading TMEM past its tile, which GL's texture of the tile doesn't hold"),
    "two-cycle": (35.0, "the second cycle reading the next pixel's texel (the RDP's pipeline), and the dither"),
    "random": (3.0, "edges"), "random-aa": (3.0, "edges, and the coverage anti-aliasing GL doesn't do"),
    "tilted-bilerp": (4.5, "edges: texture coordinates at a slant, a texel's border falls on another pixel"),
}
EDGES = 1.5  # every other scene: the edges GL rasterizes a pixel apart


def _chrome() -> str | None:
    for c in (os.environ.get("N64_CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
              "/opt/pw-browsers/chromium"):
        if c and Path(c).is_file():
            return c
    return None


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    """Each scene as software draws it (memory) and as gl.js does (width, height, RGB)."""
    chrome, cc = _chrome(), shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not chrome or not cc:
        pytest.skip("no Chrome (N64_CHROME) or no C compiler")
    work = tmp_path_factory.mktemp("webgl")
    site = work / "site"
    site.mkdir()
    software = build(work)
    (work / "gpu.c").write_text(HARNESS)
    subprocess.run([cc, "-O1", "-o", str(work / "gpu"), str(work / "gpu.c"), str(RENDER), "-lm"], check=True,
                   capture_output=True, timeout=180)
    names, memories = sorted(rdp.SCENES), {}
    for name in names:
        def scene(name=name):
            mem = Memory()
            g = Gfx(mem)
            rdp._start(mem, g)
            rdp.SCENES[name](mem, g)
            g(0xDF000000)
            return mem, g
        mem, g = scene()
        memories[name] = run(software, mem, work, g.end())[0]
        mem, g = scene()
        (work / "in.bin").write_bytes(mem.ram)
        subprocess.run([str(work / "gpu"), str(work / "in.bin"), str(site / f"{name}.gpu"), f"{g.end():x}",
                        f"{0x80000000 | FB:x}"], check=True, capture_output=True, timeout=120)
    shutil.copy(GL, site / "gl.js")
    (site / "page.html").write_text(PAGE)
    (site / "scenes.json").write_text(json.dumps(names))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        dom = subprocess.run([chrome, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                              "--no-first-run", "--no-sandbox", f"--user-data-dir={work / 'profile'}",
                              "--virtual-time-budget=60000", "--dump-dom",
                              f"http://127.0.0.1:{server.server_address[1]}/page.html"],
                             capture_output=True, text=True, timeout=300).stdout
    finally:
        server.shutdown()
    found = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    text = html.unescape(found.group(1)) if found else dom[-2000:]
    if not text.startswith("{"):
        pytest.fail("gl.js didn't draw the scenes: " + text[:2000])
    pages = json.loads(text)
    return {n: (memories[n], pages[n]["w"], pages[n]["h"], base64.b64decode(pages[n]["rgb"])) for n in names}


@pytest.mark.parametrize("name", sorted(rdp.SCENES))
def test_webgl_draws_the_scene_as_software_does(drawn, name):
    mem, w, h, rgb = drawn[name]
    assert (w, h) == (W, H)
    beyond = []
    for y in range(H):
        for x in range(W):
            i = (y * w + x) * 3
            ours = mem.pixel(x, y)
            gl = (rgb[i] >> 3, rgb[i + 1] >> 3, rgb[i + 2] >> 3)
            if max(abs(a - b) for a, b in zip(ours, gl)) > 1:
                beyond.append((x, y, ours, gl))
    pct = 100 * len(beyond) / (W * H)
    assert pct <= TOLERANCE.get(name, (EDGES,))[0], (name, f"{pct:.2f}%", beyond[:4])
