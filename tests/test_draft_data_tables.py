"""A jump table the symbol list lacks is found from its name and the original bytes (draft_data.py)."""

import struct
from types import SimpleNamespace

import pytest

from decompnet.app import draft_data

BASE = 0x800E1DE8
CASES = (0x800DF894, 0x800DF8D0, 0x800DF90C)
ASM = "".join("/* 1 %08X 00000000 */  nop\n" % address for address in CASES)


def _reader(words):
    data = b"".join(struct.pack(">I", w) for w in words)

    def read(address, size):
        at = address - BASE
        return data[at:at + size] if 0 <= at and at + size <= len(data) else None
    return read


def test_the_table_runs_while_its_words_are_instructions_of_the_function():
    reader = _reader((*CASES, 0x3FF00000, 0x800DF894))  # a double constant follows the table
    found = draft_data.jump_table_extent("jtbl_800E1DE8", reader, set(CASES), ">")
    assert found == (BASE, 12)


def test_a_name_without_an_address_or_a_single_case_is_not_a_table():
    reader = _reader((CASES[0], 0x3FF00000))
    assert draft_data.jump_table_extent("jtbl_800E1DE8", reader, set(CASES), ">") is None
    assert draft_data.jump_table_extent("jtbl_not_an_address", reader, set(CASES), ">") is None
    assert draft_data.jump_table_extent("jtbl_800E1DE8", lambda *_: None, set(CASES), ">") is None


@pytest.mark.parametrize("symbols", [{}, {"jtbl_800E1DE8": SimpleNamespace(address=BASE, size=0, kind="jtbl")}])
def test_read_supplies_a_table_the_symbols_do_not_list_or_list_without_a_size(symbols):
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name=symbols), cfg=lambda _: {},
                          reader=lambda *_: _reader(CASES))
    fn = {"asm": ASM + "lui $at, %hi(jtbl_800E1DE8)\n"}
    lines = draft_data.read(net, {"id": 1}, fn)
    assert lines[1:] == ["dlabel jtbl_800E1DE8", *(".word .L%08X" % w for w in CASES)]


def test_every_table_is_supplied_before_constants_take_the_limit_and_padding_is_dropped():
    """A large N64 state machine had 18 tables after its constants; the 16-symbol limit left m2c without the last
    ones, and with any `jr` unresolved it made no draft at all."""
    tables = {"jtbl_%08X" % (BASE + 0x100 * i): SimpleNamespace(address=BASE + 0x100 * i, size=16, kind="jtbl")
              for i in range(draft_data.MAX_SYMBOLS + 2)}
    constants = {"RO_%08X" % (0x80200000 + 4 * i): SimpleNamespace(address=0x80200000 + 4 * i, size=4, kind="rodata")
                 for i in range(draft_data.MAX_SYMBOLS)}
    words = struct.pack(">4I", *CASES, 0)  # three cases and a padding word counted into each table's size

    def read(address, size):
        return words[:size] if address < 0x80200000 else b"\0\0\0\0"[:size]
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name=tables | constants), cfg=lambda _: {},
                          reader=lambda *_: read)
    fn = {"asm": ASM + "".join("lui $at, %%hi(%s)\n" % name for name in [*constants, *tables])}
    text = "\n".join(draft_data.read(net, {"id": 1}, fn))
    assert all("dlabel " + name in text for name in tables)
    assert ".word 0x00000000" not in text
    assert text.count("dlabel RO_") == draft_data.MAX_SYMBOLS


def test_a_table_after_the_last_code_segment_is_read_at_the_functions_own_offset():
    # A splat config that lists only code leaves the data after it unbacked: the reader finds nothing there, the
    # ROM at the function's own ROM-to-address offset has the table, and it is still checked against the function.
    function = {'vram': 0x80004404, 'rom_offset': 0x5004}
    rom = bytearray(0x100000)
    table_at = BASE - function['vram'] + function['rom_offset']
    cases = (0x80004430, 0x80004454, 0x80004438)
    rom[table_at:table_at + 12] = b"".join(struct.pack(">I", w) for w in cases)
    unbacked = lambda *_: None  # noqa: E731
    read = draft_data.beside_code_reader(bytes(rom), function, unbacked)
    assert draft_data.jump_table_extent("jtbl_800E1DE8", read, set(cases), ">") == (BASE, 12)
    # What a segment backs is still read from it, and an overlay's wrong guess finds no table.
    assert draft_data.beside_code_reader(bytes(rom), function, lambda *_: b'\x01')(0x1000, 4) == b'\x01'
    assert draft_data.jump_table_extent("jtbl_800E1DE8", read, {0x1}, ">") is None
    assert draft_data.beside_code_reader(bytes(rom), {'vram': None, 'rom_offset': 0}, unbacked)(BASE, 4) is None


def test_a_table_stored_without_the_kseg0_bit_still_names_the_functions_instructions():
    """A game that patches its tables as it loads a segment keeps `0x0025A1D8` in the ROM for `0x8025A1D8`: 79 of
    a game's 2,465 open functions had no draft because no word looked like one of their instructions."""
    stripped = tuple(w & 0x7FFFFFFF for w in CASES)
    assert draft_data.jump_table_extent("jtbl_800E1DE8", _reader((*stripped, 0x3FF00000)), set(CASES), ">") == (BASE, 12)
    # a stripped word that is not one of the function's instructions, or a word with the bit already set, is as before
    assert draft_data.case_target(0x00123456, set(CASES)) is None
    assert draft_data.case_target(CASES[0], set(CASES)) == CASES[0]
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name={}), cfg=lambda _: {},
                          reader=lambda *_: _reader(stripped))
    fn = {"asm": ASM + "lui $at, %hi(jtbl_800E1DE8)\n"}
    lines = draft_data.read(net, {"id": 1}, fn)
    assert lines[1:] == ["dlabel jtbl_800E1DE8", *(".word .L%08X" % w for w in CASES)]


