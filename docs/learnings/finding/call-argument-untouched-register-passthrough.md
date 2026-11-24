---
title: A call whose target sets only $a1 passes the caller's own argument first, not a constant cast to a pointer
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

A 62-instruction N64 function (`gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2`) with one pointer parameter, an early-return
arm and a tail, both calling the same callee. m2c drafted the early arm as `callee((void *)1); return;` and the tail as
`callee(arg0, 1);` (the target writes `$a0` and `$a1` there). The early arm's target is `jal callee` with
`addiu $a1, $zero, 1` in the delay slot and no write to `$a0`, which still holds the caller's own `arg0` on every route
from the entry (the arm sits behind two branches that never write it). Saved best 5: one register row, `addiu $a0,
$zero, 1` against the target's `addiu $a1, $zero, 1`. The signature checker already printed it as `[target-argument]`.

Fix: `callee(arg0, 1);` in the early arm, 5 -> 0. A source that passes the constant in the first slot, cast to the first
parameter's guessed pointer type, loads it into the wrong register; passing the caller's parameter there puts the
constant in `$a1` with `$a0` left alone.

Why the library had no step for it: `untouched_argument_parameter` needs the callee called once and a gap between written
registers; `passthrough_argument` proves the register only on the straight-line path from the entry (a branch ends the
proof) and only for a callee called once; `target_call_arity` appended `1` after the cast (`callee((void *)1, 1)`, 300).
decomp-repair `untouched_slot_passthrough` pairs the callee's n-th `jal` with the C's n-th call, proves the untouched
registers on every route (`passthrough_argument._entry_arguments_reach(..., site=n)`), and passes `argK` there (adding the
parameter when the caller lacks it), the literal without its pointer cast first.

Measured from the original m2c draft with the family's first child, the standalone judge: this function 5 -> 0; a
second function of the same shape (a callee called after a branch, a cast constant for the second slot) 110 -> 0; two
others 890 -> 885 and 1370 -> 1360; one 350 -> 430 (a regression, the judge declines it). Fires on 6 of 2,078 first
drafts and on none of the project's 1,073 matched sources.

Controlled evidence: two functions reach 0 with the single edit; the rest are measurements of the same child.
