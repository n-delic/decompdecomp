"""Compare the N64 port's renderer with angrylion's, task by task.

The port's renderer (src/decompnet/consoles/n64/port_wasm/render.c) draws a game's graphics tasks in software, in
its own arithmetic. angrylion's RDP renderer reproduces the console's RDP bit for bit, so where the two disagree on
the same RDP commands, the port's is the one that's wrong.

1. Run a port with PORT_RDPDUMP=<folder> and PORT_RDPDUMP_TASKS=<first>-<last>: each of those tasks' RDP commands
   (as the RSP would send them: triangles as the RDP's triangle commands, S2DEX's objects as its loads and
   rectangles) are recorded with the game's memory before and after it (NNNNNN.cmds, NNNNNN.before, NNNNNN.after,
   and RDRAM's hidden bits in NNNNNN.hidden).
2. Run this with that folder and a checkout of angrylion-rdp-plus (https://github.com/ata4/angrylion-rdp-plus): it
   builds angrylion's core into a small program (in --work), replays each task's commands on its memory from
   before, and compares the colour images the task drew with the port's, pixel by pixel. angrylion's code is under
   the MAME license (no commercial use), so it is never copied into decompdecomp: point this at your own checkout.

    uv run python tools/n64_rdp_compare.py DUMPS --angrylion ~/src/angrylion-rdp-plus --out REPORT

Per task it prints how many pixels differ and by how much, and writes the port's image, angrylion's and their
difference (brightened) as PNGs into --out.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

RAM = 0x800000

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdbool.h>
#include "n64video.c" /* the whole core in this file: its hidden RDRAM bits are set from the dump */
void msg_error(const char *f, ...) { va_list a; va_start(a, f); vfprintf(stderr, f, a); va_end(a); fputc('\n', stderr); }
void msg_warning(const char *f, ...) { (void)f; }
void msg_debug(const char *f, ...) { (void)f; }
void parallel_init(uint32_t n, bool b) { (void)n; (void)b; }
void parallel_run(void task(uint32_t)) { task(0); }
uint32_t parallel_num_workers(void) { return 1; }
void parallel_close(void) {}
static uint8_t rdram[0x800000];
static uint32_t dmem[1024];
static uint32_t regs_dp[8], regs_vi[14], *dp[8], *vi[14], mi;
static void interrupt(void) {}
static uint32_t be(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }
/* argv: memory before (big-endian, as the console has it), the commands (big-endian words), memory after (out),
   and the hidden bits before (optional) */
int main(int argc, char **argv) {
    if (argc != 4 && argc != 5) return 2;
    static uint8_t raw[0x800000];
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 3;
    size_t size = fread(raw, 1, sizeof raw, f); /* 4 or 8 MB */
    fclose(f);
    if (size != 0x400000 && size != 0x800000) return 3;
    for (uint32_t i = 0; i < sizeof raw; i += 4) { uint32_t w = be(raw + i); memcpy(rdram + i, &w, 4); }
    f = fopen(argv[2], "rb");
    if (!f) return 3;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *cmds = malloc((size_t)n + 8);
    if (fread(cmds, 1, (size_t)n, f) != (size_t)n) return 3;
    fclose(f);
    for (int i = 0; i < 8; i++) dp[i] = &regs_dp[i];
    static uint8_t hidden[0x400000];
    size_t hidden_n = 0;
    if (argc > 4 && (f = fopen(argv[4], "rb"))) { hidden_n = fread(hidden, 1, sizeof hidden, f); fclose(f); }
    for (int i = 0; i < 14; i++) vi[i] = &regs_vi[i];
    struct n64video_config c;
    n64video_config_init(&c);
    c.gfx.rdram = rdram; c.gfx.rdram_size = (uint32_t)size; c.gfx.dmem = (uint8_t *)dmem;
    c.gfx.vi_reg = vi; c.gfx.dp_reg = dp; c.gfx.mi_intr_reg = &mi; c.gfx.mi_intr_cb = interrupt; /* G_RDPFULLSYNC raises it */
    c.parallel = false; c.num_workers = 1;
    n64video_init(&c);
    /* the coverage the port's earlier tasks left in RDRAM's hidden bits (4: none, the pixel's low bit says) */
    for (size_t i = 0; i < hidden_n && i < sizeof rdram_hidden; i++) rdram_hidden[i] = hidden[i];
    regs_dp[DP_STATUS] = 1; /* commands from DMEM (XBUS), up to 4 KB of whole commands at a time */
    for (long at = 0; at < n;) {
        long len = 0;
        while (at + len < n) {
            uint32_t id = cmds[at + len] & 0x3F, words = 2;
            if (id >= 0x08 && id <= 0x0F) words = 8 + (id & 4 ? 16 : 0) + (id & 2 ? 16 : 0) + (id & 1 ? 4 : 0);
            else if (id == 0x24 || id == 0x25) words = 4;
            if (len + (long)words * 4 > 4096) break;
            len += (long)words * 4;
        }
        if (at + len > n) len = n - at;
        for (long i = 0; i < len; i += 4) dmem[i / 4] = be(cmds + at + i);
        regs_dp[DP_START] = regs_dp[DP_CURRENT] = 0; regs_dp[DP_END] = (uint32_t)len;
        n64video_process_list();
        at += len;
    }
    for (uint32_t i = 0; i < sizeof raw; i += 4) {
        uint32_t w; memcpy(&w, rdram + i, 4);
        raw[i] = (uint8_t)(w >> 24); raw[i + 1] = (uint8_t)(w >> 16); raw[i + 2] = (uint8_t)(w >> 8); raw[i + 3] = (uint8_t)w;
    }
    f = fopen(argv[3], "wb");
    fwrite(raw, 1, size, f); fclose(f);
    n64video_close();
    return 0;
}
"""


