---
title: global alloc priority extra refs
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 562-instruction N64 actor state machine (`-mfp32` per-function profile). The last difference was three callee-saved
registers rotated: target `done` (a call result kept across calls) in s4, the home distance pair in s5/s6; ours gave
`done` s6.

The source change, and the measured result before and after (scores, the judge used):

`global.c` orders pseudos by `floor_log2(refs) * refs / live_length` and hands out s0.. in that order (`-dl` dump: "used
N times across M insns"). `done` had 6 refs over 112 insns (12/112), behind the squared distance (8/43) and the home
deltas (8/66, 8/70). Writing one more call result into it (`done = func_80043248(arg0); if (done != 0)` in a later
case, where it was `if (func_80043248(arg0) != 0)`) gives 8 refs (24/L) and moves it ahead: 80 -> 0 under the trial
profile. Declaration order changed nothing (no tie).

Failed experiments that would otherwise be repeated:

Splitting `done` into two variables (one per switch arm) or assigning every call result in the arm to it (12 refs)
both made the allocation worse (2495, 2540). Merging an unrelated temporary into `done` (prove_chain's
`disjoint_local_merge`) fixed the three registers but put the temporary in a saved register.

What is controlled evidence and what is one function:

Controlled: one statement changed. One function; the priority numbers come from the `-dl` dump of cc1n64 under wibo.
