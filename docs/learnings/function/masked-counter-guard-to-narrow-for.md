---
title: masked counter guard to narrow for
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 58-instruction N64 free-list routine that walks `count` links of a record table. `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O1` (per-function profile).

At 75 the first load landed in the wrong register; `saved = node = G;` (decomp-repair `chained_assignment_split`) gave
60. The rows left were the loop's entry: the target zeroes the counter before the guard's `andi` and fills the branch
delay slot with the copy of the bound. The m2c-shaped loop

    i = 0;
    if ((count & 0xFFFF) != 0) { n = count & 0xFFFF; do { ...; i++; } while ((i & 0xFFFF) < n); }

written as `u16 i; for (i = 0; i < (u16) count; i++) { ... }` was exact (60 to 0). Family:
`masked_counter_guard_for`.

Failed: `for (...; i < (count & 0xFFFF); ...)` (200, the earlier `count & 0xFFFF` is shared); the `for` over a `u32` or
`s32` counter (670, 1070); the bound assigned before the guard (120); `do {} while (0)` around `i = 0` (60, same code).

One function, measured with the app's judge.
