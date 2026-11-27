---
title: Reading the copy back in the next store ranks a float one saved register higher
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 collision walk of 559 instructions (four nested loops over boxes and triangles), gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O1`. A clean typed rewrite from the assembly scored 927 on its first compile.

The source change, and the measured result before and after (scores, the judge used):

Declaring the spilled counter before the spilled pointer and computing a plane offset into the variable that held the
normalising scale: 775. A missing word in the output record: 65, only three float saved registers rotated. Then
`best = h; res->y = h;` written `best = h; res->y = best;`: 0 (app judge). `best` and the two float parameters had
equal printed allocation priorities; one more reference decided the order. decomprepair `copy_back_set` already finds
this step from the 65 source (`prove_chain.py`).

Failed experiments that would otherwise be repeated:

`res->y = best = h;` stays at 65; `res->y = h; best = res->y;` scores 125.

What is controlled evidence and what is one function:

One function.
