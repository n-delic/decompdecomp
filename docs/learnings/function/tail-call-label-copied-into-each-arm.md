---
title: tail call label copied into each arm
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 95-instruction angle picker: four ranges of
an angle each choose a float difference, then one call takes it and a second difference. m2c wrote `v = X; goto call;`
arms around one labelled call at the end of the `void` function. Saved best 60 (float registers of two parameters
exchanged).

The source change, and the measured result before and after (scores, the judge used): the call written in every arm
(jump.c merges the tails after register allocation), and the angle in one `s16` local assigned from the float directly.
The app's judge: 0. From the plain form of the saved best (405): `tail_label_run_copy` with `local_web_split` 325, then
`local_weight_ladder` 0. From a clean rewrite with an `s32` temp and a copy (325): `local_width_group` 0.

Failed experiments that would otherwise be repeated: an `s32 angle = t; if ((s16) t >= 0x168) angle = t - 0x168;`
coalesces the two and turns `bnez; move` into `beql`; `-O2`, `-O3`, `-mfp64` and `-mips2` trials were no better.

What is controlled evidence and what is one function: the family has controlled tests in decomp-repair; the match is
one function. That the extra references from the copies raise the two parameters' allocation priority is a hypothesis.
