---
title: the sqrtf NaN retry m2c writes is the compiler's own builtin expansion
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: vector-length helpers of an N64 game (a 14-instruction `sqrtf(x*x + y*y + z*z)` leaf, and the same
hunk inside larger movement and camera functions). The target holds `sqrt.s`, a `c.eq.s v, v` self-compare, a `bc1t` over a
`jal` of the C library's `sqrtf` (the errno retry GCC 2.8 expands for `sqrtf`), and nothing else. m2c reads those bytes as
source: `v = sqrtf(a); if (v != v) { v = retry(a); }` (the retry also gets stray extra arguments from live registers).

The source change, and the measured result before and after (scores, the project judge): delete the `if` block and keep
`v = sqrtf(a)` (the callee `f32 sqrtf(f32)` declared); a `void` draft whose epilogue still holds the root in `$f0` returns it
as `f32`. Controlled on a matched leaf: 972 -> 0 (the retry dropped; `return sqrtf(...)` and the `f32` return variants are both 0
there). On open functions of the same project the library family `sqrtf_nan_fallback` (decomp-repair 0.5.1044) moved nine of ten
judged saved bests: 4576 -> 3320, 4944 -> 3649, 7324 -> 6283, 7837 -> 6793, 3262 -> 2652; one got worse (6590 -> 8150).

Failed experiments that would otherwise be repeated: writing the retry out as the callee's own call (a second check and call
the target does not have); keeping the check but dropping the call (the `c.eq.s` and branch remain).

What is controlled evidence and what is one function: one matched leaf is the exact case; the open-function moves are partial
(the rest of those functions still differs). The gate is `sqrt.s` plus a `jal` of the retry callee in the target; 57 saved m2c
drafts and 16 saved bests of the project fire it.
