---
title: A command arm's cursor step before its operand reads (negative offsets)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float (and -mfp32)
evidence: observation
date: 2026-11-26
families: cursor_advance_first, block_local_share
---

What the function (or case) looked like and the exact compiler and flags:

An SN GCC 2.8.1 model loader (618 instructions) ends in `for (;;) switch (*cmd)` over a model's display commands. Two
arms build `gSPModifyVertex` words from the command's bytes and step the cursor by 6. In the target the first arm
loads `1($s1)`..`4($s1)` and then increments the two cursor givs (`addiu $s1, $s1, 6; addiu $s5, $s5, 6`), while the
second increments them first and loads at `-6($s1)`..`-2($s1)`. Both arms were written `n = cmd[1]; c = ... cmd[5];
... cmd += 6;`, so the second came out like the first.

The source change, and the measured result before and after (scores, the judge used):

The second arm written with the step first and the reads rebased:
`case 3: cmd += 6; { _g = gfx++; c = (cmd[-4] << 24) | (cmd[-3] << 16) | (cmd[-2] << 8) | cmd[-1]; n = cmd[-5]; ... }`.
The app's judge: 1480 -> 1335 under the function's profile; `fast_judge.py --flags=-mfp32`: 1430 -> 1285 (the arm's
loads and increments now in the target's order). decomp-repair `cursor_advance_first` makes this child from the saved
best (its other children: 2025, 4140, 1630, 3575).

Failed experiments that would otherwise be repeated:

- A copy before the step (`op = cmd; cmd += 6;` and reads through `op`): 2480; the copy keeps its own saved register
  instead of becoming `$s1 - 6`.
- Sequential `*cmd++` reads: 3395.
- The same step-first edit in the first arm as well: 2220 (that arm's target order is loads first).
- In the second arm, `c` in `c2`, `w1` written without `c`, or `w1` stored first: 1690, 1675, 1575. The arms' tails still
  differ in registers (`_g`/`c` in `$a1`/`$a0` against the target's `$a0`/`$a1`), so the first arm's `j` into the
  second arm's tail (cross-jumping) is not reproduced by these.

Next step, which did reproduce it: the two arms' block pointers (`{ Gfx *_g = gfx++; ... }` in each) written as one
function-level local both arms assign: 1335 -> 825 (1285 -> 775 under `-mfp32`), the first arm now ending in `j` into
the second arm's tail with `lui $v1, 0x210` in its delay slot, as the target does. A shared global pseudo is coloured by
global.c before local-alloc gives the arms' byte temporaries their registers. Sharing it with every arm of the switch:
1030; with the case-4 pointer instead of a new local: 825 as well. decomp-repair `block_local_share` makes this child
(the chain from the saved best: `cursor_advance_first`, then `block_local_share`, 1430 -> 1285 -> 775 under `-mfp32`).
Still open: the arms' byte temporaries (`$v1, $v0, $a1, $a2` in the target, `$v0, $v1, $a0, $a1` ours) and the shared
pointer's register (`$a0` in the target, `$a3` ours).

What is controlled evidence and what is one function:

One function, one arm. The mechanism (loop.c reducing the operand reads to givs of the cursor, so the biv step's place
in the arm decides whether the increments precede the loads) is a hypothesis read from the output, not checked in the
compiler's source.
