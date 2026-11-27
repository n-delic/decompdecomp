---
title: An empty else-if arm keeps the compare the target makes for an excluded value
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 282-instruction collision pass (flags above). After `if (k == A) {...}` the target compares `k` with B (branching to the common code) and then with C; the draft's `else if (k != B && k == C)` compiles only the C compare.

The source change, and the measured result before and after (scores, the judge used): written as `if (k == A) {...} else if (k == B) {} else if (k == C) {...}`: 831 -> 7 (the rest was the declaration order of two spilled locals). The isolated judge; the app recorded the final source at 0.

Failed experiments that would otherwise be repeated: a `switch (k)` over A, B and C builds a range tree (1580). The library family `sentinel_constant_local` also reaches 7 from the same source.

What is controlled evidence and what is one function: one function.
