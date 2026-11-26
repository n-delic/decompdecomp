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
from decompnet.network import NetworkError
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


def test_a_picture_is_converted_a_plane_at_a_time_exactly_as_the_slow_way_does():
    import random

    from decompnet.port import frame_compare as fc
    rng = random.Random(7)
    mem = bytes(rng.randrange(256) for _ in range(4 * 64 * 8))
    for siz in (2, 3):
        slow = fc.rgb(mem, 16, 32, siz, 5)
        assert fc.rgb_bytes(mem, 16, 32, siz, 5) == bytes(c for p in slow for c in p)


class FakePort(http.server.BaseHTTPRequestHandler):
    inputs: list = []
    ram = b""  # served at /ram?vi=1 (lockstep)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/ram"):
            body = FakePort.ram if self.path.endswith("vi=1") else b""
        else:
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


def test_differing_blocks_are_found_inside_the_ranges_asked_for_and_described():
    a, b = bytearray(0x2000), bytearray(0x2000)
    b[0x100:0x104] = b"\x00\x00\x00\x2A"  # one block in the first page
    b[0x1100:0x1104] = b"\xDE\xAD\xBE\xEF"  # one in the second
    assert sbs.differing_blocks(bytes(a), bytes(b)) == [0x100, 0x1100]
    assert sbs.differing_blocks(bytes(a), bytes(b), [(0x1000, 0x2000)]) == [0x1100]
    lines = sbs.regions([0x100, 0x1100], bytes(a), bytes(b))
    assert lines[0].startswith("80000100-80000200  first 80000100: 00000000 vs 0000002A")
    assert lines[1].startswith("80001100-80001200  first 80001100: 00000000 vs DEADBEEF")
    assert sbs.regions([0x100, 0x200], bytes(a), bytes(b))[0].startswith("80000100-80000300")  # neighbours are one region


def test_the_emulator_parks_at_the_gate_and_its_retrace_ram_is_read_whole(tmp_path):
    shared = live.Shared(tmp_path / "live.bin", lockstep=True)
    assert shared._word(live.GATE) == 0 and shared.parked() == 0 and shared.retrace_ram(1) is None
    m = shared.map
    m[live.VIRAM:live.VIRAM + 4] = b"\x80\x00\x00\x10"
    m[live.VIVI:live.VIVI + 4] = (3).to_bytes(4, live.sys_order)
    m[live.VISEQ:live.VISEQ + 4] = (2).to_bytes(4, live.sys_order)
    assert shared.parked() == 3 and shared.retrace_ram(3)[:4] == b"\x80\x00\x00\x10" and shared.retrace_ram(2) is None
    shared.set_gate(3)
    assert shared._word(live.GATE) == 3
    assert live.Shared(tmp_path / "free.bin")._word(live.GATE) == live.FREE  # not in lockstep: never waits
    shared.close()


def test_a_tap_stays_down_for_a_few_retraces():
    class Quiet:
        shared = type("S", (), {"set_pad": lambda *a: None})()
        port_url = "http://127.0.0.1:9"
    ls = sbs.Lockstep.__new__(sbs.Lockstep)
    ls.session, ls.held, ls.pad, ls.sent_pad = Quiet(), {}, (0, 0, 0), None
    ls._port = lambda *a, **k: b""
    ls.press(0x8000, 0, 0)
    ls.press(0, 0, 0)  # released before the next retrace
    sent = []
    for _ in range(6):
        ls._apply_pad()
        sent.append(ls.sent_pad[0])
    assert sent[:sbs.Lockstep.TAP] == [0x8000] * sbs.Lockstep.TAP and sent[-1] == 0


