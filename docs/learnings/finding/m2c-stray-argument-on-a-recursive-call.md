---
title: m2c stray argument on a recursive call
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 126-instruction N64 quicksort over a row of floats with a parallel row of bytes, built with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. Its loop leaves a masked counter in `$a3`, and m2c passed that register
as a fourth argument to the three-parameter function's own recursive call: "too many arguments to function", so the
machine draft never compiled.

The source change, and the measured result before and after (scores, the judge used):
`seeds.own_call_arguments` drops an extra argument the code does not set up for that call (and declares a parameter for
one it does). Of 101,190 saved drafts in one data home, 40 had such a call. The match itself was plain C with the two
tables declared as `extern f32 D[][6]` / `extern u8 D2[][6]`, `u16` parameters and counters, and `-O1`
(2635 at the project's -O2, 0 at -O1, isolated judge and the app).

Failed experiments that would otherwise be repeated:
Byte-offset arithmetic over `extern u32` symbols with `& 0xFFFF` at each use (the saved best, 2455) never reached the
per-use masks and per-loop base reloads that `u16` counters and 2D indexing give for free.

What is controlled evidence and what is one function:
The arity rule is controlled by `tests/test_draft_own_calls.py`; the profile and C shape are one function.
