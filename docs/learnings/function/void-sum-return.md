---
title: void sum return
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-26
---

A small N64 loader (void, one word parameter) whose target ends with `addiu $v0, $s0, 0x80` and `jr $ra`.

Source change and score (the app's standalone judge), from the void source at 100: widening to `s32` alone is 205; making
the parameter carry the stored sum (`arg0 += 0x300; D_end = arg0;` and the reads of `D_end` read `arg0`) alone stays
100; both with `return 0x80 + arg0;` are 70; the existing `argument_weight_scope` then reached 0.

Now decomp-repair `void_sum_return` (`stored_sum_param_accumulate` for any function, `void_sum_return` for a void one
whose target ends with `addiu $v0, $reg, K` and no call after it). `prove_chain.py --families all` from the void source:
step 1 `void_sum_return` 70, step 2 `argument_weight_scope` 0. A search that keeps only single improvements never
sees the pair (205 and 100 are not improvements).

Failed experiments: `return K + D_end;` without the accumulation (205).

Controlled: one function. `return_type_widen` and `void_returned_local` did not offer anything for a void function
that returns a computed sum.
