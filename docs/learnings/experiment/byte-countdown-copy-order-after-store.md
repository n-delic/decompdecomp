---
title: A byte countdown whose copy comes after the first store, unsolved
kind: experiment
compiler: SN GCC 2.8.1 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 42-instruction N64 fade-out step, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. It reads
one byte field, stores it to two others, decrements it and stores the result to three, with the object pointer moved
to `$a1`.

The source change, and the measured result before and after (scores, the judge used):

685 -> 60. `o->b71 = o->b73 = o->b72; n = o->b72; n--; o->b73 = n; o->b72 = n; o->b71 = n;` gives the target's
registers: the third read of the field becomes the `move $v1,$a0` and keeps the object pointer in `$a1`. A local copy
(`n = a`) merges and loses that. Left: the target has `sb $a0` before the `move`, this has it after the `addiu`.

Failed experiments that would otherwise be repeated:

At equal priority sched2 takes the store over the `addiu` (`potential hazard`, read in the 2.8.1 `-dR` dump and
`sched.c`), so no statement order moves the store up. A `do { } while (0)` between them does, but then the copy is
born after it and takes `$v0` instead of `$v1`. About 40 fence ranges, inline helpers, field and local types, other
optimisation levels and `-mcpu` values, and SN 2.7.2 builds did not do better than 60.

What is controlled evidence and what is one function:

One function, not matched.
