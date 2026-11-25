"""A switch whose jump table is in data no splat segment lists keeps its cases in its function (rom/split.py)."""
import struct
from pathlib import Path

from decompnet.rom import split
from decompnet.rom.split import AsmLine, RomFunction, Segment

VRAM, ROM_START = 0x80400000, 0x1000
TABLE = VRAM + 0x1000  # (data after the code: no segment's vram range holds it)
DELTA = ROM_START - VRAM


def line(vram, operands=""):
    return AsmLine(vram - VRAM + ROM_START, vram, 0, "nop", operands)


def function(name, vram, count, operands=""):
    lines = [line(vram + 4 * i, operands if i == 0 else "") for i in range(count)]
    return RomFunction(name, vram, vram - VRAM + ROM_START, "code_80400000", lines, Path(name + ".s"))


def image(words, stripped):
    data = bytearray(0x4000)
    for i, word in enumerate(words):
        struct.pack_into(">I", data, TABLE + DELTA + 4 * i, word & 0x7FFFFFFF if stripped else word)
    return bytes(data)


def merged(stripped):
    segments = [Segment("code_80400000", ROM_START, ROM_START + 0x400, VRAM)]
    head = function("head", VRAM, 4, "jtbl_80401000")
    cases = [function(f"case{i}", VRAM + 0x10 + 0x10 * i, 4) for i in range(4)]
    other = function("other", VRAM + 0x50, 6)
    words = [VRAM + 0x10 + 0x10 * i for i in range(4)]
    return split.merge_switch_cases([head, *cases, other], [], image(words, stripped), True, segments)


def test_cases_of_a_switch_with_an_unlisted_table_return_to_their_function():
    for stripped in (False, True):  # (a table may store its addresses without the KSEG0 bit)
        out = merged(stripped)
        assert [f.name for f in out] == ["head", "other"]
        assert out[0].size == 4 * 4 * 5


def test_without_the_segments_the_cases_stay_apart_as_before():
    segments = None
    head = function("head", VRAM, 4, "jtbl_80401000")
    case = function("case", VRAM + 0x10, 4)
    assert len(split.merge_switch_cases([head, case], [], image([VRAM + 0x10], False), True, segments)) == 2


def test_words_that_are_not_addresses_in_the_segment_are_not_a_table():
    segments = [Segment("code_80400000", ROM_START, ROM_START + 0x400, VRAM)]
    head = function("head", VRAM, 4, "jtbl_80401000")
    case = function("case", VRAM + 0x10, 4)
    junk = image([0x12345678, 0xDEADBEEF, 0x1, 0x2], False)
    assert len(split.merge_switch_cases([head, case], [], junk, True, segments)) == 2
