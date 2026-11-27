---
title: o1 object reset signed members
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 209-instruction N64 object reset (constant stores and six counted loops over record members),
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (saved best 2315, tuned under -O2 with one-shot scopes and goto loops).

Plain C with one real struct for the object (record arrays inside it, `for` loops, `a = b = c = 0.0f` chains) scored 200
under -O1 on the first compile: the only difference was `ori $a0,$zero,0xffff` where the target has
`addiu $a0,$zero,-1`. Making the members that are assigned -1 `s16` instead of `u16` was exact (200 -> 0,
`member_immediate_signedness`; making only some of them signed scored 110 or 305).

Failed: nothing else was tried. `prove_chain` before the family existed reached 60 (`shared_store_constant`).

One function.
