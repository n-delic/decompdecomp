---
title: An old saved best can hide a function the draft family already solves
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -O2
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 22-instruction leaf of an N64 game that
wraps two signed halfword fields back into a range. `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`, the function's own profile. The target reads each field twice (`lh` and `lhu`), compares the first and adds to
the second.

The source change, and the measured result before and after (scores, the judge used): plain `if (p->a < LOW) { p->a +=
K; } else if (p->a > HIGH) { p->a -= K; }`, once per field, is byte-identical (the app's judge). `field_wrap_arms`
already wrote that from the stored machine draft (800 -> 0, one step). The saved best earlier searches had left at 510
spelled the same shapes differently (the field macro written out, the first arm's sum moved above the copy it reads, the
guard's store staged through a local) and got no child; since decomp-repair 0.6.480 the family reads those too
(510 -> 0, one step; `prove_chain.py --families field_wrap_arms` on both sources).

Failed experiments that would otherwise be repeated: all families, capped at 40 judged variants, reached only 315 from
that saved best (`read_before_assign`).

What is controlled evidence and what is one function: one function. When a saved best resists, run the families on the
stored draft as well before hand work.
