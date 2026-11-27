---
title: Stage a call result and a later stack argument
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler calls a seven-argument routine with a nested
helper result narrowed to `s16` and a final argument loaded from memory. The
target's code prepares a stack argument and narrows the helper result for the
call. Staging the helper result in an `s16` local and reusing a dead `s32`
local for the final memory argument changed the isolated score from 675 to
350 with gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`.

The `target_call_argument_stage` repair offers that combination when the
target has the call, stack store and narrowing sequence. A public SDK
implementation also stages these values before the call. The app was offline,
so 350 remains an isolated result. On this source staging only the halfword
local left the score at 675; staging only the word local scored 2331. These
are observations on one function, not controlled exact-match tests. Choosing
an order for nested expressions is guided by target evidence because calls
may have side effects.
