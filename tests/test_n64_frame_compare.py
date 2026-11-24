"""tools/n64_frame_compare.py: the console's frame and the port's drawn from each one's RAM by the port's renderer.

The emulator and a port need a ROM (CI has none), so these check the parts that don't: finding libultra's graphics
tasks in a RAM dump, and drawing one (a homemade display list, tests/n64_render_kit.py) into the image it names."""

from __future__ import annotations

import importlib.util
import shutil
import struct
from pathlib import Path

import pytest

from tests.n64_render_kit import FB, SHADE, Gfx, H, Memory, W, matrix, vertex

TOOL = Path(__file__).resolve().parents[1] / "tools" / "n64_frame_compare.py"


def _tool():
    spec = importlib.util.spec_from_file_location("n64_frame_compare", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _as_task(mem: Memory, task: int) -> None:
    """The kit's task made a whole OSTask: type M_GFXTASK, boot code and microcode somewhere in RAM, 2 KB of data."""
    mem.w32(task, 1)
    mem.w32(task + 8, 0x80000000 | mem.put(bytes(0x100)))
    mem.w32(task + 16, 0x80000000 | mem.put(bytes(0x100)))
    mem.w32(task + 28, 0x800)


def _red_square(mem: Memory) -> int:
    g = Gfx(mem)
    _as_task(mem, g.task)
    g.frame().fill(0)
    g(0xED000000, (W * 4) << 12 | H * 4)
    g(0xDA380007, 0x80000000 | mem.put(matrix()))
    g(0xDA380003, 0x80000000 | mem.put(matrix([0.5, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])))  # half size
    g.othermode(3 << 6, 0)
    g(*SHADE)
    red = 0xFF0000FF
    v = mem.put(b"".join(vertex(x, y, 0, 0, 0, red) for x, y in ((-1, 1), (1, 1), (1, -1), (-1, -1))))
    g(0x01004008, 0x80000000 | v)
    g(0x05000204, 0)
    g(0x05000406, 0)
    g(0xDF000000)
    return g.end()


def test_graphics_tasks_are_found_by_their_shape():
    """An OSTask of type 1 whose pointers are in RAM and whose microcode data is at most 2 KB; the same with an
    audio task's type, or its display list outside RAM, isn't one."""
    tool = _tool()
    mem = Memory()
    task = _red_square(mem)
    ram = bytes(mem.ram)
    assert tool.graphics_tasks(ram) == [task]
    audio = bytearray(ram)
    struct.pack_into(">I", audio, task & 0x7FFFFF, 2)
    assert tool.graphics_tasks(bytes(audio)) == []
    far = bytearray(ram)
    struct.pack_into(">I", far, (task & 0x7FFFFF) + 48, 0x80900000)
    assert tool.graphics_tasks(bytes(far)) == []


@pytest.mark.skipif(not (shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")), reason="needs a C compiler")
def test_a_task_in_a_ram_dump_is_drawn_into_its_image(tmp_path):
    """The task drawn from the dump alone, by the port's renderer: its frame image found from its own commands, the
    square red in the middle and black around it."""
    tool = _tool()
    mem = Memory()
    task = _red_square(mem)
    dump = tmp_path / "ram.bin"
    dump.write_bytes(bytes(mem.ram) + bytes(0x800000 - len(mem.ram)))
    program = tool.build(tmp_path / "work")
    drawn = tool.draw(program, dump, task, tmp_path / "work")
    assert drawn and FB in drawn
    width, lines, pixels = drawn[FB]
    assert width == W
    assert pixels[(H // 2) * W + W // 2] == (248, 0, 0)
    assert pixels[2 * W + 2] == (0, 0, 0)


def test_the_retrace_rate_follows_the_headers_region(tmp_path):
    tool = _tool()
    rom = bytearray(0x1000)
    rom[:4] = b"\x80\x37\x12\x40"
    rom[0x3E] = ord("P")
    (tmp_path / "pal.z64").write_bytes(rom)
    rom[0x3E] = ord("E")
    (tmp_path / "ntsc.z64").write_bytes(rom)
    swapped = b"".join(rom[i:i + 4][::-1] for i in range(0, len(rom), 4))  # .n64: little-endian words
    (tmp_path / "ntsc.n64").write_bytes(swapped)
    assert tool.retraces_per_second(tmp_path / "pal.z64") == 50
    assert tool.retraces_per_second(tmp_path / "ntsc.z64") == 60
    assert tool.retraces_per_second(tmp_path / "ntsc.n64") == 60


def test_dump_task_sidecar_is_placed_with_game_pointers(tmp_path):
    """A game keeps its OSTask wherever it likes, so each dump's own task (`NAME.task`, the emulator's from DMEM with
    physical pointers) is placed in the dump for drawing, its pointers made the game's, instead of scanning RAM."""
    tool = _tool()
    dump = tmp_path / "console_frames_1.bin"
    dump.write_bytes(bytes(0x400000))
    words = [1, 6, 0x1D10, 0xD0, 0x3F90, 0x1000, 0x9B190, 0x800, 0, 0, 0xC83D0, 0xD03D0, 0x1CFD68, 0x40, 0xC60E0, 0xC00]
    (tmp_path / "console_frames_1.bin.task").write_bytes(struct.pack(">16I", *words))
    placed, at = tool.with_task(dump, tmp_path)
    mem = placed.read_bytes()
    assert len(mem) == tool.RAM and at == tool.TASK_AT
    got = struct.unpack_from(">16I", mem, at & 0x7FFFFF)
    assert got[0] == 1 and got[2] == 0x80001D10 and got[6] == 0x8009B190 and got[12] == 0x801CFD68
    assert got[8] == 0 and got[7] == 0x800  # sizes and empty pointers stay as they are
    assert tool.with_task(tmp_path / "console_vi_9.bin", tmp_path) is None  # no sidecar: scan RAM as before


def test_a_shifted_picture_matches_within_a_tolerance_and_a_different_one_does_not():
    """unmatched(): the share of pixels with no equal pixel within `radius` of the other picture."""
    tool = _tool()
    w, h = 16, 8
    a = [((x * 7 + y * 3) % 5, x % 3, y % 4) for y in range(h) for x in range(w)]
    shifted = [a[y * w + min(x + 1, w - 1)] for y in range(h) for x in range(w)]  # the same picture a pixel to the left
    other = [((x * 11 + y) % 9 + 20, 0, 0) for y in range(h) for x in range(w)]
    assert tool.unmatched(a, a, w, h, 0) == 0.0
    assert tool.unmatched(a, shifted, w, h, 0) > 20.0
    assert tool.unmatched(a, shifted, w, h, 1) == 100.0 / w  # all but the column the shift pushed out of the picture
    assert tool.unmatched(a, other, w, h, 2) == 100.0


def test_the_port_is_run_once_through_the_script_to_make_its_save_first(tmp_path):
    """prime_save: one run of the port with the input script writes game.eep before the compared run; a save already there is
    left alone, and the compared run's dump settings aren't passed to the priming run."""
    import os
    import stat

    tool = _tool()
    game = tmp_path / "game"
    game.write_text("#!/bin/sh\necho \"$PORT_INPUT|$PORT_INPUT_CLOCK|$PORT_FRAME_DUMPS\" >> runs.txt\nprintf 'save' > game.eep\n")
    game.chmod(game.stat().st_mode | stat.S_IEXEC)
    rom = tmp_path / "rom.z64"
    rom.write_bytes(b"\0" * 64)
    work = tmp_path / "work"
    work.mkdir()
    os.environ["PORT_FRAME_DUMPS"] = "5:x.bin"
    try:
        assert tool.prime_save(game, rom, "150:A,200:-", work, "frames", seconds=1) is True
        assert (work / "game.eep").read_bytes() == b"save"
        assert tool.prime_save(game, rom, "150:A,200:-", work, "frames", seconds=1) is False  # a save is there: no second run
        assert (work / "runs.txt").read_text() == "150:A,200:-|frames|\n"
    finally:
        os.environ.pop("PORT_FRAME_DUMPS", None)
