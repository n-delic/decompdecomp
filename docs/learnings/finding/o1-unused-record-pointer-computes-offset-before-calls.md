---
title: An index offset computed before a run of calls at -O1 can come from an unused record pointer
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A camera routine's target computes `index * 36` at the head of a join, before the first of several `__sinf` calls
whose results are stored to `camera[index]`. With the stores written plainly the offset was computed after the calls
(best 590, only order rows). Assigning `c = &camera[index];` there, with `c` never read, was exact (0): the address add
is deleted as dead and the offset stays. The same routine needed the first product of each sum in a block-scoped `f32`
(a shared local took the wrong saved float register, and inside one expression the multiply moved after the calls).

Two related camera routines; the pointer was needed in both. The second also needed an identical-arm guard on a zero
local to keep a dead `move $a0, $zero` (105 -> 0), which is scaffolding, not source anyone would write.
