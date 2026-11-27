---
title: a global compared again is reloaded after an || of two tests, not because it is volatile
kind: function
compiler: KMC GCC 2.7.2 -O1 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-27
---

A 284-instruction `s32 f(s32 index)` that picks a direction from a float angle in four blocks: KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O1` (the project default `-O2` scored 5040; the matched sibling's C under `-O1` was the start).
The target compares with 0 and 360 off one load, then loads the global again before 90, 180 and 270.

The saved best had `if (a == 0.0f) v = 0; else if (a == 360.0f) v = 0; ...` and declared the float global `volatile`
to get the reloads (1550). Written `if (a == 0.0f || a == 360.0f) v = 0; else if ...` with the global plain, it was
exact (app judge, 0): the body of an `||` is reached from two tests, so the next compare starts at a label and reloads.

Each edit alone left 1550. decomp-repair `or_arm_merge_plain` writes both and reaches 0 from that saved best in one
step. `% 15 == 1` and `x / 15 * 15 == x - 1` on a `u32` compile the same here.

Controlled: the three variants (merge only, plain only, both) on this one function.
