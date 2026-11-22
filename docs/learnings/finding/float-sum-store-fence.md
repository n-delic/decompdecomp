---
title: a float sum split around a run of stores needs its first add's operand order and a one-shot scope inside the run
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 113-instruction bounding-sphere function (min/max over an array of 12-byte points, then `length = x2 + y2 + z2`
with six min/max stores between the two adds) sat at 25 with only the three products' registers and the order of
the adds and stores wrong. The saved best was m2c's `length2 = y2 + x2; <3 stores>; length2 += z2; <3 stores>`.

Edits, each judged by the app's judge (`fast_judge`, then `compile_and_diff`):

| edit | score |
| --- | --- |
| saved best | 25 |
| first add as `x2 + y2` alone | 220 (product registers right, the first store hoisted above the third product and the min/max register shared with it) |
| `do { } while (0);` after the second of the three stores, original operand order | 10 |
| scope after the first store, with the swap | 60 |
| no scope, with the swap | 220 |
| swap + scope after the second store | 0 |

The scope works as sched.c's loop-note rule says (the insn after a loop note depends on everything before it); that the
operand order decides the products' registers and where exactly the scope belongs are measured on this function only
(hypotheses). Found with `grid.py` over the store statements, the scope and the add (40320 orders, 0 for eight of them).
Not found by the library's existing families (`prove_chain.py --families all`: no child improved on 25, because neither
half alone does): decomprepair `float_sum_store_fence` emits the swap, each scope position and their combinations; from
the saved best its last child is the exact one.

Failed: permuting the six stores and the add without a scope (best 20, a store pair swapped); a scope around the
second half of the run (260); scopes after the second add (60).
