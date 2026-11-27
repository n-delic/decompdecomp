---
title: At -O1 the narrow parameter itself is the working value, and 5 - width staged in two statements
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 130-instruction signed number printer with
seven arguments, an `s16` value last. The target copies the incoming word at entry (`lw $v1; move $t1,$v1`), sign
extends it in `$v1` for the sign test and negates into `$t1`, which the digit loop works on.

The source change, and the measured result before and after (scores, the app judge): `num = value; if (value < 0)
num = -value;` with the loop on `num`: 115 (with `lim = 5; lim -= width;`) and 220 (with `lim = 5 - width;`).
`w = value; if ((s16) value < 0) value = -w;` with the loop on `value`: 0. `&buf[5 - width + positive]` beats
`buf + positive + 5 - width` (760 against 535 at that stage). decomp-repair `param_works`, `constant_minus_split`,
`param_works_split` (module `param_working_value`).

Failed experiments that would otherwise be repeated: an `s32` parameter keeps the value in its stack slot (`lh` of it,
a frame 8 bytes larger, 933-1263); a `u16` parameter gives `andi` where the target has `move` (200); K&R definitions
changed nothing; a local copy of either width with the loop on the copy stayed at 430.

What is controlled evidence and what is one function: the scores are the app judge's on one function.
