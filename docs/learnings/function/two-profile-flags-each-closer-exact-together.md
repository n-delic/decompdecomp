---
title: two profile flags each closer exact together
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -fno-schedule-insns -fno-schedule-insns2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 136-instruction spawn loop whose target
recomputes `i * 12` and `i * 10` from the counter (no strength reduction) and keeps source order in the prologue and
before the call (no scheduling).

The source change, and the measured result before and after (scores, the judge used), same source under each profile:
own flags 5422, `-fno-strength-reduce` 3390, `-fno-schedule-insns -fno-schedule-insns2` 2059, all three added 0.
A float constant used once in the loop is hoisted into a saved register only when it is a local assigned at the top of
the loop body (`drop = 20.0f;`); written as a literal under the final flags it is 607. The compiler preflight now
probes the union of two improving flag additions.

Failed experiments that would otherwise be repeated: either scheduling flag alone (2830, 1200 under
`-fno-strength-reduce`).

What is controlled evidence and what is one function: the profile scores are one source on one function.
