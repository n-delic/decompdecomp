"""n64_frame_scan.py FOLDER: look through the frames a port saved (PORT_FRAMES=FOLDER, PORT_FRAME_EVERY=1) for the ones
that look wrong, without an emulator: noise (a buffer shown before the game drew it, or in the wrong layout, which
compresses and looks like static), flat single-colour frames (an undrawn buffer's leftovers, a dither pattern included) and runs of black frames
(the VI blanked: expected at the start and around a change of video mode, a few frames each).

    PORT_FRAMES=frames PORT_FRAME_EVERY=1 PORT_SWAP_LOG=swaps.log port/wasm/build/game ROM 120
    python tools/n64_frame_scan.py frames [--swap-log swaps.log]

With the swap log, each finding also names the graphics task the frame came after, for
tools/n64_frame_compare.py --frames. Frames are the port's own PNGs (8-bit RGB, no interlace); only the standard library
is used. A detailed scene scores high too (snow, foliage); the noise limit is for static, which scores far higher."""
from __future__ import annotations

import argparse
import re
import struct
import sys
import zlib
from pathlib import Path

NOISE_LIMIT = 25.0  # mean change between neighbouring pixels: scenes score 3 to 15, a mis-shown buffer 40 and up


def read_png(path: Path) -> tuple[int, int, bytes]:
    """(width, height, raw scanlines with their filter bytes) of an 8-bit RGB PNG."""
    data, at, idat, width, height = path.read_bytes(), 8, b"", 0, 0
    while at < len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        chunk = data[at + 8:at + 8 + size]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunk[:13])
            if (depth, colour, interlace) != (8, 2, 0):
                raise ValueError(f"{path.name}: only 8-bit RGB PNGs without interlacing")
        elif kind == b"IDAT":
            idat += chunk
        at += 12 + size
    return width, height, zlib.decompress(idat)


def unfilter(width: int, height: int, raw: bytes) -> list[bytearray]:
    """The scanlines as pixels, undoing PNG's per-line filters."""
    stride, rows, previous = width * 3, [], bytearray(width * 3)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - 3] if i >= 3 else 0
            up = previous[i]
            upleft = previous[i - 3] if i >= 3 else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - upleft
                best = min((abs(p - left), left), (abs(p - up), up), (abs(p - upleft), upleft))[1]
                line[i] = (line[i] + best) & 255
        rows.append(line)
        previous = line
    return rows


def judge(path: Path) -> dict:
    """{"noise": mean difference between neighbouring pixels, "flat": a few colours at most: all zero} of one frame."""
    width, height, raw = read_png(path)
    rows = unfilter(width, height, raw)[::4]
    total = count = 0
    colours = set()
    for row in rows:
        for x in range(0, width * 3 - 3, 12):
            total += abs(row[x] - row[x + 3]) + abs(row[x + 1] - row[x + 4]) + abs(row[x + 2] - row[x + 5])
            count += 3
            colours.add(bytes(row[x:x + 3]))
    noise = total / max(count, 1)
    return {"noise": noise, "flat": len(colours) <= 4 and colours != {b"\0\0\0"}, "black": colours == {b"\0\0\0"}}


def scan(folder: Path, swap_log: Path | None = None) -> dict:
    """{"frames": n, "noisy": [(frame, noise)], "flat": [frames], "black": [(first, last)]} for a folder's frame_NNNNN.png."""
    tasks = {}
    if swap_log and swap_log.is_file():
        for line in swap_log.read_text().splitlines():
            m = re.match(r"frame (\d+) task (\d+)", line)
            if m:
                tasks[int(m[1])] = int(m[2])
    noisy, flat, black, runs = [], [], [], []
    frames = sorted(folder.glob("frame_*.png"))
    for path in frames:
        n = int(path.stem.split("_")[1])
        found = judge(path)
        if found["noise"] > NOISE_LIMIT:
            noisy.append((n, round(found["noise"], 1), tasks.get(n)))
        if found["black"]:
            runs.append(n)
        elif found["flat"]:
            flat.append((n, tasks.get(n)))
    for n in runs:
        if black and black[-1][1] == n - 1:
            black[-1] = (black[-1][0], n)
        else:
            black.append((n, n))
    return {"frames": len(frames), "noisy": noisy, "flat": flat, "black": black}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Find frames a port showed that look wrong (static, flat, black runs).")
    p.add_argument("folder", type=Path)
    p.add_argument("--swap-log", type=Path, help="PORT_SWAP_LOG's file, to name each frame's graphics task")
    args = p.parse_args(argv)
    found = scan(args.folder, args.swap_log)
    print(f"{found['frames']} frames")
    for n, noise, task in found["noisy"]:
        print(f"frame {n}: looks like static (noise {noise})" + (f", after graphics task {task}" if task else ""))
    for n, task in found["flat"]:
        print(f"frame {n}: one flat colour, not black" + (f", after graphics task {task}" if task else ""))
    for first, last in found["black"]:
        print(f"frames {first}-{last}: black ({last - first + 1} frames; expected only at the start and at a change of video mode)")
    return 1 if found["noisy"] or found["flat"] else 0


if __name__ == "__main__":
    sys.exit(main())
