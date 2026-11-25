---
title: loop step literal pin
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: a 181-instruction N64 path search (a nearest-point
walk with a `for (; t < end; t += 0.005f)` loop whose body calls three spline evaluators and `sqrtf`). SN GCC 2.8.1
(`sn-n64-281`), `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`. One register row was left: the step's
pool constant (`lui $at; lwc1 $fN`) sat in `$f8` where the target had it in `$f0`, loaded right at the `add.s`.

Mechanism (loop.c, global.c, reload): the step is a constant-pool load that nothing in the loop changes, so loop.c
makes it a movable and hoists it. The loop has calls, so no saved FP register is free for it; global.c leaves it
unallocated and reload rematerialises it at the use into a reload register of its own choosing (`$f8`). The target's
constant was a local pseudo of the last block, which local-alloc gives the first free register (`$f0`).
loop.c only moves a *user variable's* pseudo when its set is in a block that holds all of its uses (the
`float_constant_loop_pin` finding for KMC 2.7.2; it is the same code in 2.8.1). A local set before the loop and set
again in the step has its first and last reference in different blocks, so it is never moved, and cse still turns the
use into the pool load.

The source change, and the measured result before and after: `f32 step = 0.005f;` above the loop and
`t += (step = 0.005f)` as the step (or `for (; cond; step = 0.005f, t += step)`): 10 -> 0 (isolated judge and the app's
own exact trial, then applied with `suggest`). Reading the constant through an `extern f32` instead (memory, not the
pool) gave the right `$f0` but `lui $v0` instead of `lui $at`: 220. The uninitialised form with the set only in the
step, pinning the bound's `1.0` with it, and pinning every literal at once: 10 / 15 / 25.

Failed experiments that would otherwise be repeated: a step local set once (the same hoist), a goto loop, a while or do
form, an `extern const f32` (1522).

What is controlled evidence and what is one function: one function, verified exact. The library's
`float_constant_loop_pin` did not offer it (a compound assignment was not an operand, and a single literal was never
offered alone); decomprepair 0.6.2 does, and the family's child scores 0 on this function.
