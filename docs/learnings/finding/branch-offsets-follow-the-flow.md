---
title: branch offsets follow the flow (score version 3)
kind: finding
compiler: KMC GCC 2.7.2 -O2 (scoring, all processors)
evidence: observation
date: 2026-11-22
---

Case: a 996-instruction N64 function (KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`), saved best 340 (325 after later
work). A hand-off note described a private seed "closer to the target (2175 only because of offsets)": one local
variable for three `flags & 4` tests, which adds one instruction. Judged with the app's judge in a scratch copy of the
project (read only), rebuilt from the saved best by that edit: 325 -> 2160 under score version 2.

Cause: one instruction added or removed shifts the offset of every later branch. Each conditional branch whose number
differs cost 10 (`PENALTY_OPERAND`), and a jump the assembler relocated against the function's own symbol
(`j f+0x50c`, a different token after the shift) read as another call and cost 150 (`PENALTY_MEANING`): the seed's
2160 was one inserted instruction (100), 88 shifted branch rows and 7 relocated jumps (1,050), nothing else.

Change (scoring version 3, `scoring._Alignment`): the diff already aligns the two instruction lists by opcode. A
branch whose target is the instruction that its counterpart's target pairs with in that alignment goes to the same
place, so it is compared with its target masked, whatever the offsets say. Conditional branches and jumps with a
number or label (MIPS `bnez $a0, 0x40`, PowerPC `bne 0x20`, x86 `.L1a`, ARM and Thumb `#0x20`, compare-and-branch and
test-and-branch with the target last) and jumps to the function's own symbol (`j f+0x50c`, `<f+0x1c>`) are read. A
branch to a symbol of another function is compared as before; a branch whose target pairs with another instruction is
charged as before. A score only falls; a match is still byte-identical (`bytes_match`), and a candidate that differs
only in shifted branches still scores at least 1.

Measured, 84 functions' history in one N64 game (1,624 judged states recompiled with the project's compilers, linked at the
function's address, scored under version 2 and 3 on the same objects):

| | version 2 | version 3 |
| --- | --- | --- |
| the seed above | 2160 | 170 |
| the function's saved best (325), same function | 325 | 260 |
| 66 matched functions (>= 5 states): mean Kendall tau of score against token distance of the source to the final match | 0.562 | 0.572 (19 functions better, 12 worse, 35 the same) |
| the same, 22 functions of 150 or more instructions | 0.531 | 0.536 |
| mean tau of score against the instruction-count error (should not rise: a different length is not progress) | 0.567 | 0.537 |
| pairs of states with a clear distance order (>= 20 tokens apart), the closer one scored worse | 21.7% of 10,116 | 21.3% |
| the same, pairs where one state has the target's instruction count and the other has not | 17.2% of 3,919 | 15.8% |
| 13 large unmatched functions (970 to 2,503 instructions), best state of 23 | 6,536 to 36,955 | 3,786 to 29,955 (mean drop per state 1,441 to 5,235) |

709 of the 1,259 matched-function states had branch-only rows, and no state's score rose. The gain in ranking is
modest on matched functions' histories because most of their states differ in more than offsets, and almost all of the
gain is on functions of 150+ instructions; the effect is large exactly where it was reported: on big functions where one
extra instruction made a faithful candidate (170) score 6 times a worse one (the seed's 2160 against 325, now 170
against 260).

Not changed: the jump-table entries (the words in `.rodata`, outside the function's code, so not in the diff's rows); a
branch with the same offset text as its counterpart but another aligned target (0 as before: a change to charge it would
raise scores); instructions inserted and deleted around a branch target keep their 100 each. `regions.py`
(the skill's region table) finds basic blocks from the same operand forms but only estimates; it doesn't compare, so
nothing is shared with it.

Rollout: saved bests were scored under version 2 and are rescored once by Check again (`app/score_version.py`); until
then a stale best is high, so a slightly worse source could replace it. Users restart the app and run Check again.

Evidence: observation (one game, one compiler; the seed is a reconstruction of the hand-off note's edit from the saved
best, scored in a scratch copy). Controlled unit tests for each processor's branch form are in
`tests/test_branch_alignment_scoring.py`, with synthetic code.