OURS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void port_rdp_replay(const uint8_t *cmds, size_t bytes, const uint8_t *hidden, size_t hidden_n);
static uint8_t ram[0x800000];
uint8_t *port_memory(void) { return ram - 0x80000000u; }
int port_live_active(void) { return 0; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) { *n = 0; return NULL; }
    fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*n + 1);
    *n = fread(b, 1, *n, f); fclose(f);
    return b;
}
/* argv: memory before, the commands, memory after (out), the hidden bits before */
int main(int argc, char **argv) {
    if (argc < 4) return 2;
    size_t size, n, hn = 0;
    uint8_t *before = slurp(argv[1], &size), *cmds = slurp(argv[2], &n), *hidden = argc > 4 ? slurp(argv[4], &hn) : NULL;
    if (!before || !cmds || size > sizeof ram) return 3;
    memcpy(ram, before, size);
    port_rdp_replay(cmds, n, hidden, hn);
    FILE *f = fopen(argv[3], "wb");
    fwrite(ram, 1, size, f); fclose(f);
    return 0;
}
"""
RENDER = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/render.c"


def build_ours(work: Path) -> Path:
    """The port's renderer around the same replay: the two RDPs given the same commands."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    work.mkdir(parents=True, exist_ok=True)
    (work / "ours.c").write_text(OURS)
    program = work / "ours_replay"
    subprocess.run([cc, "-O2", "-o", str(program), str(work / "ours.c"), str(RENDER), "-lm"], check=True, timeout=600)
    return program


def build(angrylion: Path, work: Path) -> Path:
    core = angrylion / "src" / "core"
    if not (core / "n64video.c").exists():
        sys.exit(f"{angrylion} isn't an angrylion-rdp-plus checkout (no src/core/n64video.c)")
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        sys.exit("no C compiler")
    work.mkdir(parents=True, exist_ok=True)
    (work / "harness.c").write_text(HARNESS)
    program = work / "angrylion_replay"
    subprocess.run([cc, "-O2", "-w", f"-I{core}", "-o", str(program), str(work / "harness.c")],
                   check=True, timeout=600)
    return program


