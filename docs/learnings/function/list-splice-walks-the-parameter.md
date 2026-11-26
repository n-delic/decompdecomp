---
title: list splice walks the parameter
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 105-instruction free-list splice, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, like its matched neighbours (the
saved best, climbed under `-O2`, was 1840).

The walk is written on the `u16` parameter itself: `while (T[arg0].next != 0xFFFF) { arg0 = T[arg0].next; count++; }`.
A second cursor variable (`cur = arg0;`, any width) left the entry test's `andi $v0, $a0, 0xffff; move $a1, $v0`
unreachable (275, or 1115 for a `u16` cursor); the extension and its copy are the compiler's own. Dropping the cursor:
275 -> 5; `total = count + total` for `total += count`: 5 -> 0 (judged by the app).

Library: `mirror_local_drop` (decomp-repair) drops the cursor; `allocation_lever` closes the last row.
