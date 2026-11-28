---
title: Fixed-point matrix to float: compute both packed words before the first conversion
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

A 38-instruction SDK routine (`guMtxL2F`: two word streams of 16.16 halves, converted to a float matrix in a 4 by 2
loop). The machine draft and the saved best (890) walked their own pointers and converted each packed word inline.

What matched (app's judge, 0): index the matrix as `rows[row][pair * 2]` (the row pointer is the loop pass's own),
read each stream word into a local, compute both packed words into locals before the first conversion, and write the
scale as `(1.0f / 65536.0f)`. The target shifts the integer word in place before `mtc1`, which only the staged form
does.

Measured steps from a hand-written loop: pool symbol as `extern f32` 250 (the address took `$v0`, not `$at`); float
literal 240; both words staged as well 0. From the saved best: literal plus one staged word 455, the shift temporary
inlined 205 (only the row pointer's place left).

Failed: the SDK's address-taken `e1`/`q` spelling under KMC GCC 2.7.2 `-O2` and `-O3` stored them on the stack (2455);
the staged loop under KMC `-O2` scored 500 (nops after `mtc1`). 51 children of `temporary_hoist`,
`temporary_hoist_block` and `float_value_stage` from the 240 source gave none better.

Controlled: decomp-repair `packed_fixed_matrix_to_float` takes the machine draft and the 890 source to 0, and
`conversion_operand_pair` takes the 240 source to 0 (decomp-repair PR 2246). The app's library pin is pending.
