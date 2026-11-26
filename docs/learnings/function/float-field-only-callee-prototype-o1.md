---
title: table fields passed to an unprototyped float callee, and a -O1 function judged under -O2
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 105-instruction N64 particle update calling
two float math routines on table fields, gcc 2.7.2 KMC. The project default was `-O2`; the function is `-O1` like most
of its matched neighbours (the target recomputes the record index on the `else` path and saves `$ra` first).

The source change, and the measured result before and after (scores, the judge used): plain C over a record array
(`T D[]; D[id].x += f(D[id].angle);`, `u16 id`) was exact first try under `-O1` (2950 under `-O2`). From the m2c draft
under `-O1`, `prove_chain` stopped at 320 with two `cvt.d.s $f12`: one callee was still declared `f32 g();` because its
arguments were only `*((f32 *)(base + i))`. With `f32 g(f32);` it is 0; decomp-repair `float_prototype` now reads such
a load as a float argument.

Failed experiments that would otherwise be repeated: do-while fences on the `-O2` profile (best 115).

What is controlled evidence and what is one function: one function.
