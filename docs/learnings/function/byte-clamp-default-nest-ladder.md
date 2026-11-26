---
title: A nest of overwritten defaults is an else-if ladder
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 29-instruction clamp of a signed byte and a
float scale, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The target loads the byte again for each later test, into
the register the result lives in.

The source change, and the measured result before and after (scores, the judge used): `v = 0; if (a) { v = 0x3F; if (b)
{ v = -0x3F; if (c) { v = x; } } }` written as `if (!a) v = 0; else if (!b) ... else v = x;` with an `s8` local: 640 to
0 (the app's judge). Family `default_nest_ladder` (decomp-repair). The same source at `-O2` scores 1770.

Failed experiments that would otherwise be repeated: one conditional expression instead of the ladder (1945 at `-O1`,
2165 at `-O2`); the expression behind a macro into the `s8` local (1345).

What is controlled evidence and what is one function: one function.
