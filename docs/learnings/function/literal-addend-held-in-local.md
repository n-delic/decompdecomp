---
title: A literal in the middle of a sum keeps its addiu only when held in a local
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2 (sn-n64-281)
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 386-instruction hit handler of an N64 arcade game (SN GCC 2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`). A speed is computed twice as `angleSin(a) * (size * 409 + 614 + (r >> 6))`. The target does
`addiu $v1, $v1, 0x266` on the product and then `addu $v1, $v1, $s0`; ours added 0x266 to the shifted random value.
Symptom: constant added to the wrong operand, `addiu` on the wrong register, `addu` operands swapped.

The source change, and the measured result before and after (scores, the judge used):

`s32 add = 0x266;` before the two statements and `size * 409 + add + (r >> 6)` in both. App judge: 360 to 0 (from 420
with two stores still swapped: 420 to 60, then the swap to 0). decomp-repair `constant_in_local_addend` (PR 2302)
emits it.

Compiler reason, read in the 2.8.1 `fold-const.c` (`associate:`, `split_tree`): `(VAR + CON) + ARG1` is rebuilt as
`VAR + (ARG1 + CON)`. A variable is not an `INTEGER_CST`, so it is left where written. SN's own build has no source.

Failed experiments that would otherwise be repeated:

All on the same function: parentheses around `(size * 409 + 614)` (no change); an unsigned inner product cast to
`s32` (no change); `(r >> 6) + 614 + size * 409` reaches 75 then 15 with only operand order wrong; `- -(r >> 6)`
860; computing the whole base in a local before the call 1640 to 3986 (it costs a saved register). A `static
__inline__` function returning `size * 409 + 614` also reaches 0.

The earlier steps of the match: three `playSoundEffect(K)` calls instead of one variable argument (780 to 420; the
existing `call_into_arms` offers exactly that child).

What is controlled evidence and what is one function:

The fold rule is read in the source; the scores are one function.
