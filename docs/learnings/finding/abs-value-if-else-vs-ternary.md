---
title: An absolute value written as if/else negates the source; as ?: it negates the copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: observation
date: 2026-11-27
families: branch_choice_ternary, abs_idiom
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game's 93-instruction camera-style update (KMC gcc 2.7.2, per-function profile `-O1 -g`) wraps two 12-bit
angles into a signed range and clamps them to +-1000. Each clamp takes the absolute value of the wrapped angle. The
target has, both times, `move $v0,$v1; bgez $v1,L; negu $v0,$v0` (the copy in the branch's delay slot, the negation
of the copy).

The source change, and the measured result before and after (scores, the judge used):

`if (t >= 0) { d = t; } else { d = -t; }` gave `negu $v0,$v1` (negating the source register), score 10 in the app's
judge (two register rows). Both written as `d = (t >= 0) ? t : -t;` the app judged it byte-identical (10 -> 0).
`d = abs(t);` (with `int abs(int);`) was also 0. Hypothesis, not checked in expr.c: the conditional expression is
expanded straight into its target, so the second arm negates the target that the delay-slot copy already filled.

Failed experiments that would otherwise be repeated:

All scored 400 (a `bltzl` with the `negu` in its delay slot, the arms laid out the other way):
`d = t; if (t < 0) d = -d;`, `d = t; if (d < 0) d = -d;`, `d = t; if (t >= 0) {} else d = -d;`, and the inverted
ternary `d = (t < 0) ? -t : t;`. So the test's direction matters in the `?:` form too.

What is controlled evidence and what is one function:

One function, two sites, judged in the app. decomp-repair `branch_choice_ternary` writes a variable filled by both
arms of an `if`/`else` as the `?:` (all sites at once, each alone, each inverted); its first child of the saved best
was 0. `abs_idiom` covers only the one-arm `d = t; if (d < 0) d = -d;` form.
