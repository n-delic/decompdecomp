---
title: zero through a copy keeps it apart
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 195-instruction N64 effect function. Its entry fades a byte: the target has `addiu a0,v1,-1; move v1,a0`, stores
`v1`, and tests `a0` after an `if` that stores `v1` through a second pointer. `level = value - 1; value = level;`
compiled to one register (`addiu v1,v1,-1`, and the later test on `v1`). At the end of `cse_insn` (cse.c, read in the
2.8.1 source; the target compiler is the SN build) a copy whose destination outlives its source is swapped with the
previous instruction, and cse then follows the path around the `if`.

The source change, and the measured result before and after (scores, the judge used):

The app's judge. Both of these together: `level = value - 1;` one unrelated statement above `value = level;`, and the
guarded block's `hit->g = 0;` written `hit->g = value = 0;`. 110 -> 0. The rest of the way from the saved best (385):
`shade = measure(..); p = pool; angle = shade + K;` with `shade` a dead `int` local (the target loads the pool address
into `v1` between the call and the add; 145 -> 120 with the other edits as they then stood), and a scratch value
`bit = random() & 2; e->flag = bit * 0x10;` written into its use (120 -> 110).

Failed experiments that would otherwise be repeated:

The zero alone: a copy appears but the wrong way round (210). The separating statement alone: 110, unchanged, for six
orders. `do { } while (0)` around the copy, the stores or the `if`, `u8`/`s16`/`u32` types for the faded value, and a
second condition on the `if`: no copy. A fresh local as the call's carrier: 255.

What is controlled evidence and what is one function:

One function. The permuter's forms are decomp-repair `branch_copy_zero_chain`, `call_result_dead_carrier` and
`call_result_temp_inline`; from the saved best they chain 385 -> 120 -> 10 -> 0.
