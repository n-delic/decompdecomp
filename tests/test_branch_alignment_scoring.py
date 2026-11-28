"""Scoring version 3: a branch to the instruction its counterpart's target pairs with costs nothing, so one instruction
added elsewhere doesn't charge every later branch for moving (a candidate that fixed a whole loop scored 2,175 against a
saved best of 340 because of one instruction that shifted the offsets). A match is still byte-identical."""
import struct

import pytest

from decompnet import scoring
from decompnet.elfobj import FunctionObj


def words(big, *ws):
    return b"".join(struct.pack(">I" if big else "<I", w) for w in ws)


def fn(arch, big, code):
    return FunctionObj("f", arch, big, code)


def both(target, cand):
    """(version 2's score, version 3's) of one pair."""
    scoring.ALIGN_BRANCH_TARGETS = False
    try:
        old = scoring._diff(target, cand)
    finally:
        scoring.ALIGN_BRANCH_TARGETS = True
    return old, scoring._diff(target, cand)


# arch, big-endian, target code, the candidate with one instruction added before the last one (every branch offset
# changes), and the candidate whose branch goes to another instruction than its counterpart's
CASES = {
    "mips": ("mips", True,
             words(True, 0x10800003, 0, 0x24A50001, 0x24C60001, 0x03E00008, 0),
             words(True, 0x10800004, 0, 0x24A50001, 0x24C60001, 0x24420001, 0x03E00008, 0),
             words(True, 0x10800002, 0, 0x24A50001, 0x24C60001, 0x24420001, 0x03E00008, 0)),
    "ppc": ("ppc", True,
            words(True, 0x4800000C, 0x38630001, 0x38840001, 0x4E800020),
            words(True, 0x48000010, 0x38630001, 0x38840001, 0x38A50001, 0x4E800020),
            words(True, 0x48000008, 0x38630001, 0x38840001, 0x38A50001, 0x4E800020)),
    "x86": ("x86", False,
            bytes.fromhex("eb02" "40" "41" "c3"),
            bytes.fromhex("eb03" "40" "41" "42" "c3"),
            bytes.fromhex("eb01" "40" "41" "42" "c3")),
    "arm": ("arm", False,
            words(False, 0xEA000001, 0xE2800001, 0xE2811001, 0xE12FFF1E),
            words(False, 0xEA000002, 0xE2800001, 0xE2811001, 0xE2822001, 0xE12FFF1E),
            words(False, 0xEA000000, 0xE2800001, 0xE2811001, 0xE2822001, 0xE12FFF1E)),
}


@pytest.mark.parametrize("kind", CASES)
def test_a_shifted_branch_costs_nothing_but_the_instruction_that_shifted_it(kind):
    arch, big, target, shifted, _ = CASES[kind]
    old, new = both(fn(arch, big, target), fn(arch, big, shifted))
    assert new.score == scoring.PENALTY_INSERTION  # the one extra instruction, nothing else
    assert old.score == scoring.PENALTY_INSERTION + scoring.PENALTY_OPERAND  # the old one charged the branch too
    assert not new.bytes_match and new.score > 0  # never a match: the bytes differ


@pytest.mark.parametrize("kind", CASES)
def test_a_branch_to_another_instruction_is_still_charged(kind):
    arch, big, target, _, wrong = CASES[kind]
    old, new = both(fn(arch, big, target), fn(arch, big, wrong))
    assert new.score == old.score > scoring.PENALTY_INSERTION


@pytest.mark.parametrize("kind", CASES)
def test_identical_code_matches(kind):
    arch, big, target, _, _ = CASES[kind]
    d = scoring._diff(fn(arch, big, target), fn(arch, big, target))
    assert d.bytes_match and d.score == 0


def test_many_shifted_branches_do_not_outweigh_a_real_difference():
    """The motivating case: 40 forward branches over one block; the candidate adds an instruction at the top (shifting
    them all) against a candidate that only changes a register."""
    n = 40
    body = [0x10800000 | (n - 1 - i) for i in range(n)]  # beqz a0 forward, each to the final jr
    target = words(True, *body, 0x03E00008, 0)
    shifted = words(True, *[w + 1 for w in body], 0x24020001, 0x03E00008, 0)
    regalloc = words(True, *[w if i else 0x10A00000 | (n - 1) for i, w in enumerate(body)], 0x03E00008, 0)
    t = fn("mips", True, target)
    old_shift, new_shift = both(t, fn("mips", True, shifted))
    assert old_shift.score >= n * scoring.PENALTY_OPERAND and new_shift.score == scoring.PENALTY_INSERTION
    _, new_reg = both(t, fn("mips", True, regalloc))
    assert new_reg.score <= scoring.PENALTY_REGALLOC * 2  # one wrong register is cheap, and ranks behind nothing odd


def test_the_score_version_moved():
    assert scoring.SCORE_VERSION == 5
