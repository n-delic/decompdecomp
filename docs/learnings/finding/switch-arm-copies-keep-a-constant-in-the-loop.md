---
title: switch arm copies keep a constant in the loop
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:
A 193-instruction N64 per-object spawner, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.
One loop with calls compares a mode word with 5 twice. The target loads the constant beside each compare
(`li $v0, 5`); the candidate moved it out of the loop and, with no register left, reloaded it into `$a1`.

The source change, and the measured result before and after (scores, the judge used):
loop.c moves an invariant when `threshold * savings * lifetime >= insn_count`; the two compares are one movable
with savings 2. `cc1 -dL` showed it moved at 217 and 220 "real insns" and "not desirable" at 225. Cross-jumping
runs later, so each copy of a switch arm still counts. Every stacked `case` label of the switch given its own copy of
its arm was the match (app judge: 685 with one run copied, 0 with both runs).

Other steps from the m2c draft (10524): a clean rewrite with typed records (5300); a counter array's symbol where
the draft stored through a constant address (4779); the loop as `while (1) { if (...) break; }`, which keeps the
test at the top and spills the `u16` counter (1655).

decomprepair `else_case_arm_lift` does the arm copies where m2c opened the labels inside an `if`'s `else`
(2165 -> 0; from the draft after `loop_else_break`, 3485 -> 1730).

Failed experiments that would otherwise be repeated:
`volatile` on the counter (6315). Splitting only `if (a || b)` into two arms added 3 insns, not enough (685).
Copies placed right after their old arm instead of at the end of the switch: 680.

What is controlled evidence and what is one function:
The insn counts and the movable's verdict are from the compiler's own dump of one function.
