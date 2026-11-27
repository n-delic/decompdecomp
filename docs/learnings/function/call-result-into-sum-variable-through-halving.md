---
title: A call result kept in the variable its argument was halved from
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function looked like and the exact compiler and flags: a 66-instruction N64 function (KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O1 -g`) that sums two halfwords of a record minus a global, halves it (signed), passes the
half to a lookup `f(0x100, half)` returning a 16-bit value, and stores the negated result. Target: the sum and the
result in one saved register (`lh $s3; addu $s3; subu $s3; srl $a1,$s3,31; ... move $s3,$v0; negu $v0,$s3`). The
saved best (80) had m2c's three locals: `temp_s3` (u32), `bias = (s32)(temp_s3 + (temp_s3 >> 0x1F))` and
`temp_s3_2 = f(0x100, bias >> 1)`, with `s16 f(s32, s32);`.

The source change, and the measured result: `temp_s3` declared `s32`, the result written back into it
(`temp_s3 = f(0x100, half);`), the bias written `half = temp_s3 / 2;` in the bias's place (before the second sum),
and the prototype returning `s32`: 80 -> 0. Together only: the merge alone with the `s16` prototype was 420 (the
narrow return is re-extended, `sll`/`sra`, where the target has `move`), with the `s32` prototype 120 (the halving
inlined into the call argument is scheduled after the second sum's loads).

What is controlled evidence and what is one function: one function. decomp-repair `call_result_carrier_reuse`
(0.6.298) offers the merge with the division and, as a second child, the widened prototype.
