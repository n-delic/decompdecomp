"""alloc_roles.py (the match-function skill) replays global.c's colouring from a greg dump and reads the target's
register for each pseudo from the judge's rows."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def _module():
    spec = importlib.util.spec_from_file_location("alloc_roles", SCRIPTS / "alloc_roles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Three allocnos that all conflict; 72 has the highest priority, 73 prefers $a1, 85 is the lowest.
LREG = """Register 72 used 8 times across 4 insns; GR_REGS or none.
Register 73 used 4 times across 8 insns; GR_REGS or none.
Register 85 used 6 times across 4 insns; GR_REGS or none.

(insn 10 9 11 (set (reg:SI 85)
        (plus:SI (reg/v:SI 72)
            (reg/v:SI 73))) 3 {addsi3_internal} (nil)
    (nil))
"""
GREG = """;; 3 regs to allocate: 72 73 85
;; 72 conflicts: 72 73 85 2
;; 73 conflicts: 72 73 85
;; 73 preferences: 5
;; 85 conflicts: 72 73 85 2

;; Register dispositions:
72 in 3  73 in 5  85 in 4
"""


def test_the_replay_gives_the_compilers_registers():
    roles = _module()
    usage, insns = roles.parse_lreg(LREG)
    disposition, conflicts, prefs = roles.parse_greg(GREG)
    prio = {p: roles.priority(*usage[p]) for p in conflicts}
    assert insns[10] == [85, 72, 73]
    # 72 first: $v0 is a hard conflict, so $v1; 85 next: $v0 and $v1 taken, $a1 is 73's preference, so $a0; 73 its $a1
    assert roles.simulate(prio, conflicts, prefs) == disposition


def test_votes_pair_the_assembly_lines_with_the_rows():
    roles = _module()
    usage, insns = roles.parse_lreg(LREG)
    disposition, conflicts, prefs = roles.parse_greg(GREG)
    asm = "\taddu\t$4,$3,$5 \t\t#  10 addsi3_internal\n"
    rows = [("r", "addu $v1, $a0, $a1", "addu $a0, $v1, $a1")]
    votes = roles.role_votes(asm, insns, disposition, rows)
    assert votes[85]["v1"] == 1 and votes[72]["a0"] == 1
    assert roles.targets(votes, conflicts) == {85: 3, 72: 4, 73: 5}


def test_the_what_if_names_the_priority_that_moves_a_pseudo():
    roles = _module()
    usage, _ = roles.parse_lreg(LREG)
    _, conflicts, prefs = roles.parse_greg(GREG)
    prio = {p: roles.priority(*usage[p]) for p in conflicts}
    # the target has 85 in $v1 and 72 in $a0: 85 must be coloured before 72
    steps = roles.whatif(prio, conflicts, prefs, {85: 3, 72: 4}, 3)
    assert steps and steps[-1][2] == 0
