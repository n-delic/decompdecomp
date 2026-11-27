---
title: squared distance in place steps
kind: function
compiler: sn-n64-281 -O2 -mfp64
evidence: observation
date: 2026-11-27
---

A 70-instruction N64 nearest-object search loop, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`. Symptom: every instruction right but a stack frame that saves two registers (`mflo $s0` / `mflo $s1`), or the
difference, its product and the shifted square in different registers (`mflo $v1; srl $v1, $v1` where the target has
`mflo $t0; srl $t0, $t0`).

The source change, judged by the app's judge: `dist = ((u32) (dx * dx) >> 12) + ((u32) (dy * dy) >> 12)` and
`best = (u32) (range * range) >> 12` written as `dist = dx; dist *= dist; dist >>= 12;` right after the first
difference, `dy *= dy; dist += (u32) dy >> 12;`, and `best = range * range; best >>= 12;`, with the search's
`do { } while (0)` fences opened. From the saved best (930): `clean_rewrite_scaffold` 330, then `square_sum_steps`
0 (decomp-repair, 2026-11-27).

Each edit needs the others: the folded difference with the fences closed scored 220, opened without the range's steps
105. A typed rewrite had the same two saved registers with the one-expression form (1240), and was exact with the
steps.

Also found: `clean_rewrite_scaffold` wrote an inlined `char *` helper's body without casting an `s16 *` argument, so
the returned pointer stepped by halfwords (480 instead of 330). Fixed in the same library change.

What is controlled evidence and what is one function: the library has unit tests that run both forms on a host
compiler; the byte results are one function.
