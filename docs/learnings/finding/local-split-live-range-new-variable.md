---
title: local split live range new variable
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: four small N64 functions built with KMC and SN GCC (`-G0 -mips3 -mgp32 -mfp32 -O2`, the game's own profile) each differed from the source that matched by one variable decision. A float local held a call's result for the first compares and was assigned again later (`d = f(); if (d < K) ...; d = cam->x;`); a loop cursor was assigned from a call several lines above its `for (; o <= end; o++)`; a call result was the base of a store through a field (`FIELD(f(a), T, 0x9C) = v;`) where the match held it in a local that was already declared and dead there; a float call result stood in a loop test where the match assigned a local in the test (`k < (dist = sqrtf(x))`).

The source change, and the measured result before and after (scores, the judge used): the reused local's first live range written in a new local declared after the others (60 -> 0); the call's result in a local of its own and the cursor set from it in the loop head, the new local declared beside the others of its type (105 -> 0); the call result in the dead local of the same type (770 -> 0); the test assigning the local (35 -> 0). Each was exact in one library child (decomp-repair `local_split_reuse`: `local_split_new`, `loop_cursor_own_temp`, `call_result_base_local`, `call_assigned_in_test`), judged read-only with the app's isolated judge worker. A new local's DECLARATION POSITION is part of the fix (the pseudo number breaks allocation ties): after all leading declarations for the split, beside the same-type declarations for the cursor.

Failed experiments that would otherwise be repeated: none that failed; no existing family offered any of the four (a one-hop coverage check of the library's draft children over the pre-fix sources found no child nearer the match).

What is controlled evidence and what is one function: observation, one function each; the split is only offered where no other path can reach the renamed range (a conditional second definition leaves the first live, unless an if/else chain defines the local in every arm).
