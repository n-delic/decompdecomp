---
title: a double sqrt retry and a hoisted float literal in a goto loop
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 70-instruction model-radius loop of an N64
game: each pass divides three floats by a constant, takes the double square root of the sum of squares and keeps the
largest. The target holds `sqrt.d`, the `c.eq.d` self-compare with a `jal` of the C library's `sqrt`, a loop tested at
the top with a `j` at the bottom, and only even float registers (a per-function `-mfp32` profile).

The source change, and the measured result before and after (scores, the project judge), from the plain form of the
m2c draft (910):
1. `v = sqrt(a)` with no retry block, `double sqrt(double);` declared (the draft named both calls after the project's
   symbol for the library routine): 245. Library `sqrt_nan_fallback_double`.
2. The pool word written as the literal `10000.0f` (still held in its local): 40. Library `float_literal_global`.
3. The goto loop written `while (1) { if (!(c)) break; ... }` and the literal at each use: 0. Library
   `loop_float_literal_inline_break`.

Failed experiments that would otherwise be repeated: the loop form alone scored 60 and the literal inline alone 717;
`while (c)` rotates the loop (1315); `for (;;) { if (!(c)) { break; } ... }` also moved the test to the bottom (1195)
where `while (1)` did not; the same extern global read inline instead of the literal is not hoisted past the call (814).
A clean rewrite with one record pointer stepped by `v++` matches too (the loop pass makes the second pointer).

What is controlled evidence and what is one function: one function. Why `for (;;)` and `while (1)` differ here was not
traced in the compiler's source (hypothesis: jump.c's duplication of the exit test).
