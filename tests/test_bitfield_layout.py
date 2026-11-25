"""port/wasm/bitfield_layout.py: the sizes of structs before and after the big-endian bitfield rewrite."""

from __future__ import annotations

from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.port.wasm import bitfield_layout
from decompnet.port.wasm.build import IR_FLAGS, _run_quiet, big_endian_bitfields

DUMP = """*** Dumping AST Record Layout
Type: Vec3
  Size:96
  DataSize:96
*** Dumping AST Record Layout
Type: TrackSound
  Size:224
  DataSize:224
*** Dumping AST Record Layout
Type: struct Obj
  Size:1056
"""


def test_clangs_record_layouts_are_read_in_bytes():
    assert bitfield_layout.parse_layouts(DUMP) == {"Vec3": 12, "TrackSound": 28, "struct Obj": 132}


def test_only_records_whose_size_differs_are_reported():
    assert bitfield_layout.changed_records({"A": 28, "B": 4, "C": 8}, {"A": 32, "B": 4, "D": 1}) == [("A", 28, 32)]
    assert bitfield_layout.changed_records(None, {"A": 1}) == []  # a file that doesn't compile can't be judged


def test_top_level_records_are_found_by_their_typedef_or_tag():
    text = ("typedef struct { s32 a; struct { u32 x : 4; } inner; } A;\nstruct B { s32 b; };\n"
            "typedef union { u32 w; } C;\nvoid f(void) { struct { int z; } local; }\n")
    assert bitfield_layout.top_level_records(text) == ["A", "struct B", "C"]


def _zig():
    try:
        return toolchains.binary("zig")
    except Exception:  # noqa: BLE001 - the toolchain isn't installed here
        pytest.skip("zig isn't installed")


STRUCT = ("typedef struct {\n    s32 unk0;\n    u16 unk14;\n    u32 unk16 : 8;\n    u32 unk17 : 8;\n    u32 unk18 : 8;\n"
          "    u32 unk19 : 8;\n    u32 unk1A : 8;\n} TrackSound;\n")
PRELUDE = "typedef signed int s32; typedef unsigned int u32; typedef unsigned short u16;\n"


def _build(tmp_path: Path, rewrite) -> tuple[list[str], list[str]]:
    out = tmp_path / "out"
    (out / "include").mkdir(parents=True)
    (out / "src").mkdir()
    (out / "include" / "common.h").write_text(PRELUDE, encoding="utf-8")
    original = '#include "common.h"\n' + STRUCT
    (out / "src" / "a.c").write_text('#include "common.h"\n' + rewrite(STRUCT), encoding="utf-8")
    logged: list[str] = []
    found = bitfield_layout.check(lambda argv, env=None: _run_quiet(argv, env), _zig(), IR_FLAGS, out, {"a.c": original},
                                  {}, logged.append)
    return found, logged


def test_a_rewrite_that_grows_a_struct_is_warned_about(tmp_path):
    """The old rewrite started every run on a fresh word, and closed it with a zero-width field: the struct grew."""
    broken = lambda text: text.replace(" : 8;\n", " : 8;\n    u32 : 0;\n")  # noqa: E731 - every byte on a word of its own
    found, logged = _build(tmp_path, broken)
    assert found and "TrackSound" in found[0] and logged


def test_the_real_rewrite_keeps_the_size(tmp_path):
    found, logged = _build(tmp_path, big_endian_bitfields)
    assert found == [] and logged == []
