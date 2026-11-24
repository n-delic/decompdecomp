---
title: float global array read
kind: finding
compiler: KMC GCC 2.7.2 -G8 -mips2 -O1
evidence: observation
date: 2026-11-24
---

A 17-instruction N64 function: a call, then a float global compared with another float global, the call's result or 2
returned (KMC GCC 2.7.2, -G8 -mips2 -O1). The target reads the first global through an ordinary register
(`lui $v0, %hi(G)`, `lwc1 $fv1, %lo(G)($v0)`) and the second through `$at` (the `l.s` macro's shape), and keeps the call's
result in `$v1`. The saved best, with `extern f32 G;`, read both through `$at` and scored 10.

The source change, and the measured result before and after (scores, the judge used): two parts that only pay off together.
Declare the first global `extern f32 G[];` and read `G[0]` (alone: 115, the load pair is right but the call result lands
in `$a0`); then read the element into a local right after the call and compare the local (`f = G[0];` after
`temp = call();`): 0. The fast judge scored 10 -> 0. decomp-repair `float_global_array_read` (0.5.1003 or later) offers both
as children, one per statement boundary for the local; `prove_chain.py --families all` reached 0 in one step.

Failed experiments that would otherwise be repeated: a scalar extern with pointer casts, `*(f32 *)&G`, `volatile`,
the local loaded before the call (607), ternary or if/else shapes (410), `-0.0f` (240): the function stayed at 10 or got worse.

What is controlled evidence and what is one function: one function; the rule offered is bounded by the assembly (the global's
high half built in a register other than `$at`), and over one project's saved bests it proposes children for 7 of 259 and
for none of 124 matched sources. The reason for the extra register is a hypothesis, not read from the compiler's source.
