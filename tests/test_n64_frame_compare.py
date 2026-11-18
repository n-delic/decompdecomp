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
