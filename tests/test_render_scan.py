"""port/render_scan.py: the port run headless, its frames looked through, the findings listed for the Porting tab."""

from __future__ import annotations

import random
import stat
import struct
import zlib
from pathlib import Path

import pytest

from decompnet.network import NetworkError
from decompnet.port import render_scan


def _png(w: int, h: int, pixels: list[bytes]) -> bytes:
    raw = b"".join(b"\0" + b"".join(pixels[y * w:(y + 1) * w]) for y in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _frames(folder: Path) -> None:
    """A scene, then a texture streaked sideways over black, then the scene again."""
    rng = random.Random(3)
    w, h = 64, 48
    scene = [bytes([x * 3, y * 4, 90]) for y in range(h) for x in range(w)]
    streak = []
    for y in range(h):
        colour = bytes([rng.randrange(40, 255) for _ in range(3)])
        streak += [colour if y < 10 else bytes(3)] * w
    for n, pixels in enumerate([scene, streak, scene], start=1):
        (folder / f"frame_{n:05d}.png").write_bytes(_png(w, h, pixels))


def test_findings_lists_each_frame_once_with_every_reason():
    rows = render_scan.findings({"noisy": [(4, 40.0, 9)], "flat": [], "smeared": [(2, 5.5, 3), (4, 4.0, 9)],
                                 "partial": [(2, 0.8, 3)], "black": [], "frames": 5})
    assert [r["frame"] for r in rows] == [2, 4]
    assert rows[0]["kinds"] == ["smeared", "partial"] and rows[0]["task"] == 3
    assert rows[1]["kinds"] == ["noisy", "smeared"]


def test_a_headless_run_is_looked_through_and_the_streaked_frame_flagged(tmp_path):
    """A stand-in for the port's program: it saves the frames and its swap log the way PORT_FRAMES and PORT_SWAP_LOG ask."""
    from decompnet.port import frame_scan

    source = tmp_path / "frames_src"
    source.mkdir()
    _frames(source)
    program = tmp_path / "game"
    program.write_text(
        "#!/bin/sh\n"
        f'cp {source}/frame_*.png "$PORT_FRAMES"/\n'
        'printf "frame 1 task 5 retrace 1\\nframe 2 task 10 retrace 2\\nframe 3 task 15 retrace 3\\n" > "$PORT_SWAP_LOG"\n'
        'test "$PORT_VTIME" = 1 && test "$PORT_INPUT" = "60:START,90:" && exit 0\nexit 3\n')
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out"
    frames = render_scan.run(program, tmp_path / "rom.z64", out, seconds=5, every=1, input="60:START\n90:")
    rows = render_scan.findings(frame_scan.scan(frames, out / "swaps.log"))
    assert [r["frame"] for r in rows] == [2] and rows[0]["task"] == 10
    assert {"smeared", "partial"} <= set(rows[0]["kinds"])


def test_a_game_that_stops_with_an_error_says_where_its_log_is(tmp_path):
    program = tmp_path / "game"
    program.write_text("#!/bin/sh\necho boom\nexit 2\n")
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(NetworkError, match="run.log"):
        render_scan.run(program, tmp_path / "rom.z64", tmp_path / "out", seconds=5, every=1, input="")


def test_summary_is_none_before_a_scan_and_reads_the_saved_one(tmp_path):
    assert render_scan.summary(tmp_path) is None
    path = render_scan.results_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"at": 1, "findings": []}')
    assert render_scan.summary(tmp_path) == {"at": 1, "findings": []}
