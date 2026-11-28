---
title: float compare literal weighted by one-shot scopes, and a looped table named in a local
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 144-instruction N64 text-scrolling screen: a `while` loop over a table of string pointers, a long `||` chain of
indices choosing the draw call, and a tail `v -= 0.5; if ((f32) y < 0.0f) { ...reset... }`.

The source change, and the measured result before and after (scores, the judge used):

App judge, from a plain `while` loop (first written nested range ifs, 2145; `&&` range test, 1595):

- the table held in a pointer local before the loop and used for `[i]` and `[0]`: 1595 -> 515 (the target has the whole
  address in a saved register, `lw $a0, 0($s6)`, and copies the induction pointer from it);
- the callees' fourth parameter as `f32` with `1.0f` instead of `0x3F800000`: 515 -> 215;
- `float limit; do { do { limit = 0.0f; } while (0); } while (0); if ((f32) y < limit)`: 215 -> 0. One scope deep: 20
  (order right, `$f0`/`$f2` exchanged); set plainly in the local: 215.

decomp-repair `loop_table_local`, `float_argument_prototype` and `float_compare_limit_weight` reach 0 from the plain
loop with `prove_chain.py`.

Failed experiments that would otherwise be repeated:

`0.0f > (f32) y`, `(f32) y < 0`, a `f32` local for the converted `y`, the scroll update through a local (30), a float
local shared with the entry's truncated loads (registers right at the end, entry loads reordered), and a one-shot scope
around the scroll update only (70).

What is controlled evidence and what is one function:

One function. That the extra loop depth lifts the zero's local-alloc priority above the conversion's value is a
hypothesis; the scores are measured. The local and its scopes are scaffolding.
