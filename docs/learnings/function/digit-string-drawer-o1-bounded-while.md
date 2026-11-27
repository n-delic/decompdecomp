---
title: digit string drawer o1 bounded while
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 115-instruction digit-string drawer: a 5-entry divisor table copied with `lwl`/`lwr` into the front of a `u16`
array, a loop writing one glyph code per decimal digit, a loop blanking leading zeros and a loop calling a glyph
drawer. Built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`: no strength reduction (the index is shifted every
iteration), parameter saves all before the parameter moves. At `-O2` the same C scored 65 to 5660; the profile change is
per function.

The source change, and the measured result before and after (scores, the judge used):

Plain C matched (app judge, 0): the blanking loop as `while (d[i] == 0x10 && i < 4)` (m2c writes a guard on element 0
and `do { } while (A & (i < 4))`), and `u16` parameters copied into `u16` locals for the position, the step written
`x = step + x`. From the saved best (5995 at the old profile, 1035 at `-O1`) the library chain is statement_slide 560,
bounded_sentinel_while 270, guarded_do_while_loop 65, param_copy_halfword 0.

Failed experiments that would otherwise be repeated:

`-fno-strength-reduce` and `-fno-schedule-insns` combinations at `-O2` only got to 1450; `-fforce-addr` and the CSE
skip passes changed nothing.

What is controlled evidence and what is one function:

One function. The tests in decomprepair cover the two rewrites on small functions of the same shape.
