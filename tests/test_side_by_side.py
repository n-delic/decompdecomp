"""The side-by-side viewer (port/wasm/side_by_side.py) and the emulator's live mode (consoles/n64/emulator/live.py): the
shared file's seqlock and pad, the viewer's routes against a fake port, and, on the user's own ROM when mupenRE is
installed and DECOMP_TEST_N64_ROM names one (CI has neither), the real console's frames."""
import http.server
import os
import struct
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.consoles.n64.emulator import live
from decompnet.port.wasm import side_by_side as sbs


def test_the_pad_goes_to_the_input_plugin_as_the_core_holds_it():
    # A (0x8000 on the port) is bit 7 of the core's buttons; L (0x0020) is bit 13; the stick sits in the top two bytes
    assert live.pad_value(0x8000, 0, 0) == 0x0080
    assert live.pad_value(0x0020, 0, 0) == 0x2000
    assert live.pad_value(0x1000 | 0x0100, 0, 0) == 0x0010 | 0x0001
    value = live.pad_value(0, -80, 80)
    assert value >> 16 & 0xFF == 0xB0 and value >> 24 == 80


def _write(shared, frames, vi, task, ram, seq):
    m = shared.map
    m[live.SEQ:live.SEQ + 4] = (seq | 1).to_bytes(4, live.sys_order)
    m[live.FRAMES:live.FRAMES + 4] = frames.to_bytes(4, live.sys_order)
    m[live.VI:live.VI + 4] = vi.to_bytes(4, live.sys_order)
    m[live.TASK:live.TASK + 64] = task
    m[live.HEADER:live.HEADER + len(ram)] = ram
    m[live.SEQ:live.SEQ + 4] = (seq + 2).to_bytes(4, live.sys_order)


def test_a_whole_frame_is_read_and_a_half_written_one_is_not(tmp_path):
    shared = live.Shared(tmp_path / "live.bin")
    assert shared.snapshot(tries=1) is None  # nothing written yet
    _write(shared, 7, 30, bytes(range(64)), b"\x80\x00\x00\x10", 0)
    frames, vi, task, ram = shared.snapshot()
    assert (frames, vi, task) == (7, 30, bytes(range(64))) and ram[:4] == b"\x80\x00\x00\x10"
    assert len(ram) == 4 << 20
    shared.map[live.SEQ:live.SEQ + 4] = (3).to_bytes(4, live.sys_order)  # the emulator is mid-write
    assert shared.snapshot(tries=1) is None
    shared.close()


def test_the_pad_is_written_for_the_input_plugin(tmp_path):
    shared = live.Shared(tmp_path / "live.bin")
    shared.set_pad(0x8000, 5, -5)
    value = int.from_bytes(shared.map[live.INPUT:live.INPUT + 4], live.sys_order)
    assert value == 0x0080 | 5 << 16 | 0xFB << 24
    shared.close()


def test_an_image_is_picked_and_turned_into_the_pages_bytes():
    small, big = (2, 2, [(1, 2, 3)] * 4), (4, 3, [(9, 8, 7)] * 12)
    assert sbs.pick_image({1: small, 2: big}) == big and sbs.pick_image({}) is None
    assert sbs.to_rgb([(1, 2, 3), (4, 5, 6)]) == bytes([1, 2, 3, 4, 5, 6])


class FakePort(http.server.BaseHTTPRequestHandler):
    inputs: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = struct.pack("<III", 1, 1, 5) + b"\x01\x02\x03"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        FakePort.inputs.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class StubConsole:
    def __init__(self, *args, **kw):
        self.frame = struct.pack("<III", 1, 1, 9) + b"\x0a\x0b\x0c"

    def wait(self, after, timeout=1.0):
        return self.frame if after != 9 else None

    def close(self):
        pass


def test_the_page_shows_both_halves_and_the_pad_reaches_both(tmp_path, monkeypatch):
    port = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakePort)
    threading.Thread(target=port.serve_forever, daemon=True).start()

    class Quiet:
        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(live, "start", lambda *a, **k: Quiet())
    monkeypatch.setattr(sbs, "ConsoleView", StubConsole)
    session = sbs.Session(tmp_path / "rom.z64", f"http://127.0.0.1:{port.server_address[1]}/", tmp_path)
    try:
        def get(path):
            with urllib.request.urlopen(session.url.rstrip("/") + path, timeout=5) as r:
                return r.read()
        assert b"Console (mupenRE)" in get("/") and b"/emu/frame" in get("/") and b"/port/frame" in get("/")
        assert get("/port/frame?after=-1")[8:12] == (5).to_bytes(4, "little")  # the port's frame, proxied
        assert get("/emu/frame?after=-1")[12:] == b"\x0a\x0b\x0c" and get("/emu/frame?after=9") == b""
        urllib.request.urlopen(urllib.request.Request(session.url + "input?b=32768&x=80&y=0", method="POST"), timeout=5).close()
        assert "/input?b=32768&x=80&y=0" in FakePort.inputs
        assert int.from_bytes(session.shared.map[live.INPUT:live.INPUT + 4], live.sys_order) == 0x0080 | 80 << 16
    finally:
        session.close()
        port.shutdown()


ROM = os.environ.get("DECOMP_TEST_N64_ROM", "")


@pytest.mark.skipif(toolchains.installed("mupenre") is None or not os.path.isfile(ROM),
                    reason="needs the mupenre tool and DECOMP_TEST_N64_ROM set to your own N64 ROM")
def test_the_real_console_runs_live_and_takes_the_pad(tmp_path):
    shared = live.Shared(tmp_path / "live.bin")
    proc = live.start(Path(ROM), shared)
    try:
        deadline, seen = time.monotonic() + 60, None
        while time.monotonic() < deadline and not (seen := shared.snapshot()):
            time.sleep(0.2)
        assert seen is not None and seen[0] > 0 and len(seen[3]) == 4 << 20
        first = seen[0]
        time.sleep(1)
        assert shared.snapshot()[0] > first  # real time: the game goes on
    finally:
        proc.kill()
        shared.close()