def test_a_table_in_the_boot_segments_data_is_found_through_another_segments_offset():
    """A jump table at 0x800AD078 sits after the boot code; the function's own overlay has another ROM offset."""
    segment = lambda start, vram: SimpleNamespace(rom_start=start, vram=vram)  # noqa: E731
    rom = bytearray(0x2000)
    boot = segment(0x100, 0x80000100)
    table_at = 0x900  # BASE is far above this boot image, so give the table an address the boot offset reaches
    address = 0x80000100 + table_at - 0x100
    cases = (0x8025A1D8, 0x8025A1E0)
    rom[table_at:table_at + 8] = b"".join(struct.pack(">I", w & 0x7FFFFFFF) for w in cases)
    net = SimpleNamespace(_rom=lambda _: (bytes(rom), [segment(0x1000, 0x80200000), boot]))
    readers = draft_data.delta_readers(net, {}, {})
    name = "jtbl_%08X" % address
    found = [draft_data.jump_table_extent(name, r, set(cases), ">") for r in readers]
    assert found == [None, (address, 8)]
    assert draft_data.delta_readers(SimpleNamespace(_rom=lambda _: 1 / 0), {}, {}) == []


def test_a_dispatch_through_a_table_named_as_data_is_supplied_under_the_jump_table_name():
    """The splitter named an unlisted table `D_...`: m2c cut the switch because no data came with it."""
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name={}), cfg=lambda _: {},
                          reader=lambda *_: _reader(CASES))
    fn = {"asm": ASM + "lui $at, %hi(D_800E1DE8)\naddu $at, $at, $v0\nlw $v0, %lo(D_800E1DE8)($at)\njr $v0\nnop\n"}
    lines = draft_data.read(net, {"id": 1}, fn)
    assert lines[1:] == ["dlabel jtbl_800E1DE8", *(".word .L%08X" % w for w in CASES)]


def test_a_data_word_that_is_only_read_is_not_taken_for_a_jump_table():
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name={}), cfg=lambda _: {},
                          reader=lambda *_: _reader(CASES))
    fn = {"asm": ASM + "lui $at, %hi(D_800E1DE8)\nlw $v0, %lo(D_800E1DE8)($at)\njr $ra\nnop\n"}
    assert draft_data.read(net, {"id": 1}, fn) == [".section .rodata"]


def test_guarded_adjacent_tables_do_not_absorb_each_others_cases():
    # Each of two contiguous tables targets the same function. The sentinel scan alone joins them, changing the
    # switches and sometimes making m2c see more than one table in a block.
    second = BASE + 12
    words = (*CASES, CASES[2], CASES[1], CASES[0])
    net = SimpleNamespace(symbols=lambda _: SimpleNamespace(by_name={}), cfg=lambda _: {},
                          reader=lambda *_: _reader(words))

    def dispatch(name):
        return (f"sltiu $v0, $v1, 3\nbeqz $v0, .L800DF90C\nsll $v0, $v1, 2\n"
                f"lui $at, %hi({name})\naddu $at, $at, $v0\nlw $v0, %lo({name})($at)\njr $v0\nnop\n")

    fn = {"asm": ASM + dispatch(f"jtbl_{BASE:08X}") + dispatch(f"jtbl_{second:08X}")}
    lines = draft_data.read(net, {"id": 1}, fn)
    assert lines[1:] == [f"dlabel jtbl_{BASE:08X}", *(".word .L%08X" % w for w in CASES),
                         f"dlabel jtbl_{second:08X}", *(".word .L%08X" % w for w in reversed(CASES))]


def test_guarded_table_size_requires_the_same_index_and_a_complete_dispatch():
    asm = ("sltiu $v0, $v1, 3\nbeqz $v0, .L800DF90C\nsll $v0, $v1, 2\n"
           "lui $at, %hi(D_800E1DE8)\naddu $at, $at, $v0\nlw $v0, %lo(D_800E1DE8)($at)\njr $v0\n")
    assert draft_data.guarded_table_sizes(asm) == {"jtbl_800E1DE8": 3}
    assert draft_data.guarded_table_sizes(asm.replace("sll $v0, $v1", "sll $v0, $a0")) == {}
    assert draft_data.guarded_table_sizes(asm.replace("jr $v0", "jr $a0")) == {}
    assert draft_data.guarded_table_sizes(asm.replace("beqz $v0", "bnez $v0")) == {}
    assert draft_data.guarded_table_sizes(asm.replace("addu $at", ".L80000000:\naddu $at")) == {}


def test_guarded_table_size_ignores_conflicting_uses_of_the_same_table():
    asm = ("sltiu $v0,$v1,03\nbeqz $v0,.L800DF90C\nsll $v0,$v1,2\n"
           "lui $at,%hi(jtbl_800E1DE8)\naddu $at,$at,$v0\n"
           "lw $v0,%lo(jtbl_800E1DE8)($at)\njr $v0\n")
    assert draft_data.guarded_table_sizes(asm) == {"jtbl_800E1DE8": 3}
    assert draft_data.guarded_table_sizes(asm + asm.replace(",03", ",04")) == {}


def test_guard_cannot_destroy_its_own_index():
    asm = ("sltiu $v0, $v0, 3\nbeqz $v0, .L800DF90C\nsll $v0, $v0, 2\n"
           "lui $at, %hi(jtbl_800E1DE8)\naddu $at, $at, $v0\n"
           "lw $v0, %lo(jtbl_800E1DE8)($at)\njr $v0\n")
    assert draft_data.guarded_table_sizes(asm) == {}