def test_lockstep_compares_both_rams_at_a_retrace_then_lets_both_go_on(tmp_path, monkeypatch):
    ram = bytearray(0x800000)
    ram[0x400:0x404] = b"\x00\x00\x00\x07"
    FakePort.ram, FakePort.inputs = bytes(ram), []
    port = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakePort)
    threading.Thread(target=port.serve_forever, daemon=True).start()

    class Quiet:
        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(live, "start", lambda *a, **k: Quiet())
    monkeypatch.setattr(sbs, "ConsoleView", StubConsole)
    session = sbs.Session(tmp_path / "rom.z64", f"http://127.0.0.1:{port.server_address[1]}/", tmp_path, lockstep=True, rate=200)
    try:
        m = session.shared.map  # the emulator, parked at retrace 1 with a RAM that differs at 0x400 only
        m[live.VIVI:live.VIVI + 4] = (1).to_bytes(4, live.sys_order)
        m[live.VISEQ:live.VISEQ + 4] = (2).to_bytes(4, live.sys_order)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not session.lockstep.regions:
            time.sleep(0.05)
        assert session.lockstep.vi == 1 and session.lockstep.first_vi == 1 and session.lockstep.blocks == 1
        assert session.lockstep.regions[0].startswith("80000400-80000500  first 80000400: 00000007 vs 00000000")
        with urllib.request.urlopen(session.url + "state", timeout=5) as r:
            assert b'"lockstep": true' in r.read()
        assert "/gate?to=1" in FakePort.inputs and session.shared._word(live.GATE) == 1  # both let go on
    finally:
        session.close()
        port.shutdown()


def test_addresses_are_named_from_the_symbols_and_the_difference_list_says_so(tmp_path):
    symbols = sbs.Symbols([(0x80000100, 0x40, "gScore"), (0x80000200, 0, "label")])
    assert symbols.name(0x80000100) == "gScore" and symbols.name(0x8000010C) == "gScore+0xC"
    assert symbols.name(0x80000140) == "" and symbols.name(0x800000FF) == ""  # outside every entry
    assert symbols.name(0x800002F0) == "label+0xF0" and symbols.name(0x80000300) == ""  # no size: 0x100 reach
    listing = tmp_path / "symbol_addrs.txt"
    listing.write_text("gHealth = 0x80000400; // size:0x8\nbare = 0x80000500;\nnot a symbol\n")
    from_file = sbs.Symbols.from_file(listing)
    assert from_file.name(0x80000404) == "gHealth+0x4" and from_file.name(0x80000510) == "bare+0x10"
    a, b = bytearray(0x400), bytearray(0x400)
    b[0x100:0x104] = b"\x00\x00\x00\x01"
    line = sbs.regions([0x100], bytes(a), bytes(b), symbols=symbols)[0]
    assert "first 80000100 (gScore): 00000000 vs 00000001" in line
    assert "(" not in sbs.regions([0x100], bytes(a), bytes(b))[0].split("first")[1].split(":")[0]  # none known: no name


def test_the_port_and_emulator_have_the_same_memory(tmp_path, monkeypatch):
    seen = {}

    class Proc:
        def poll(self):
            return None

        def kill(self):
            pass

    def popen(argv, **kw):
        seen.update(kw["env"])
        return Proc()

    monkeypatch.setattr(sbs.subprocess, "Popen", popen)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: type("R", (), {"close": lambda s: None})())
    sbs.start_port(tmp_path / "game", tmp_path / "rom.z64", tmp_path, lockstep=True)
    assert seen["PORT_MEMSIZE"] == "4" and seen["PORT_LOCKSTEP"] == "1" and seen["PORT_VTIME"] == "1"
    sbs.start_port(tmp_path / "game", tmp_path / "rom.z64", tmp_path, expansion_pak=True)
    assert seen["PORT_MEMSIZE"] == "8"


def test_the_shared_files_layout_matches_the_c_header_and_nothing_overlaps():
    """live.h (the plugins') and live.py (the viewer's) describe one file: the same numbers, and no two fields on the
    same bytes (the retrace block's counters once sat inside the task's 64 bytes, which the video plugin overwrote)."""
    import re

    header = (Path(live.__file__).with_name("live.h")).read_text()
    c: dict[str, int] = {}
    for m in re.finditer(r"#define LIVE_(\w+) +([^\n/]+)", header):  # (in order: a define may use the ones before it)
        c[m.group(1)] = eval(re.sub(r"(\d)u\b", r"\1", re.sub(r"\bLIVE_(\w+)", r"c['\1']", m.group(2))), {"c": c})
    for name in ("SEQ", "FRAMES", "VI", "GATE", "TASK", "VISEQ", "VIVI", "AUDIO_HZ", "AUDIO_END", "INPUT"):
        assert c[name] == getattr(live, name), name
    for name in ("HEADER", "VIRAM", "AUDIO", "TOTAL", "RING"):
        assert c[name] == getattr(live, name), name
    spans = {"SEQ": 4, "FRAMES": 4, "VI": 4, "GATE": 4, "TASK": 64, "VISEQ": 4, "VIVI": 4, "AUDIO_HZ": 4,
             "AUDIO_END": 4, "INPUT": 4}
    used = sorted((getattr(live, n), getattr(live, n) + size, n) for n, size in spans.items())
    for (_, end, a), (start, _, b) in zip(used, used[1:]):
        assert end <= start, f"{a} and {b} overlap"
    assert used[-1][1] <= live.HEADER


