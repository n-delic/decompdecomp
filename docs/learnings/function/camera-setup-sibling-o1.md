---
title: camera setup sibling o1
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 113-instruction camera setup routine (a perspective call in each arm of one `if`, display-list words, a look-at call
with constant float arguments), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The `mtc1` followed by `nop` then `swc1`
and the two whole call setups in the arms are what `-O1` writes; `-O2` with both schedulers off reached only 2100.

The source change, and the measured result before and after (scores, the judge used):

The matched sibling's C with one table offset changed and the look-at arguments written as float literals (a first
argument list, then the constants the target stores to the stack): 7811 at `-O2`, 0 at `-O1` (app judge). The draft had
lost every stack argument of the two calls (best 6199).

Failed experiments that would otherwise be repeated:

Editing the draft: its calls pass the wrong argument lists, so no flag or rewrite of it gets near.

What is controlled evidence and what is one function:

One function. No repair was added: the gap is a draft without the callees' prototypes, not a rewrite.
