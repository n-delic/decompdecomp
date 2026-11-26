"""The reversing emulator (consoles/n64/emulator): its pinned build, the frontend's report, RDRAM comparisons, and
a real run on the user's own ROM when the tool is installed and DECOMP_TEST_N64_ROM names one (CI has neither)."""
import os
import stat
import sys

import pytest

from decompnet import toolchains
from decompnet.consoles.n64 import emulator


def test_the_emulator_is_pinned_and_built_from_source():
    tc = toolchains.get("mupenre")
    assert tc.build == "mupenre" and "cmake" in tc.requires and tc.binary_name == "mupenre-frontend"
    intos = [p["into"] for p in tc.parts]
    assert intos == ["source/core", "source/core/libRetroReversing", "source/rsp-hle", "source/sdl2", "source/libpng"]
    for part in tc.parts:
        assert len(part["sha256"]) == 64 and part["file"].startswith("mupenre-")
        assert "windows-x86_64" not in part["platforms"]  # the frontend uses dlopen: Linux and macOS only
    for name in ("frontend.c", "input.c", "video.c"):
        assert (emulator.HERE / name).is_file()


def test_the_frontends_report_is_read():
    log = ("core: Unable to open rom database file '(null)'.\n"
           "watch 2452 8002DC78 8002DB3C 00000001 00000000 802A4594 00000003 802A4594\n"
           "break 2452 8002E148 80023100 800B3418 802C88EC 800B3450 801C16C0 00000000 00000800 10001C1C 802C7EC0 00000000\n"
           "dumped 2500 /tmp/a b/vi2500.bin\n"
           "framedump 900 /tmp/a b/frame900.bin\n"
           "stopped 2525\n")
    run = emulator.parse(log)
    assert run.vis == 2525 and run.dumps == {2500: emulator.Path("/tmp/a b/vi2500.bin")}
    assert run.frame_dumps == {900: emulator.Path("/tmp/a b/frame900.bin")}
    watch, brk = run.hits
    assert (watch.kind, watch.vi, watch.pc, watch.ra, watch.v0, watch.words) == ("watch", 2452, 0x8002DC78, 0x8002DB3C,
                                                                                0x802A4594, ())
    assert brk.kind == "break" and brk.args[0] == 0x800B3418 and brk.words == (0x800, 0x10001C1C, 0x802C7EC0, 0)
    assert run.errors == ["core: Unable to open rom database file '(null)'."]


