---
title: A scale made from a dying float local is written back into it
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-26
---

What the case looked like: two vector length clamps, `len = sqrtf(...); if (len > 15.0f) { s = 15.0f / len; x = s * x; }`,
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The target copies the square root into `$f2` on both paths and loads
15.0 into `$f0`; ours had the two registers swapped and no copies.

The source change (library family `consumed_value_reuse`): `len = 15.0f / len; x = len * x;`, 830 to 0 (app's judge),
with plain struct fields. One block rewritten alone: 70.

Failed experiments: `15.0f < len` (no change), a local holding 15.0 (1682), the double `15.0` (1680), `-O2` (3337);
the saved search sources that put the sum in an inline helper kept it in `$f20` with a larger frame (2602 to 3116).

Controlled: the one-block and two-block rewrites on one function.
