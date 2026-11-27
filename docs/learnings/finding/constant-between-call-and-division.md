---
title: A constant assignment between a call and the division of its result
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 function of 428 instructions, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, with float saved registers one
round off. The target loads `$zero` into a saved float register right after the `sqrt.s` NaN fallback, before the
`1.0f` of the reciprocal.

The source change, and the measured result before and after (scores, the judge used):

First, one local per use of a reused float temporary and of the angle passed to the math calls (400 -> 285). Then
`inv = 1.0f / sqrtf(x); ... pitch = 0.0f;` written `inv = sqrtf(x); pitch = 0.0f; inv = 1.0f / inv;`: 285 -> 0
(app judge). decomprepair `constant_between_division` (decomp-repair #1970) emits the second step. `prove_chain` from
the 400 source with the earlier library reached 260.

Failed experiments that would otherwise be repeated:

`pitch = 0.0f` anywhere else, including as a comma expression inside the numerator, scored 60 to 385.

What is controlled evidence and what is one function:

One function.
