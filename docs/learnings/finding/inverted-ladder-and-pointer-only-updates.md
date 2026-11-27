---
title: inverted ladder and pointer only updates
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

An N64 score routine of 73 instructions (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`):
a byte index into word tables, a `sprintf`, and a three-way `if (mode == 0) ... else if (mode == 1) ... else if
(mode == 2)` where each arm adds a constant to the same table element. Saved best was the m2c draft (3466).

A clean rewrite from the target with typed word arrays judged 406; writing the compare as `a > b` rather than
`b < a` (the operand loaded first is written first) judged 0, and the app's own local pass found that step.

From the draft itself, with the app's judge:

1. `inverted_guard_ladder` (the nested `!=` ladder and its labelled store as a flat chain, the arms' updates on the
   place): 3466 -> 1666.
2. `field_copy_reread`: 662.
3. `comparison_sides`: 200.
4. `asm_byte_table_deref_width` (the draft read a `char[]` table with `lbu` where the target has `lw`): 0.

`arm_pointer_update_inline` alone (`p = address; *p = *p + k;` -> `*(s32 *)(address) += k;`) took the best state
the older families reached from the draft (240, only the arms' index registers) to 0. Each arm's `lbu` index then
lands in the pointer's register and the third arm's `sllv` by the compare constant appears.

Turning one level of the ladder at a time, or only copying the labelled tail, judged no better than before (3181
unchanged for the tail copy), so a greedy chain never got through it.

One function. The reason the pointer local changes the register is a hypothesis: the allocation was not dumped.
