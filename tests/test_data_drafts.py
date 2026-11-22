"""Machine drafts of the game's data (app/data_drafts.py), on test_data_units' homemade N64 project: a table at
DATA ({boot, boot, 0, 0}) and a count of 7 at DATA+0x10 up to .bss. What the game's code does with each datum is
given directly (a structs.Structs stand-in), so each rule is tested on its own; drafts are judged with KMC GCC."""
import struct
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import data_drafts, data_units
from decompnet.structs import FieldGuess
from tests.test_data_units import BSS, DATA, build


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


def bss_draft(net, address, span):
    with net.progress.view():
        return data_drafts.typed_bss(net, net.project_row("game"), address, span)


def test_bss_is_typed_from_the_code_alone(project):
    net = project
    at = BSS + 0x20
    assert bss_draft(net, at, 16) == (f"u8 D_{at:08X}[16];", "")  # nothing reads it by a field: a buffer
    net.code_uses[at] = [field(0, 4)]  # an indexed s32: an array to where the next datum starts
    assert bss_draft(net, at, 12) == (f"s32 D_{at:08X}[3];", "")
    assert bss_draft(net, at, 4) == (f"s32 D_{at:08X};", "")
    net.code_uses[at] = [field(0, 4), field(8, 2, "u"), field(12, 4, points_to=7)]
    # the bytes nothing reads are padding, so the definition is the whole datum
    assert bss_draft(net, at, 20) == (f"struct {{ s32 f_000; u8 pad_004[4]; u16 f_008; u8 pad_00A[2]; void *f_00C; "
                                      f"u8 pad_010[4]; }} D_{at:08X};", "")
    net.code_uses[at] = [field(0, 4, others={"f32": {2}})]
    assert bss_draft(net, at, 4) == (None, "the code reads it two ways")
    net.code_uses[at] = [field(0, 4), field(2, 4)]
    assert bss_draft(net, at, 8) == (None, "fields overlap at +0x2")
    net.code_uses[at] = [field(0, 4)]
    assert bss_draft(net, at, 6)[0] is None  # a size no s32 array makes


def test_bss_drafts_match_without_agents(project):
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")
    import json

    from tests.test_data_units import TEXT
    net = project
    with net.store.tx() as db:  # the code refers to two words of .bss, the second a whole 8 bytes after the first
        db.execute("UPDATE functions SET relocs=? WHERE name='boot'", (json.dumps(
            [[0, 5, DATA], [4, 6, DATA], [8, 5, DATA + 0x10], [12, 6, DATA + 0x10], [16, 4, TEXT],
             [20, 5, BSS], [24, 5, BSS + 8]]),))
    net.code_uses[DATA + 0x10] = [field(0, 4)]
    net.code_uses[BSS] = [field(0, 4)]
    lines = []
    counts = data_drafts.run(net, "game", lines.append)
    with net.progress.view():
        first = data_units.unit(net, "game", f"D_{BSS:08X}")
    assert first["status"] == "matched" and "s32 D_80000540[2];" in first["source"], (counts, lines)


def test_text_the_code_never_reads_by_a_field_is_a_string(project):
    net = project
    with net.progress.view():
        proj = net.project_row("game")
        at = DATA + 0x10

        def source(raw):
            return data_drafts.typed(net, proj, at, len(raw), raw)

    assert source(b"Lap %d\0\0\0\0") == ('char D_80000510[10] = "Lap %d";', "")
    # two strings and the zeros between them, quotes, backslashes, newlines and `?` (trigraphs) escaped
    assert source(b'say "hi"\n\0\0ok??\\\0')[0] == r'char D_80000510[17] = "say \"hi\"\n\000\000ok\?\?\\";'
    assert data_drafts._text(b"A\0") is None  # one character: not enough to call it text
    assert data_drafts._text(b"\0\0\0 \0\0\0\0") is None  # a number like 0x20 is not text
    assert data_drafts._text(b"Lap %d") is None  # no terminator: not a string
    assert data_drafts._text(b"caf\xe9\0") is None  # not ASCII
    net.code_uses[at] = [field(0, 1, "u")]  # read as a byte by the code: its field, not a guess about it
    assert not source(b"Lap %d\0\0\0\0")[0].startswith("char")


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
