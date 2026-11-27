---
title: call product and shift steps through dead locals
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 68-instruction fixed-point velocity routine (two calls returning `s16`, each times a speed, `>> 12`, stored to two
members) of an N64 game, SN64 GCC 2.8.1 with the flags above. The saved best was an m2c draft at 500; a plain rewrite
with a typed record and a callee declared `s32` where the target does not sign-extend its result scored 110.

The source change, and the measured result before and after (scores, the judge used):

The app's isolated judge, then its own judge (0). From the 110 rewrite:

- `do { speed = 0xC00; } while (0);` around the constant one arm assigns (family `run_weight_scope`): the target's
  `$s2`/`$s3` order for the speed and the record pointer.
- the second product held in the local that held the first call's length (`dead_scratch_reuse`).
- `x = f(); x *= speed;`; a narrow call result declared `s32` and sign-extended by hand through a dead local
  (`d = f() << 16; x = d >> 16;`, the target's `sll $v0; sra $s1`); `x >>= 12; d = -x; p->m = d;` and
  `y >>= 12; p->m = y;`. The new family `call_product_stage` offers these together; applied to the state after the
  two existing families it gave 0 (140 before).

Failed experiments that would otherwise be repeated:

A fresh temporary for the negation or the shift (`temporary_hoist`, `store_result_reuse`) stayed at 10: the variable
has to be one the function uses elsewhere. A `s16` temporary for the first call and `(s16)` casts did not change the
`sll`'s register. `/ 0x1000` for `>> 12` scored 840.

What is controlled evidence and what is one function:

Each score is a measured judge run on this one function. That local-alloc ties the shift's intermediate to its
destination only for a one-block value is a hypothesis: this compiler's passes have no public source.
