"""tools/n64_task_diff.py: two display-list traces compared with their address words masked."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _tool():
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("n64_task_diff", TOOLS / "n64_task_diff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_commands_differ_only_in_what_the_game_computed():
    tool = _tool()
    console = ("noise\n8027A0B8 DB060000 00000000\n8027A0C0 DE000000 8009B948\n8027A0C8 FF10013F 00360240\n"
               "8027A340 F64FC5EC 00000230\n")
    port = ("8027C2B8 DB060000 00000000\n8027C2C0 DE000000 8009C000\n8027C2C8 FF10013F 00365600\n"
            "8027C540 F64FC5E8 0000022C\n")
    a, b = tool.commands(console), tool.commands(port)
    assert a[1] == ("DE000000", "PTR") and a[2] == ("FF10013F", "PTR") and len(a) == 4
    assert tool.differences(a, b) == [(3, ("F64FC5EC", "00000230"), ("F64FC5E8", "0000022C"))]
    assert tool.differences(a, b[:3]) == [(3, ("F64FC5EC", "00000230"), ("-", "-"))]
