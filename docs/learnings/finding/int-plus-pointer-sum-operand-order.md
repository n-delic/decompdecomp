---
title: int + (pointer + int) keeps the pointer first at both adds; integer sums keep the written order
kind: finding
compiler: sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: observation
date: 2026-11-27
families: byte_pointer_sum_int_first
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game's 77-instruction loop (SN GCC 2.7.2, profile `sn-n64-272-0001 -O1`) looks up a pairwise table, row
`i << 5`, column `j * 4`, with `char *base` hoisted. The saved best was 10, the only rows the operand order of two
`addu`s: target `addu $v0,$v0,$s3` (row index + base) then `addu $v0,$s5,$v0` (column + row address); ours had both
the other way round.

The source change, and the measured result before and after (scores, the judge used):

`var_v0 = temp_s5 + (base + idx);` (10) written as `var_v0 = (void *) (temp_s5 + (idx + ((s32) base)));` was exact
in the app's judge. The C front end builds `pointer + int` and `int + pointer` with the pointer operand first
(c-typeck.c `pointer_int_sum`), so swapping the operands in the source changes nothing; integer sums keep the order
written.

Failed experiments that would otherwise be repeated:

Swapping the operands in the pointer form (an earlier agent: unchanged at 10). A 2D array `s32 D[][8]` with
`&D[i][j]` (480: loop.c hoisted `&D[0][j]` instead). The column term written inline as `temp_s2 * 4` (235).

What is controlled evidence and what is one function:

One function, judged in the app. decomp-repair `byte_pointer_sum_int_first` offers `v = X + (P + I)` with a byte
pointer `P` as `(void *) (X + (I + (s32) P))` (and the inner order swapped). Note: this project's SN toolchain could
not be judged locally from a session off origin/main (the `psyq-obj-parser` recipe's fingerprint changed, so it reads
as not installed); every score here is the app's.
