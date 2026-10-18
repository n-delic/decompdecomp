"""A compile's result says what the agent's last change did and where the first difference is now: one small change
per compile works best, and the agent should know whether to keep or undo it."""

from decompnet import scoring
from decompnet.network import Network

ROWS = [(" ", "addiu sp, sp, -0x18", "addiu sp, sp, -0x18"), (" ", "sw ra, 0x10(sp)", "sw ra, 0x10(sp)"),
        ("r", "move s0, a0", "move s1, a0"), ("<", "jal func_80001234", ""), (" ", "jr ra", "jr ra")]


def test_the_first_difference_and_how_far_the_start_matches():
    assert scoring.first_mismatch(ROWS) == (2, 5, ROWS[2])
    assert scoring.first_mismatch([(" ", "nop", "nop")]) == (1, 1, None)
    assert scoring.first_mismatch([(">", "", "nop"), (" ", "jr ra", "jr ra")]) == (0, 1, (">", "", "nop"))


def test_the_score_says_whether_the_last_change_helped():
    d = scoring.DiffResult(40, 500, ROWS, False)
    said = Network._what_changed(d, 90)
    assert "better (90 → 40): keep it" in said and "first 2 of 5 target instructions match" in said
    assert "regalloc" in said and "move s0, a0" in said
    assert "worse (10 → 40)" in Network._what_changed(d, 10)
    assert "no effect" in Network._what_changed(d, 40)
    assert "last change" not in Network._what_changed(d, None)  # a first compile: nothing to compare with
