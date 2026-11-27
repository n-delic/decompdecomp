---
title: o1 table flat column first
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 178-instruction N64 menu-cursor routine
that reads one `u16` cell of a 24-wide table in two arms. It saves `$ra/$s0` before copying its parameter (`-O1`); the
saved best was m2c goto scaffolding tuned under `-O2`.

The source change, and the measured result before and after (scores, the judge used): plain nested `if`/`return` C
(each rejecting path stores 4 itself and returns; jump optimisation merges the stores) with the table declared
`u16 T[][24]` scored 1790; the flat `u16 T[]` read as `T[col + row * 24]` was exact (app judge). `T[row * 24 + col]`
scored 40 (the loads of `col` and `row` swapped). Decomp-repair `index_2d_flatten` offers both flat orders.

Failed experiments that would otherwise be repeated: none beyond the two-dimensional and row-first forms above.

What is controlled evidence and what is one function: one function.
