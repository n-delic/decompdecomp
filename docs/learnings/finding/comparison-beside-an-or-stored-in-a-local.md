---
title: A comparison beside a bitwise or, stored in a local, stays a value
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 148-instruction N64 spawn routine, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The target computes
`andi; sltiu $v1,7; xori $v1,1; or; bnez`; `((x >> 3) & 1 | (count >= 7U)) == 0` compiled to two branches.

The source change, and the measured result before and after (scores, the judge used):
`(... | (flag = (count >= 7U))) == 0` with a new `int flag`: 1065 -> 650 and 655 -> 240 (isolated judge, two
sources). decomp-repair `or_compare_materialized` offers it.

Failed experiments that would otherwise be repeated:
`count > 6`, `!(count < 7)`, `(count < 7) ^ 1`, `!(a | b)` and an inline predicate returning the comparison did not
give the target's instructions.

What is controlled evidence and what is one function:
One function. That the folder turns an `|` of unnamed truth values into jumps is a hypothesis.
