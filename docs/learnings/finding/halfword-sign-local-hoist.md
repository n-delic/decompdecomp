---
title: The sign-extended copy of a halfword counter taken right after the counter is read
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
A 40-instruction N64 object update with two tests of a halfword counter (285 before the match). Compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2.

The source change, and the measured result:
`n = (s16) t;` moved from just before the first test to after the statement that stores `t` back. Family `halfword_sign_local_hoist` (also names two raw `(s16) t` uses with one `s32` local). Partial: only this placement matches the matched C; the rest of that function's difference (an `s16` second counter, argument forms, scope debris) comes from other families, so the matched C was not reproduced whole. Judge not run.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: observation.
