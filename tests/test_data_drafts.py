"""Machine drafts of the game's data (app/data_drafts.py), on test_data_units' homemade N64 project: a table at
DATA ({boot, boot, 0, 0}) and a count of 7 at DATA+0x10 up to .bss. What the game's code does with each datum is
given directly (a structs.Structs stand-in), so each rule is tested on its own; drafts are judged with KMC GCC."""
import struct
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import data_drafts, data_units
from decompnet.structs import FieldGuess
from tests.test_data_units import DATA, build


def field(offset, size, kind="s", others=None, points_to=None):
    return FieldGuess(offset, size, kind, {1}, others or {}, points_to)


@pytest.fixture
def project(tmp_path):
    net = build(tmp_path)
    net.code_uses = {}  # address -> the fields the code reads and writes there

    class Found:
        def type_of(self, key):
            return key[1] if key[1] in net.code_uses else None

        def layout(self, type_id):
            return SimpleNamespace(fields=net.code_uses[type_id])
    net.structs = lambda proj: Found()
    yield net
    if hasattr(net, "autosave"):
        net.autosave.close()


def draft(net, address):
    with net.progress.view():
        proj = net.project_row("game")
        span = data_units.unit(net, "game", f"D_{address:08X}")["span"]
        return data_drafts.typed(net, proj, address, span, data_units.reader(net, proj)(address, span))


def test_what_the_code_says_becomes_the_type(project):
    net = project
    # nothing reads the table directly, but every word is a function or empty: a table of function pointers
    source, _ = draft(net, DATA)
    assert source == "extern void boot();\nvoid (*D_80000500[4])(void) = { boot, boot, 0, 0 };"
    net.code_uses[DATA + 0x10] = [field(0, 4)]  # an indexed s32: an array of them, to where .bss starts
    source, _ = draft(net, DATA + 0x10)
    assert source == "s32 D_80000510[12] = { 7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 };"
    net.code_uses[DATA + 0x10] = [field(0, 4, "f")]
    assert draft(net, DATA + 0x10)[0].startswith("f32 D_80000510[12] = { 9.80908925e-45f, 0.0f,")


def test_a_struct_with_pointers_and_what_isn_t_drafted(project):
    net = project
    net.code_uses[DATA] = [field(0, 4, points_to=7), field(4, 4, points_to=7), field(8, 4), field(12, 2, "u")]
    source, _ = draft(net, DATA)
    assert source.endswith("struct { void *f_000; void *f_004; s32 f_008; u16 f_00C; } D_80000500 "
                           "= { boot, boot, 0, 0 };")
    net.code_uses[DATA] = [field(0, 4, others={"f32": {2}})]
    assert draft(net, DATA) == (None, "the code reads it two ways")
    net.code_uses[DATA + 0x10] = [field(4, 4)]  # the 7 at +0x0 is read by nothing the reader follows
    assert draft(net, DATA + 0x10) == (None, "bytes +0x0..+0x4 aren't read by the code")
    net.code_uses[DATA + 0x10] = [field(0, 4, points_to=7)]  # a "pointer" holding 7: not an address
    assert draft(net, DATA + 0x10)[0] is None


def test_values_are_written_exactly():
    assert data_drafts._literal("f32", struct.pack(">f", 1.5), "big") == "1.5f"
    assert data_drafts._literal("f64", struct.pack(">d", 2.0), "big") == "2.0"
    assert data_drafts._literal("s32", (0x80000000).to_bytes(4, "big"), "big") == "(-2147483647 - 1)"
    assert data_drafts._literal("u32", (0xFFFFFFFF).to_bytes(4, "big"), "big") == "4294967295U"
    assert data_drafts._literal("s16", (0xFFFE).to_bytes(2, "big"), "big") == "-2"
    assert data_drafts._literal("f32", struct.pack(">f", float("nan")), "big") is None


def test_the_drafts_match_and_agents_see_what_the_code_does(project):
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")
    net = project
    net.code_uses[DATA] = [field(0, 4, points_to=7), field(4, 4, points_to=7), field(8, 4), field(12, 2, "u")]
    net.code_uses[DATA + 0x10] = [field(0, 4)]
    lines = []
    counts = data_drafts.run(net, "game", lines.append)
    assert counts["matched"] == 2 and counts["tried"] == 2, lines
    with net.progress.view():
        assert data_units.unit(net, "game", "D_80000500")["status"] == "matched"
        text = data_units.unit_text(data_units.unit(net, "game", "D_80000510"))
    assert "The game's code uses it as: +0x0 s32 (1 function)" in text
