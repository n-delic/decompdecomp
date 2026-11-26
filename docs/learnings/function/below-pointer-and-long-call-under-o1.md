---
title: stores below a held address, and a long call two zero arguments short, under -O1
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

Two N64 functions (KMC gcc 2.7.2) whose saved attempts were under the project's `-O2`; both were exact only under
`-G0 -mips3 -mgp32 -mfp32 -O1` as a per-function profile. Signs of `-O1` here: unfilled delay slots beside plain C,
a table element reloaded for each use, and a field read again after a branch.

Colour interpolation (136 instructions). The target holds the timer byte's address in a register and stores the four
result bytes at -4..-1 from it. `p[-4] = v` compiled to absolute `sb %lo(...)($at)` (1200); a second pointer
`q = p - 4; q[0] = v` stayed base-relative (200; decomp-repair `negative_offset_base`). Then `s32` quotient and
remainder of a `u8 / u8` division (the compiler divides unsigned) with the remainder statement first: 0. Library
families (`local_weight_ladder`, `narrow_unmask`, `allocation_lever`) also reach 0 from the 200 and 1200 sources.

State handler with an 18-argument call (139 instructions). The call was written with 16 arguments: 233, mostly stack
rows (frame 0x50 for 0x58). The two trailing zeros (`stack_argument_tail`) gave 5, and a `u16` result local widened to
`s32` with `(u16)` at the array subscripts (`narrow_widening`'s cast child) gave 0. Widening without the casts: 1660.
An earlier saved best had a `volatile` pad local standing in for the missing arguments, and `volatile` reads to
get the second switch's reload; neither is needed at `-O1`.

Controlled evidence: the judge's scores for these two functions; why `-O1` folds `p[-k]` is a hypothesis.
