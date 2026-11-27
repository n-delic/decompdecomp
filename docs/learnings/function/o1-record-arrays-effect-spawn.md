---
title: o1 record arrays effect spawn
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 221-instruction N64 function that spawns two effects (two calls of an 18-argument allocator, writes into a table of
100-byte records and one of 124-byte records), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (saved best 2249 under its
own settings; the same C scored 1010 under -O1).

Plain C with both tables as real record arrays and the id allocator's result a `u16` scored 10 on the first compile:
`addiu $v0,$v0,2` where the target has `move $v1,$v0; addiu $v0,$v1,2`. The spawn call's result kept in an `s32` and
used through `(u16) n` casts was exact (10 -> 0, `narrow_widening`). An `s16` local scored 620.

One function.
