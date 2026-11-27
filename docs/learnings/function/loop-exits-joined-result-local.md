---
title: A loop's several returns join at one result return
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`): a `for (;;)` read loop with two exits, `return 0;` and
`return f(sp10);` (375 as drafted, inside a `do { c = a; ... } while (0);` copy fence a Deep Permuter probe had left).

Exact (375 -> 0): remove the fence and both copies, then leave through one tail with the result in a local initialised to the literal
the first exit returns: `s32 ret = 0; ... { D = 1; goto done; } ... { ret = f(sp10); goto done; } ... done: return ret;`.
Without the fence only (`return f(sp10)` as it was): 723; with `ret = 0;` assigned at the first exit as well: 723 (the assignment is
dead, the compiler normalises it, but the initialiser has to be the shape); `s32 ret;` uninitialised: 723.

The family `loop_returns_joined_result` in decomp-repair writes those children (and strips the copy fence); no earlier family did
(`returns_to_result_local` wants three leaves in an `if` tree, no loop). One function; the fence removal alone is worse (723), so a
greedy chain cannot take it without this family doing both.
