"""tools/n64_record_seeds.py: the first argument of each break hit, as an unsigned 32-bit number."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "n64_record_seeds.py"


def test_the_seeds_are_the_first_arguments_as_unsigned_numbers():
    spec = importlib.util.spec_from_file_location("n64_record_seeds", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("n64_record_seeds", module)
    spec.loader.exec_module(module)

    class Hit:
        def __init__(self, first):
            self.args = (first, 0, 0, 0)

    assert module.seeds([Hit(577813552), Hit(-1), Hit(1 << 33 | 5)]) == [577813552, 4294967295, 5]
