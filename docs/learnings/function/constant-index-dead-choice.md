---
title: A constant array index kept at run time by a dead two-arm assignment
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 46-instruction initialiser,
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, whose target loads an index (`li $v1, 2`) and computes `index * 36`
with shifts and adds, then stores six fields through it. Every plain spelling of the C folds the offset.

The source change, and the measured result before and after (scores, the judge used): `i = 2; unused = arg0 ? 1 : 2;`
before the stores through `D[i]`: the app's judge went from 245 (saved best with search scaffolding) and 3495 (clean
`D[2]`) to 0. Family `constant_index_opaque` (decomp-repair). The dead local is scaffolding.

Failed experiments that would otherwise be repeated: `do { i = 2; } while (0);` 450 (cse stops at the loop end, combine
folds it anyway); goto loops with a condition that never holds 145 at best (the branch stays); `for` and `while` loops
715 and up; `if (arg0) { t = 1; }` and other single-arm dead statements fold (3590); every `-O2` form folds.

What is controlled evidence and what is one function: one function. That the arms' labels split the block for cse and
combine, and that flow and the jump pass after reload remove them, is a hypothesis from the measured variants and the
passes' sources, not traced in dumps.
