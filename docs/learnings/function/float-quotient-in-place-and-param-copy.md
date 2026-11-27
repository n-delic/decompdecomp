---
title: a float local divided in place before the call and a narrow parameter copied before the clamp compare
kind: function
compiler: SN GCC 2.7.2 -O1 -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler:

An 85-instruction N64 clamp-and-slew routine (SN GCC 2.7.2 -O1): s16 arg0 and arg2, a float local divided by a global scale and converted for a call. The saved best was 265.

The source change and measured result (app judge):

Three steps, each measured: (a) one s32 temp_s1 holds the clamped value in both arms and the final store writes it (265 -> 190, goto/phi merge from m2c; no family does it, too tied to that draft's goto shape); (b) temp = temp / D_SCALE as its own statement, then func(..., (s32) temp) (190 -> 10, decomp-repair float_quotient_argument_stage); (c) if (arg2 < (var_a0 = E)) var_a0 = (s32) arg2; written var_a0 = E; arg2c = (s32) arg2; if (arg2c < var_a0) var_a0 = arg2c; (10 -> 0, condition_assignment_param_local). From 190 the two families chain to 0.

Failed experiments:

The all-families chain from 265, 190 and 10 found no improving child.

Controlled evidence:

One function; step (a) is not automated.