def test_the_consoles_sound_is_served_in_the_ports_format_from_the_ring(tmp_path):
    shared = live.Shared(tmp_path / "live.bin")
    assert shared.audio(-1)[16:] == b"" and struct.unpack("<I", shared.audio(-1)[:4])[0] == 22050  # nothing yet
    ring = [(i + 1) & 0xFFFF | (-(i + 1) & 0xFFFF) << 16 for i in range(10)]  # left 1..10, right -1..-10
    for i, word in enumerate(ring):
        shared.map[live.AUDIO + 4 * i:live.AUDIO + 4 * i + 4] = word.to_bytes(4, live.sys_order)
    shared.map[live.AUDIO_HZ:live.AUDIO_HZ + 4] = (32000).to_bytes(4, live.sys_order)
    shared.map[live.AUDIO_END:live.AUDIO_END + 4] = (10).to_bytes(4, live.sys_order)
    hz, first, _ = struct.unpack("<IQI", shared.audio(0)[:16])
    samples = struct.unpack("<20h", shared.audio(0)[16:])
    assert (hz, first) == (32000, 0) and samples[:4] == (1, -1, 2, -2) and len(samples) == 20
    assert len(shared.audio(4)[16:]) == 6 * 4  # only what's new
    # the ring has wrapped: the newest frames are read across its end
    end = live.RING + 3
    shared.map[live.AUDIO_END:live.AUDIO_END + 4] = end.to_bytes(4, live.sys_order)
    shared.map[live.AUDIO + 4 * (live.RING - 1):live.AUDIO + 4 * live.RING] = (7).to_bytes(4, live.sys_order)
    tail = shared.audio(end - 4)
    assert struct.unpack("<IQI", tail[:16])[1] == end - 4 and len(tail[16:]) == 16
    assert struct.unpack("<2h", tail[16:20])[0] == 7  # frame end-4 is the ring's last word
    shared.close()


def test_a_screenshot_is_saved_as_a_new_png_and_only_from_the_page_itself(tmp_path, monkeypatch):
    import json
    import urllib.error

    png = b"\x89PNG\r\n\x1a\n" + b"data"
    first = sbs.save_screenshot(png, tmp_path / "shots", "My Game!")
    second = sbs.save_screenshot(png, tmp_path / "shots", "My Game!")
    assert first != second and first.read_bytes() == second.read_bytes() == png
    assert first.name.startswith("side-by-side-my-game-") and first.suffix == ".png"
    with pytest.raises(NetworkError):
        sbs.save_screenshot(b"not a png", tmp_path)

    class Quiet:
        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(live, "start", lambda *a, **k: Quiet())
    monkeypatch.setattr(sbs, "ConsoleView", StubConsole)
    session = sbs.Session(tmp_path / "rom.z64", "http://127.0.0.1:9/", tmp_path / "work", title="g",
                          screenshots=tmp_path / "downloads")
    try:
        def post(body, headers):
            request = urllib.request.Request(session.url + "screenshot", data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=5) as r:
                return json.loads(r.read())
        saved = Path(post(png, {"X-Side-By-Side": "1"})["path"])
        assert saved.parent == tmp_path / "downloads" and saved.read_bytes() == png
        with pytest.raises(urllib.error.HTTPError):  # another site's page can't send the header without asking
            post(png, {})
        with pytest.raises(urllib.error.HTTPError):
            post(b"not a png", {"X-Side-By-Side": "1"})
        assert len(list((tmp_path / "downloads").iterdir())) == 1
    finally:
        session.close()


