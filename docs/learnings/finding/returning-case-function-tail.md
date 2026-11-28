---
title: returning case function tail
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

An N64 state handler's target places one returning switch arm near the end of the function. A reconstructed switch
put that arm before the main body, so its branch and the arm's instructions were aligned far from their targets.

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, replacing that arm with a jump to its body at the function tail
reduced the local linked score from 9,930 to 7,215. A return before the new label preserves normal fallthrough. The
current project profile remains `-O2`; the result is diagnostic, not an exact match or an applied profile change.

Reordering the four case/default labels without outlining the returning arm did not improve the earlier 10,402
switch candidate. The outlined candidate scored 23,129 under the saved `-O2` profile, so it was not submitted.

This is one observation, not a general compiler rule. The generated repair reproduced the 7,215 score, and a
compiled synthetic control checks that the original and outlined cases agree. Game source and data remain private.