def test_differences_group_nearby_words_at_console_addresses():
    a = bytearray(0x1000)
    b = bytearray(a)
    b[0x100:0x104] = b"\x3f\x80\x00\x00"
    b[0x108:0x10C] = b"\x00\x00\x00\x01"  # within 16 bytes: the same range
    b[0x800:0x804] = b"\xbf\x80\x00\x00"
    assert emulator.differences(bytes(a), bytes(b), 0x80000000, 0x80001000) == [
        (0x80000100, 0x8000010C, 2), (0x80000800, 0x80000804, 1)]
    assert emulator.differences(bytes(a), bytes(b), 0x80000400, 0x80001000) == [(0x80000800, 0x80000804, 1)]
    assert emulator.word(bytes(b), 0x80000800) == 0xBF800000


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in frontend is a shell script")
def test_a_run_hands_the_frontend_its_requests_and_keeps_a_cut_short_report(tmp_path, monkeypatch):
    """A stand-in frontend: what the runner passes in, and a run cut short by its timeout (a game that never starts
    its video interface counts no VIs) still returns the hits reported until then."""
    script = tmp_path / "mupenre-frontend"
    script.write_text("#!/bin/sh\n"
                      'echo "args $# $5" >&2\n'
                      'echo "env $EMU_VIS $EMU_WATCH $EMU_BREAK $EMU_DUMP $EMU_8MB" >&2\n'
                      'echo "frames $EMU_FRAME_DUMP $EMU_INPUT_CLOCK" >&2\n'
                      'echo "break 0 80000400 0 0 0 0 0 0 1 2 3 4" >&2\n'
                      'if [ "$EMU_VIS" = 9 ]; then sleep 30; fi\n'
                      'echo "stopped $EMU_VIS" >&2\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(emulator, "installed", lambda: tmp_path)
    monkeypatch.setattr(emulator, "current", lambda folder, log=print: folder)  # never a real install from a test
    rom = tmp_path / "game.z64"
    rom.write_bytes(bytes(64))
    seen = []
    real = emulator.parse
    monkeypatch.setattr(emulator, "parse", lambda log: (seen.append(log), real(log))[1])
    run = emulator.run(rom, 3, watches=[(0x802A45C8, 4)], breaks=[0x8002E148], dumps={2: tmp_path / "d.bin"},
                       expansion_pak=True, timeout=20)
    assert run.vis == 3 and not run.timed_out and run.hits[0].pc == 0x80000400
    assert f"args 5 {rom}" in seen[-1]
    assert f"env 3 2A45C8:4 8002E148 2:{tmp_path / 'd.bin'} 1" in seen[-1]  # watches physical, breaks virtual
    frames = emulator.run(rom, 3, frame_dumps={900: tmp_path / "f.bin"}, input_clock="frames", timeout=20)
    assert not frames.timed_out
    assert f"frames 900:{tmp_path / 'f.bin'} frames" in seen[-1]  # the game's 900th frame, input timed in frames
    cut = emulator.run(rom, 9, timeout=2)
    assert cut.timed_out and cut.vis == 0 and cut.hits[0].words == (1, 2, 3, 4)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in frontend is a shell script")
def test_a_run_keeps_its_saves_out_of_the_users_home_unless_given_a_folder(tmp_path, monkeypatch):
    """The core's own save folder is in the user's home and outlives a run, so one run's EEPROM changed the next."""
    script = tmp_path / "mupenre-frontend"
    script.write_text("#!/bin/sh\n"
                      f'echo "$EMU_SAVES" >> "{tmp_path / 'folders'}"\n'
                      f'ls "$EMU_SAVES" >> "{tmp_path / 'found'}"\n'
                      'echo data > "$EMU_SAVES/game.eep"\n'
                      'echo "stopped $EMU_VIS" >&2\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(emulator, "installed", lambda: tmp_path)
    monkeypatch.setattr(emulator, "current", lambda folder, log=print: folder)
    monkeypatch.setenv("EMU_SAVES", str(tmp_path / "ambient"))
    rom = tmp_path / "game.z64"
    rom.write_bytes(bytes(64))
    for _ in range(2):
        emulator.run(rom, 3, timeout=20)
    first, second = (tmp_path / "folders").read_text().splitlines()
    assert first != second and str(tmp_path / "ambient") not in (first, second)
    assert not os.path.exists(first) and not os.path.exists(second)  # nothing is left behind
    assert (tmp_path / "found").read_text() == ""  # and each run started without a save
    mine = tmp_path / "mine"  # (made when it isn't there)
    for _ in range(2):
        assert emulator.main([str(rom), "--vis", "3", "--saves", str(mine)]) == 0
    assert (tmp_path / "folders").read_text().splitlines()[2:] == [str(mine.resolve())] * 2
    assert (tmp_path / "found").read_text() == "game.eep\n"  # the second found the first's save
    assert (mine / "game.eep").is_file()


ROM = os.environ.get("DECOMP_TEST_N64_ROM", "")


@pytest.mark.skipif(toolchains.installed("mupenre") is None or not os.path.isfile(ROM),
                    reason="needs the mupenre tool and DECOMP_TEST_N64_ROM set to your own N64 ROM")
def test_your_rom_runs_the_same_twice_and_stops_at_its_entry_point(tmp_path):
    entry = int.from_bytes(open(ROM, "rb").read(12)[8:12], "big")
    first = emulator.run(ROM, 60, dumps={60: tmp_path / "a.bin"}, timeout=300)
    again = emulator.run(ROM, 60, dumps={60: tmp_path / "b.bin"}, timeout=300)
    assert first.vis == again.vis == 60
    a, b = (tmp_path / "a.bin").read_bytes(), (tmp_path / "b.bin").read_bytes()
    assert len(a) == 4 << 20 and a == b, "the same run every time (no randomised interrupts)"
    hit = emulator.run(ROM, 2, breaks=[entry], max_hits=1, timeout=300).hits
    assert hit and hit[0].kind == "break" and hit[0].pc == entry


def test_a_runs_output_stays_bounded_and_a_stuck_emulator_is_named():
    # A homemade ROM the core can't run printed "core: not compiled exception" 800,000 times a second, all kept in
    # memory: 1.4 GB a minute, and a test worker that imported it grew to 15 GB.
    from decompnet.consoles.n64.emulator import _Output, parse
    now = [0.0]
    out = _Output(clock=lambda: now[0])
    out.add("core: Unable to open rom database\n")
    for _ in range(_Output.STUCK - 2):
        out.add("core: not compiled exception\n")
    assert out.stuck is None
    out.add("watch 3 80001000 80002000 0 0 0 0 0 1\n")  # (report lines are always kept)
    for _ in range(_Output.STUCK):
        out.add("core: not compiled exception\n")
    assert out.stuck == "core: not compiled exception"
    text = out.text()
    assert len(text) < 1000 and "more times" in text
    assert len(parse(text).hits) == 1

    # A warning printed once a frame repeats as often, but over minutes: not stuck, and still bounded.
    slow = _Output(clock=lambda: now[0])
    for _ in range(3 * _Output.STUCK):
        now[0] += 1 / 60
        slow.add("core: a once-a-frame warning\n")
    assert slow.stuck is None and len(slow.text()) < 200

    noisy = _Output(clock=lambda: now[0])
    for index in range(100_000):
        noisy.add(f"core: distinct message {index}\n")
    noisy.add("stopped 30\n")
    assert len(noisy.text()) < _Output.NOISE + 1000 and parse(noisy.text()).vis == 30


def test_ensure_builds_the_missing_emulator_once_and_retries_a_failure_later(tmp_path, monkeypatch):
    """An import that finds the emulator missing builds it (as objdiff-cli is), so a computer that never built it
    doesn't import worse (loaded_code skipped running the game); a failed build isn't retried per import."""
    from decompnet import toolchains

    monkeypatch.setenv("DECOMP_AUTO_TOOLS", "on")
    monkeypatch.setattr(emulator, "_auto", {"lock": emulator.threading.Lock(), "failed": 0.0})
    state = {"built": False, "calls": 0, "fail": True}
    monkeypatch.setattr(emulator, "installed", lambda: tmp_path if state["built"] else None)

    def fake_ensure(tid, log=print):
        state["calls"] += 1
        if state["fail"]:
            raise toolchains.ToolchainError("no compiler")
        state["built"] = True
    monkeypatch.setattr(toolchains, "ensure", fake_ensure)
    lines = []
    assert emulator.ensure(lines.append) is None and state["calls"] == 1
    assert any("could not be built" in line for line in lines)
    assert emulator.ensure(lines.append) is None and state["calls"] == 1  # not again at once
    state["fail"] = False
    emulator._auto["failed"] = 0.0
    assert emulator.ensure(lines.append) == tmp_path and emulator.ensure(lines.append) == tmp_path
    assert state["calls"] == 2
    monkeypatch.setenv("DECOMP_AUTO_TOOLS", "off")
    state["built"] = False
    assert emulator.ensure(lines.append) is None and state["calls"] == 2
