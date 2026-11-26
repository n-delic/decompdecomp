---
title: three unrolled vector components, comparison sides swapped, exact at -O1
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 144-instruction N64 leaf updates a lower and an upper bound vector from three float vectors, one block per component
(`if (a[i] < b[i]) { ... } else { ... }`, five comparisons per block, plain `f32 *` parameters, the fifth on the stack).
Its saved best (500) was an m2c draft full of identical-arm branches, tuned under `-O2`; the matched neighbours were
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.

Measured with the app's judge:

- Plain `if`/`else` with direct array reads: 6680 under `-O2`, 90 under `-O1`. Under `-O1` every reload the target has is
  there by itself; the 90 was only which register each of two float loads took.
- gcc loads a comparison's left operand first, so writing the target's load order (`b[i] > a[i]`, `c[i] > b[i]`,
  `c[i] > a[i]`: nine comparisons) gave 0, recorded by the app through a `compiler_experiments` trial.
- `comparison_sides` offered only the first four comparisons, so `prove_chain` stopped at 70. With every comparison
  offered and one child per comparison repeated with other numbers (decomp-repair), it reaches 0 in three steps.

Failed (earlier attempts): tuning the `-O2` draft with identical arms and gotos stopped at 500.

One function; the left-operand-first load order is the behaviour `comparison_sides` already records.
