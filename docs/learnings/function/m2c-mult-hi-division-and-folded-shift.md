---
title: m2c's MULT_HI division and a shift folded into a divisor
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 67-instruction N64 routine that picks a random speed and heading for a moving object, built with `sn-n64-281
-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. A matched neighbour does nearly the same work, and its C
adapted by hand was exact on the second judge run. The machine draft scored 1899.

The source change, and the measured result before and after (scores, the judge used):

From the draft, with the app's judge worker, in order:

1. `MULT_HI(v, 0x80010003)` with `>> 0xE` and `- (v >> 0x1F)` is `v / 32767`: 1899 -> 1484 (`magic_high_divide`).
2. A local read before a call, inlined (`product_inline`, existing): -> 1030.
3. `x / 192` where the target has `sra x, 3` before the multiply is `(x >> 3) / 24` (`folded_shift_quotient`): -> 765.
4. `a - (e - K)` compiled as `(a + K) - e`; the bracket in a new local, and the `s32` local holding the call's result
   declared `s16` (`constant_term_stage`, `narrowed_only_local`): -> 565.
5. `a * 2 * b` as `(a << 1) * b` (`shifted_factor_product`) together with one store moved below the computation it
   follows in the game (`statement_slide`, existing): -> 0.

Failed experiments that would otherwise be repeated:

- Storing the bracketed term in a dead local (`dead_local_stage`) left 10 to 315: the reused name ties registers.
- Step 5's two halves do not pay off alone: the shift spelling leaves the score unchanged and the store move alone
  raises it (565 -> 640).
- Dropping the stray third argument of a call, or fixing the callee's prototype, was not needed for this match.

What is controlled evidence and what is one function:

One function. That GCC moves a constant out of a bracketed term is read from the compiled output here, not from the
compiler's source.
