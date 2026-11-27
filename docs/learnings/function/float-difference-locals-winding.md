---
title: float difference locals winding
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 38-instruction N64 triangle winding test: the cross product of two edge vectors of three `s16` points, converted to
float; when negative, points 0 and 2 are swapped and 1 returned. Its float values use even registers only, so it is
built with `-mfp32` (per-function profile).

The source change, and the measured result before and after (scores, the judge used):

From the m2c draft (app judge, 1095):

1. Each `(f32) (A - B)` of the inline cross product named in an `f32` local ahead of the `if`, the x differences
   first, then the y differences: 410 (`float_difference_locals`). In the expression's own order the same four
   locals scored 1095, so the order is the point.
2. The guard written as `if (cond) { swap; return 1; } return 0;`: 100 (`terminal_guard_layout`).
3. The second swapped store's source loaded into a local before the first store: 0 (`staged_store_rise`).

Failed experiments that would otherwise be repeated:

- `s32` locals for the differences with casts at the products: 1215 or worse in every order tried.
- A struct assignment swap (`t = p[0]; p[0] = p[2]; p[2] = t;`): 3085 (it copies unaligned).
- One of the four differences left inline in a typed-record spelling reached 20 (two registers swapped), not 0.

What is controlled evidence and what is one function:

One function. Why the assignment order decides the schedule is read from the output, not from the compiler (no public
source).
