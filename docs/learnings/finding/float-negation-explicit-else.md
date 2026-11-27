---
title: float negation explicit else
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

In a large N64 function, two conditional negations initialize a nonvolatile float local with `-x` and assign `x` when `x > 0.0f`. GCC 2.7.2 KMC at `-G0 -mips3 -mgp32 -mfp32 -O2` gave a closer branch and register layout when both were written as explicit `if`/`else` assignments.

The application judge improved from 47,035 to 46,980; the same source at `-O1` improved from 2,530 to 2,285. A ternary spelling worsened an earlier diagnostic score from 2,610 to 3,105, and initializing with `x` then negating when the comparison is false yielded 2,745 on the later source.

The explicit branches preserve the previous behavior for NaNs and signed zero. This is one function observation, not a match or a controlled compiler regression test.