def test_a_slowed_port_goes_on_for_a_share_of_the_consoles_retraces_and_a_held_one_waits():
    port_gates, emu_gates, pads = [], [], []

    class Shared:
        def set_gate(self, n):
            emu_gates.append(n)
            if len(emu_gates) == 8:
                ls.set_speed(0)
            if len(emu_gates) == 12:
                ls.stop = True

    class Quiet:
        shared = Shared()

    ls = sbs.Lockstep.__new__(sbs.Lockstep)
    ls.session, ls.interval, ls.offset, ls.stop = Quiet(), 0.0, 0, False
    ls.running, ls.stepping, ls.speed, ls.port_vi, ls.frame, ls.note = True, False, 0.5, 1, None, ""
    ls.history, ls.sent_pad = [], None
    asked = []
    ls._wait = lambda n, give_up=30.0, port=None, port_vi=None: (asked.append((n, port_vi, port is not None)), (b"p", b"e"))[1]
    ls._compare = lambda *a: None
    ls._apply_pad = lambda counts=True: pads.append(counts)

    def port(path, post=False, timeout=10):
        if path.startswith("/gate"):
            port_gates.append(int(path.split("=")[1]))
        return b""

    ls._port = port
    ls._run()
    assert emu_gates == list(range(1, 13))  # the console goes on every retrace
    assert port_gates == [1, 2, 3, 4]  # half as often, then not at all while held
    assert asked[:3] == [(1, 1, False), (2, 1, True), (3, 2, False)]  # a port kept waiting isn't read again
    assert pads[:4] == [False, True, False, True] and all(pads[8:])  # taps count the port's retraces, the console's if held


def test_the_speed_asked_for_is_kept_between_held_and_full():
    ls = sbs.Lockstep.__new__(sbs.Lockstep)
    for asked, kept in ((0.75, 0.75), (3, 1.0), (-1, 0.0), (float("nan"), 1.0)):
        ls.set_speed(asked)
        assert ls.speed == kept


class Both:
    """Both sides as the stepping thread sees them: what each was given, in order."""

    def __init__(self):
        self.log, self.port_vi, self.gate = [], 1, 0
        self.shared = self
        self.saved = None

    # the console's shared file
    def set_gate(self, n):
        self.gate = n
        self.log.append(("console", n))

    def set_pad(self, *pad):
        self.log.append(("pad", pad))

    def parked(self):
        return self.gate + 1

    # the session
    def restart(self, mark):
        self.log, self.port_vi, self.gate = [], 1, 0

    def port(self, path, post=False, timeout=10):
        if path.startswith("/gate"):
            self.log.append(("port", int(path.split("=")[1])))
            self.port_vi += 1
        elif path == "/state":
            return b'{"parked": %d}' % self.port_vi
        elif path.startswith("/input"):
            self.log.append(("input", path))
        return b""


def _stepper(both):
    ls = sbs.Lockstep.__new__(sbs.Lockstep)
    ls.session, ls.offset, ls.stop, ls.interval = both, 0, False, 0.0
    ls.n = ls.p = 1
    ls.history, ls.sent_pad, ls.pad, ls.held, ls.speed = [], None, (0, 0, 0), {}, 1.0
    ls.lock, ls.frame, ls.number, ls.first_vi, ls.note = threading.Condition(), None, 0, 0, ""
    ls.running, ls.stepping = True, False
    ls._port = both.port
    ls._wait = lambda n, give_up=30.0, port=None, port_vi=None: (bytes(512), bytes(512))
    ls._compare, ls.new = lambda *a: None, []
    return ls


def test_a_replay_gives_both_sides_what_they_were_given_retrace_by_retrace():
    both = Both()
    ls = _stepper(both)
    for step in range(40):
        if step == 5:
            ls.press(0x1000, 0, 0)  # Start, tapped
            ls.press(0, 0, 0)
        if step == 20:
            ls.press(0x8000, 40, -3)
        advance = not 25 <= step < 30 and step % 7 != 3  # held for a while, and slowed now and then
        ls._apply_pad(counts=advance)
        assert ls._go_on(advance)
    lived, history = list(both.log), [list(run) for run in ls.history]
    assert sum(run[4] for run in history) == 40 and len(history) < 40
    same = sbs.fingerprint(bytes(512))
    ls.running = True
    ls._replay({"history": history, "first_vi": 7, "port": same, "console": same})
    assert both.log == lived
    assert (ls.n, ls.history, ls.first_vi, ls.running) == (41, history, 7, False)  # paused there, and bookmarkable again
    assert "exactly as they were" in ls.message and ls.new == []


