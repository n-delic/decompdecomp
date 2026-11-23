"""tools/n64_vi_compare.py: the console's blanked VI intervals from the emulator's log, the port's from its swap log."""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "n64_vi_compare.py"


def _tool():
    spec = importlib.util.spec_from_file_location("n64_vi_compare", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blank_intervals_are_read_and_compared():
    tool = _tool()
    console = tool.console_blank("task 0 status 0000311E origin 000280 width 320 hvideo 00000000\n"
                                 "task 6 status 00013042 origin 27F180 width 640 hvideo 006C02EC\n"
                                 "task 400 status 00013042 origin 27F180 width 640 hvideo 00000000\n"
                                 "task 410 status 00013012 origin 315240 width 320 hvideo 006C02EC\n"
                                 "task 800 status 00013012 origin 315240 width 320 hvideo 00000000\n")
    assert console == [(0, 6), (400, 410), (800, None)]
    swaps = ("frame 1 task 5 retrace 3 buffer 8027F180 black 1\nframe 2 task 10 retrace 4 buffer 8027F180 black 0\n"
             "frame 3 task 402 retrace 9 buffer 80315240 black 1\nframe 4 task 412 retrace 10 buffer 80315240 black 0\n")
    port = tool.port_blank(swaps)
    assert port == [(5, 10), (402, 412)]
    assert tool.differences(console, port, 700) == []
    # where a blank starts may differ between runs (a menu advanced hundreds of tasks apart), its length may not
    assert tool.differences(console, [(305, 310), (402, 412)], 700) == []
    assert tool.differences(console, [(5, 10)], 700) == ["2 blanks on the console, 1 in the port"]
    assert tool.differences([(0, 6)], [(300, 340)], 700) == ["blank 1: console tasks 0-6, port 300-340"]
