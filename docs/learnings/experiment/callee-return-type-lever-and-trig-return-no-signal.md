---
title: flipping callee return types void/s32 and sins/coss s16 -> s32 found nothing on a project's open functions
kind: experiment
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-25
---

What the function looked like: the hand matches of one N64 project that were fixed by a callee's declared return (`void` vs `s32`,
and `sins`/`coss` declared `int` with `s16` locals).

The source change, and the measured result before and after (scores, the project judge): a candidate-only library family that
flipped every statement-called callee's return (and one callee at a time), gated on an unfilled `jal` slot, fired on about a
quarter of all drafts and changed the score on none of ten open near-matches judged (identical before and after); the trig
family (`s16` -> `s32` return, and with `(s16)` casts at the uses) made two open functions worse (160 -> 1005, 745 -> 1445) and
changed the others not at all. Neither was shipped. A conversion-into-each-arm rewrite (a float joined across an `if`/`else`,
converted after) was also tried: one standalone matched function went 785 -> 1095 (the hand fix needed other edits too).

Failed experiments that would otherwise be repeated: all three above. What would be needed first is a target-side signal that the
return type matters (which is not in the bytes of the call itself); until then these stay hand edits.

What is controlled evidence and what is one function: negative, ten open and four matched functions, one project.