def test_a_replay_says_which_side_is_not_as_it_was_and_the_page_is_ignored_meanwhile():
    both = Both()
    ls = _stepper(both)
    ls._replay({"history": [[0, 0, 0, 1, 3]], "port": sbs.fingerprint(b"\1" * 512), "console": sbs.fingerprint(bytes(512))})
    assert "port (2 blocks differ)" in ls.message and "console" not in ls.message
    ls.replaying = {"done": 0, "total": 1}
    ls.press(0x8000, 0, 0)
    assert ls.pad == (0, 0, 0) and not ls.held
    with pytest.raises(NetworkError):
        ls.ask("save")
    ls.ask("stop")
    assert ls.cancel


def test_bookmarks_are_kept_one_a_file_and_only_the_listed_ones_are_touched(tmp_path):
    marks = sbs.Bookmarks(tmp_path / "bookmarks")
    assert marks.list() == []
    one, two = marks.add({"name": "retrace 5"}), marks.add({"name": "retrace 9"})
    (tmp_path / "bookmarks" / "broken.json").write_text("{", encoding="utf-8")
    again = sbs.Bookmarks(tmp_path / "bookmarks")
    assert [m["id"] for m in again.list()] == [two, one] and again.load(one) == {"name": "retrace 5"}
    for ident in ("../secret", "broken", ""):
        with pytest.raises(NetworkError):
            again.load(ident)
    again.delete(one)
    assert [m["name"] for m in again.list()] == ["retrace 9"]


def test_a_port_started_again_runs_on_copies_of_the_saves_the_run_started_from(tmp_path, monkeypatch):
    started, ended = [], []

    class Proc:
        pid = 41

        def poll(self):
            return None

        def kill(self):
            ended.append(self)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(sbs.subprocess, "Popen", lambda argv, **kw: (started.append((argv, kw["env"])), Proc())[1])
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: type("R", (), {"close": lambda s: None})())
    (tmp_path / "game.eep").write_bytes(b"progress")
    port = sbs.Port(Proc(), ["game", "rom"], tmp_path, {})
    (tmp_path / "game.eep").write_bytes(b"later progress")
    url = port.restart(port.saves, tmp_path / "replay")
    env = started[0][1]
    assert url == f"http://127.0.0.1:{env['PORT_LIVE']}/" and port.pid == 41 and ended
    assert Path(env["PORT_SAVE"]).read_bytes() == b"progress"
    assert (tmp_path / "game.eep").read_bytes() == b"later progress"  # the user's own save is left alone
    assert not Path(env["PORT_SRAM"]).exists()  # none then, none now
    port.terminate = lambda: None
    port.closed = True
    with pytest.raises(NetworkError):
        port.restart({}, tmp_path / "replay")
    with pytest.raises(NetworkError):
        sbs.write_saves({"../out": ""}, tmp_path / "w")


def test_the_console_started_again_forgets_its_last_run_and_keeps_its_saves_where_told(tmp_path, monkeypatch):
    shared = live.Shared(tmp_path / "live.bin", lockstep=True)
    shared.set_gate(50)
    shared.map[live.VIVI:live.VIVI + 4] = (50).to_bytes(4, live.sys_order)
    shared.reset()
    assert shared.parked() == 0 and shared._word(live.GATE) == 0
    seen = {}
    monkeypatch.setattr(live, "installed", lambda: tmp_path)
    monkeypatch.setattr(live, "current", lambda folder: folder)
    monkeypatch.setattr(live.limits, "capped", lambda argv: argv)
    monkeypatch.setattr(live.subprocess, "Popen", lambda argv, **kw: (seen.update(kw["env"]), type("P", (), {})())[1])
    live.start(tmp_path / "rom.z64", shared, saves=tmp_path / "saves")
    assert seen["EMU_SAVES"] == str((tmp_path / "saves").resolve()) and seen["EMU_UNPACED"] == "1"
    shared.close()
