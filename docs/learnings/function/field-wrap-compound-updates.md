---
title: a halfword wrapped into a range is a compound if/else-if, not m2c's temporaries
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 46-instruction N64 leaf that wraps two halfword fields back into a range, the range chosen by a global flag.
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The target reads each field twice
(`lh` for the compares, `lhu` for the sum), keeps the sum in `$v0`, and builds `-0x8000` with `addiu` from `$zero`.

The source change, and the measured result before and after (scores, the judge used):

m2c's draft (800) held the two reads in temporaries and wrote the update as a shared result assigned inside a comma
condition, as a guard that returns, and as `x = (s16) ((u16) x - 0x8000)`. Written as
`if (x < LOW) { x += K1; } else if (x >= HIGH) { x += K2; }` and `x -= 0x8000;`, it is byte-identical (0) in the app's
judge run standalone (`decomp judge-worker`, a checkout from before the PsyQ converter's C++ section change). The
library's `field_wrap_arms` writes all of it as one child from the saved draft.

Each site alone: first comma site 960 (worse), the guard 800, the second comma site 715, the compound update 400.
All four together: 0. A one-edit-at-a-time search therefore cannot reach it.

Failed experiments that would otherwise be repeated:

Removing the `do { } while (0)` around the last update without writing it as a compound assignment stays at 400;
keeping the scope around the compound assignment is exact, so the scope is not what mattered. 120 judged variants of
the other repair families reached 490.

What is controlled evidence and what is one function:

The per-site scores are controlled single edits. That `(u16) x - K` makes `ori` where `x -= K` makes `addiu` is read
from this one function's output (not checked in the compiler's source: a hypothesis).
