---
title: A callee's result declared narrow where the target narrows the other side of the compare
kind: function
compiler: SN GCC for N64 (sn-n64-281)
evidence: verified
date: 2026-11-27
---

A 48-instruction two-axis range check, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, judged
with the app's judge. The saved best (30) declared the callee `volatile short f(s16)` and its two limits `s32`, so the
`sll`/`sra` landed on `$v0`; the target has `sll $s0,$s0,16; sra` and `sll $v1,$s2,16; sra`: the limits are extended.

The source change, only exact as a set: the callee returns `s32`, the two limit locals are `s16`, and each compare is
written call first (`f(a) > hi || f(b) > lo`). From the saved best: callee alone 410, locals alone 400, call first
alone 30, callee and locals 375, all three 0.

Library family: `compare_narrow_move` (decomp-repair), whose first child on the saved best is exact.

The one-step families do not reach it, because every single part is worse or neutral. This is one function.