def commands(data: bytes):
    """The recorded commands, each as its words."""
    words = struct.unpack(f">{len(data) // 4}I", data[: len(data) // 4 * 4])
    i = 0
    while i < len(words):
        op = words[i] >> 24 & 0x3F
        n = 2
        if 0x08 <= op <= 0x0F:
            n = 8 + (16 if op & 4 else 0) + (16 if op & 2 else 0) + (4 if op & 1 else 0)
        elif op in (0x24, 0x25):
            n = 4
        yield words[i:i + n]
        i += n


def images(data: bytes) -> list[tuple[int, int, int, int]]:
    """The colour images drawn to: address, width, pixel size (2: 16-bit, 3: 32-bit), lines (the scissor's)."""
    found: dict[int, list[int]] = {}
    current = None
    lines = 240
    for w in commands(data):
        op = w[0] >> 24 & 0x3F
        if op == 0x3F:
            current = (w[1] & 0xFFFFFF, (w[0] & 0xFFF) + 1, w[0] >> 19 & 3)
        elif op == 0x2D:
            lines = (w[1] & 0xFFF) >> 2
        elif current and (0x08 <= op <= 0x0F or op in (0x24, 0x25, 0x36)):
            seen = found.setdefault(current[0], [current[1], current[2], 0])
            seen[2] = max(seen[2], lines)
    return [(a, w, siz, h) for a, (w, siz, h) in found.items() if siz in (2, 3) and h > 0]


def rgb(mem: bytes, addr: int, width: int, siz: int, lines: int) -> list[tuple[int, int, int]]:
    out = []
    for i in range(width * lines):
        if siz == 3:
            a = addr + i * 4
            out.append((mem[a], mem[a + 1], mem[a + 2]))
        else:
            v = mem[addr + i * 2] << 8 | mem[addr + i * 2 + 1]
            out.append(((v >> 11 & 31) << 3, (v >> 6 & 31) << 3, (v >> 1 & 31) << 3))
    return out


def png(path: Path, pixels: list[tuple[int, int, int]], width: int, height: int) -> None:
    raw = b"".join(b"\0" + bytes(c for p in pixels[y * width:(y + 1) * width] for c in p) for y in range(height))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def compare(ours: list, theirs: list) -> dict:
    """Pixels that differ, those differing by more than one 5-bit step, the largest difference, the mean."""
    differ = more = 0
    largest = total = 0
    for a, b in zip(ours, theirs):
        d = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        if d:
            differ += 1
            total += d
            largest = max(largest, d)
            if d > 8:
                more += 1
    n = len(ours) or 1
    return {"pixels": len(ours), "differ": differ, "differ_pct": 100 * differ / n, "beyond_one_step": more,
            "beyond_pct": 100 * more / n, "largest": largest, "mean": total / n}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", type=Path, help="the folder PORT_RDPDUMP wrote")
    ap.add_argument("--angrylion", type=Path, default=os.environ.get("N64_ANGRYLION"),
                    help="a checkout of angrylion-rdp-plus (or N64_ANGRYLION)")
    ap.add_argument("--out", type=Path, help="where to write each task's images (ours, angrylion's, difference)")
    ap.add_argument("--ours", action="store_true",
                    help="replay the commands through the port's renderer too, instead of using the memory the game "
                         "run left: the two RDPs alone, without the RSP (and the current render.c, without a rebuild)")
    ap.add_argument("--work", type=Path, default=Path(".n64_rdp_compare"), help="where the replay program is built")
    args = ap.parse_args()
    if not args.angrylion:
        sys.exit("--angrylion: a checkout of https://github.com/ata4/angrylion-rdp-plus")
    program = build(args.angrylion, args.work)
    ours_program = build_ours(args.work) if args.ours else None
    totals = {"pixels": 0, "differ": 0, "beyond_one_step": 0}
    for cmds in sorted(args.dumps.glob("*.cmds")):
        stem = cmds.with_suffix("")
        after = stem.with_suffix(".after")
        theirs_path = args.work / f"{stem.name}.angrylion"
        hidden = [str(stem.with_suffix(".hidden"))] if stem.with_suffix(".hidden").exists() else []
        subprocess.run([str(program), str(stem.with_suffix(".before")), str(cmds), str(theirs_path), *hidden], check=True,
                       timeout=600)
        if ours_program:
            ours_path = args.work / f"{stem.name}.ours"
            subprocess.run([str(ours_program), str(stem.with_suffix(".before")), str(cmds), str(ours_path), *hidden],
                           check=True, timeout=600)
            after = ours_path
        ours_mem, theirs_mem = after.read_bytes(), theirs_path.read_bytes()
        theirs_path.unlink()
        for addr, width, siz, lines in images(cmds.read_bytes()):
            ours, theirs = rgb(ours_mem, addr, width, siz, lines), rgb(theirs_mem, addr, width, siz, lines)
            r = compare(ours, theirs)
            for k in totals:
                totals[k] += r[k]
            print(f"task {stem.name} image {addr:06X} {width}x{lines}: "
                  f"{r['differ_pct']:.2f}% of pixels differ, {r['beyond_pct']:.2f}% by more than one step, "
                  f"largest {r['largest']}, mean {r['mean']:.2f}")
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                name = f"{stem.name}_{addr:06X}"
                png(args.out / f"{name}_ours.png", ours, width, lines)
                png(args.out / f"{name}_angrylion.png", theirs, width, lines)
                diff = [tuple(min(255, abs(x - y) * 8) for x, y in zip(a, b)) for a, b in zip(ours, theirs)]
                png(args.out / f"{name}_diff.png", diff, width, lines)
    n = totals["pixels"] or 1
    print(f"all tasks: {100 * totals['differ'] / n:.2f}% of pixels differ, "
          f"{100 * totals['beyond_one_step'] / n:.2f}% by more than one step")


if __name__ == "__main__":
    main()
