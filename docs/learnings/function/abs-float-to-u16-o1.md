---
title: an int taken as an absolute float and converted to u16 under -O1 (two arms, cross-jumped truncations)
kind: function
compiler: KMC GCC 2.7.2 -O1 -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like: a 167-instruction arctangent-by-table helper returning degrees. Each argument goes
`(f32)x > 0.0f ? (u32)(f32)x : (u32)-(f32)x`, masked to 16 bits into an `s32`. The negative arm converts the int again
(no CSE): -O1. At -O2 the conversion is reused and the best of the same source was 3380.

The source change and measurements (app judge): `if ((f32)a > 0.0f) x = (u16)(u32)(f32)a; else x = (u16)(u32)-(f32)a;`
with `s32 x`, `u16` table element and return type, and the arm for a negative first argument written first: exact under
`-O1`, applied as a per-function profile through a compiler trial.

m2c writes the cross-jumped truncations as `goto`s between the arms; written back as `(u32)` conversions (decomp-repair
`float_unsigned_conversion`) the fresh m2c draft went 6820 -> 2185 under -O1.

Failed experiments: one local reused for the float value (4345), a negation guard then one conversion (4870).

Controlled evidence: one function. The library chain from the rewritten draft stopped at 400; m2c's shared
subtraction for `(360 - v) % 360` and `180 - v` still needs a hand edit.
