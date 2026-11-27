---
title: temporary passed to next alloc
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

An 82-instruction N64 function that spawns two child records from an allocator and initialises them. The saved C was
an m2c draft scoring 1235 under the function's profile (345 under KMC, which was the wrong lead: the target stores
globals through `lui $v0`, as SN does).

Five edits reach 0, each judged by the app's judge:

1. `w = x; ...; w = w + 2;` written `w = x + 2;` (515 alone).
2. The constant local m2c made (`k = 2`) written as literals (775 alone).
3. Two adjacent byte stores swapped (no change alone).
4. A `do { } while (0)` fence removed (worse alone, 1325).
5. The second allocation's temporary written as the first: `temp = alloc(temp);` instead of
   `temp_2 = alloc(temp);` (1160 alone; 75 -> 0 as the last step). Both pointers then sit in `$a0`, as in the target.

Steps 1 and 5 were not offered by the library. decomp-repair `step_into_definition` and
`merge_disjoint_temporaries` (which now merges a temporary read by the statement that first sets the next) produce
them; applying the library's children along this path from the saved C reaches 0.

Failed: a plain constant local for the 2 (any position, `u8` or `s32`) never moved the `li` into the target's place;
the store order did.

Verified for this one function. The explanation of the `$a0` choice (one pseudo with the call-argument copy
preference) is a hypothesis.
