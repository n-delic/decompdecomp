---
title: bitwise condition local
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-26
---

A small N64 counter function: a halfword field incremented, then clamped to a limit when it is below it and a
global flag is not 2, then a test of the clamped value.

Source change and score (the app's standalone judge): the saved best tested `(((s16) temp_v0) < 0xA0) & (D_flag != 2)`
inline and left the phi variable (`var_a1`, set to the limit in the body and tested after) unset on the other path: 225.
The hand match sets `var_a1 = (s16) temp_v0;` before the `if` on every path and tests `(D_flag != 2) & (var_a1 < 0xA0)`.
Neither step improves alone: the local with the old operand order is 70, the flag first with the inline cast is 295,
both together 0.

Now decomp-repair `bitwise_condition_local` (children: local with the flag first, local in the original order) and
`bitwise_condition_swap` (operands exchanged only). `prove_chain.py --families all` from the saved best reaches 0 in
one step (81 children).

Failed experiments: swapping a `&` condition without the local; the local without the swap.

Controlled: the family recovers the hand match from the saved pre-match source. One function; the operand order rule
is a hypothesis (the front end orders the compares by operand order).
