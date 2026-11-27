---
title: stepped byte counter narrowed with its temporary
kind: function
compiler: sn-n64-281 -O2
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 26-instruction N64 bit reader, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`: a loop that
runs a byte count down and shifts one bit out of a buffered byte each time. m2c wrote the count as
`t = v - 1; v = t; if ((t & 0xFF) != 0xFF)` on two `s32` locals; the one differing row was `andi $v0,$v0,0xff` in the
target against `andi $v0,$a3,0xff` (cse reads `t` back as `v`).

The source change, and the measured result before and after (scores, the judge used):

`u8 n = count; ... if ((u8) --n == 0xFF) break;` or both words declared `u8`: 5 -> 0 on the app's judge (and the saved
best, 60 with its search fences, to 0 in one step of decomp-repair `stepped_byte_counter`).

Failed experiments that would otherwise be repeated:

Narrowing the counter alone 105, the temporary alone 110, folding the temporary with the counter still a word 110, a
`u8` parameter with the two-statement step kept 105, a signed byte temporary compared with -1 450. Rotated loops
(`while (n--)`, `while (--n != 0xFF)`) were 1020 and up: the target tests at the top and jumps back, so the loop is
`while (1)` with a `break`.

What is controlled evidence and what is one function:

The scores are one function. That cse reads the word temporary back as the counter is an inference from the differing
row, not read from the compiler's source.
