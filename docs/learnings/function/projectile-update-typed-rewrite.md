---
title: a 678-instruction projectile update from a typed rewrite, compare operands and a call-sum split
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 678-instruction N64 projectile update
(hit test, chain of effect spawns, ten particles from a pool), SN GCC 2.8.1 with the flags above. The saved best was
an m2c draft at 13681.

The source change, and the measured result before and after (scores, the judge used): a typed rewrite from the
target's assembly (one entity record, one particle record) scored 12283, then 2370, 510 and 0 (the app's judge).
Undoing one edit of the exact source at a time (the app's standalone judge):

- `x >= (f() ? 2 : 1)` instead of `t = 2; if (f() == 0) t = 1; x >= t`: 4500 (`compare_call_operand_local`).
- `x >= (f() != 0)` instead of the flag in a local before the `if`: 405 (the same family).
- `for (j = 0; j < 10; j++)` instead of `for (;;) { if (j < 10) .. else break; }`: 2050 (`loop_else_break`, 0).
- `p = pool; a = f() + K;` instead of call, `p = pool`, sum: 435; the split alone stays 435
  (`call_sum_address_split`).
- `i++; p++;` instead of `p++; i++;`: 170 (`independent_statement_swap`).
- two spilled `s16` locals declared after an `s32` one: 4 (`spill_local_orders`, which now takes halfword locals).

Failed experiments that would otherwise be repeated: `a = f(); p = pool; a += K;` (one variable set twice) lost the
spill slot and scored 637; `(u32) (f() != 0)` written in the comparison loaded the other side before the call (535).

What is controlled evidence and what is one function: each row is controlled on this function only. Why the compiler
folds the operand written inside the comparison is a hypothesis (SN's compiler has no source).
