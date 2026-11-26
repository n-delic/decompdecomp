---
title: last read base reassign
kind: function
compiler: KMC GCC 2.7.2 sn-n64-272-0001 -O1 -g
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a small N64 sort comparator (KMC GCC 2.7.2,
the function's own profile) that returns the difference of two u16 fields of 0x510-byte records, both found through one
table base held in an integer local (`temp`, loaded from a global). m2c writes each lookup as `(temp + idx * 0x510)`
inside the read. The target sums the last lookup into the dying base register (`addu $a2,$a2,$v0; lhu $v0,0x418($a2)`);
the nested sum put it in the result register (`addu $v0,$v0,$a2; lhu $v0,0x418($v0)`): 10 differing rows.

The source change, and the measured result before and after (scores, the judge used): hold the first read in a local,
assign the base its last sum, read through it: `first = *temp2; temp = temp + idx * 0x510; return first - *(u16 *)((s8 *)temp + 0x418);`
10 -> 0 (the app's judge, `fast_judge.py`). decomp-repair's `last_read_base_reassign` (RetroReversing/decomp-repair#1806)
makes that child from the score-10 source and its child judged 0. Reading (hypothesis): a pseudo both set and used by the
add is tied to its source by local-alloc, so the dead base's register takes the sum.

Failed experiments that would otherwise be repeated: the m2c nested form (the saved best, 10) and its earlier
variants (75 and 15 in the attempt history) all left the last sum in the result register; only the reassignment moved it.

What is controlled evidence and what is one function: one function; the family's unit tests are synthetic shapes.
