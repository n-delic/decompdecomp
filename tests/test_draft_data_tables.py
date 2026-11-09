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
