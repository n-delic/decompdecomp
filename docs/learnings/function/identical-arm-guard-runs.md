---
title: identical arm guard runs
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 235-instruction N64 menu handler (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) whose
saved best held one scaffolding guard `if (m) { 3 byte stores; 2 global-to-global copies; i = 0; } else { same }` (390, ten
rows). The scheduler hoisted the copies' loads above the byte stores, and the loop bound's load above the copies; the target
keeps stores, loads, copies, then the bound's load.

The source change and result (fast_judge, then the app's compile_and_diff: matched): one guard per run, the counter reset
written once after them: `if (m) {stores} else {stores}  if (m) {copies} else {copies}  i = 0;`. 390 -> 0. Cause
(hypothesis from reading sched.c; only the judge confirms it): the first scheduler works per basic block, and each arm is
a block of its own, so statements in one arm move past each other for load latency; a guard per run is a block per run, so
loads of the second run cannot rise above stores of the first (a volatile or aliasing pair would also block them, and
neither is in the target). Library: decomp-repair `identical_arm_split` (PR 1289) offers the cut points and the hoisted
tail as combined children; two of its 18 children from the saved best are 0.

Failed experiments: removing the guard (855), the whole run in one guard with the tail outside (465 and 390), cuts without
the hoisted counter reset (children 280-300).

Evidence: one function; the controlled part is the 18 children judged from the saved best.
