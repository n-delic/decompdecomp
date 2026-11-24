"""tools/n64_frame_scan.py: the frames a port saved, searched for static, flat colour and runs of black."""

from __future__ import annotations

import importlib.util
import random
import struct
import zlib
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "n64_frame_scan.py"


def _tool():
    spec = importlib.util.spec_from_file_location("n64_frame_scan", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png(path: Path, pixels: list[bytes], width: int, height: int) -> None:
    raw = b"".join(b"\0" + b"".join(pixels[y * width:(y + 1) * width]) for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_static_flat_and_black_frames_are_told_apart_from_a_scene(tmp_path):
    tool = _tool()
    w, h = 64, 48
    rng = random.Random(1)
    gradient = [bytes([x * 3, y * 4, 90]) for y in range(h) for x in range(w)]
    static = [bytes(rng.randrange(256) for _ in range(3)) for _ in range(w * h)]
    flat = [bytes([60, 180, 20])] * (w * h)
    black = [bytes(3)] * (w * h)
    for n, pixels in enumerate([black, black, gradient, static, flat, black, gradient], start=1):
        _png(tmp_path / f"frame_{n:05d}.png", pixels, w, h)
    (tmp_path / "swaps.log").write_text("".join(f"frame {n} task {n * 5} retrace {n}\n" for n in range(1, 8)))
    found = tool.scan(tmp_path, tmp_path / "swaps.log")
    assert found["frames"] == 7
    assert found["black"] == [(1, 2), (6, 6)]
    assert [n for n, _, _ in found["noisy"]] == [4] and found["noisy"][0][2] == 20
    assert found["flat"] == [(5, 25)]  # the green leftovers, not black


def test_a_streaked_strip_over_black_is_found_and_a_scene_or_letterbox_is_not(tmp_path):
    tool = _tool()
    w, h = 64, 48
    rng = random.Random(2)
    # a texture read with the wrong stride: every row one colour run, rows unrelated, only the top fifth drawn
    streak = []
    for y in range(h):
        colour = bytes([rng.randrange(40, 255), rng.randrange(40, 255), rng.randrange(40, 255)])
        streak += [colour if y < 10 else bytes(3)] * w
    scene = [bytes([x * 3, y * 4, 90]) for y in range(h) for x in range(w)]
    letterbox = [p if 6 <= i // w < h - 6 else bytes(3) for i, p in enumerate(scene)]
    for n, pixels in enumerate([scene, streak, letterbox], start=1):
        _png(tmp_path / f"frame_{n:05d}.png", pixels, w, h)
    found = tool.scan(tmp_path)
    assert [n for n, _, _ in found["smeared"]] == [2]
    assert [n for n, _, _ in found["partial"]] == [2]
