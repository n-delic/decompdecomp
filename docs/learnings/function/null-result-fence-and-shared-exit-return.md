---
title: result variable cleared before a NULL guard, and a nested test that returns early
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 123-instruction N64 hit test that returns the object it tested for NULL, or NULL. Compiler and flags as above (the
function's own per-function profile). The target saves the last register *before* the first branch and clears `$v0`
in its delay slot; every plain spelling of the guard put the save in the slot.

The source change, and the measured result before and after (scores, the judge used):

The app's judge; isolated probes with `fast_judge.py`. Saved best 735, exact after:

1. `*((index * 4) + &D)` on an `M2C_UNK` symbol, which C scales a second time, written `(&D)[index]` (735 -> 435).
2. `dx *= dx;` before the line that squares the difference, instead of squaring into a temporary (435 -> 400).
3. The guard through a result variable: `do { result = NULL; } while (0); if (p != NULL) { ...; result = p; } return
   result;`, with every failure inside returning `NULL` itself (400 -> 370), and the byte copied on the success path
   held in the `u8` local the state tests used, declared `s32` (370 -> 100).
4. A nested `if (type != NONE) { ...; goto L; } goto L;` (L returns NULL) as `if (type == NONE) { return NULL; }`.
   The target loads `type` a second time to index a table, and only this form kept the load (100 -> 0).

decomp-repair: `pointer_byte_step` (parenthesised index), `square_in_place`, `null_result_fence` /
`null_result_byte_temp`, `shared_exit_guard_return`. Taking the best child at each step runs 735 -> 435 -> 400 -> 100 -> 0.

Failed experiments that would otherwise be repeated:

None of these moved the delay slot (300 on a typed rewrite): `||` and nested guard forms, `goto` to the exit, an
inline helper, a result variable that is constant on every path to its `return` (cse removes it), a plain
`result = NULL;` without the fence (scheduled before the register saves, 325 on the exact source), `-g`, `-O3`, other
SN builds. A `volatile` field also kept the second load, but is not needed with the early `return`. A `goto` to the
same exit label instead of the early `return` did not keep it (100).

What is controlled evidence and what is one function:

One function. Why the fence keeps the clear next to the branch and why the `return` keeps the load are hypotheses
(scheduling boundary, cse stopping at the jump), not read from SN's source, which is not available.
