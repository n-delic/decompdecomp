---
title: state machine volatile pin and plain field reads
kind: function
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 603-instruction N64 object state machine (KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): a jump-table switch over a
kind byte, each case updating two bit-packed words of the object (a 14-bit state field at the word's low end, a 6-bit and
a 9-bit field of the next word), saving the object's three float coordinates to three globals and zeroing them. Saved best
1695, 20,000 automatic trials, 73 failed trials behind it. Matched (recorded 0) by hand in one session.

The source change, and the measured result before and after (scores, the judge used; fast_judge unless noted):

1695 saved best -> 0, in this order (each row the score after that edit):

| edit | score | decomp-repair family |
|---|---|---|
| m2c's mask-and-shift word updates as bit-field updates (`p->state \|= 0x10;`) in the case arms | 1695 -> 1675 | `field_rmw_bitfield` |
| the scratch mode local used by two unrelated goto ladders split in two | 1675 -> 1370 | `region_local_split` |
| `((Rec2C *) p)->value` one-member-record reads written as `*(f32 *)((char *)p + 0x2C)` (seven sites at once) | 1370 -> 415 | `record_member_plain` |
| the zeroing local + `do { } while (0)` fence as `*(p+0x24) = *(p+0x28) = *(p+0x2C) = 0.0f;` | 415 -> 285 | `float_literal_chain` |
| join tails copied into the arms (flag set, mode store), the four small field arms as bit-fields | 285 -> 230 | `goto_body_copy`, `field_rmw_bitfield` |
| the second ladder's arms placed after the tail so the default arm falls into the join | 230 -> 180 | not yet a family |
| the lone arm's mask and result written through the result local the sibling arms share (`var = MASK; var = t & var; var \|= ...`) | 180 -> 120 | `rmw_result_local` |
| two pairs of neighbouring accesses made `volatile` (mode store + first coordinate load; flag word load + first coordinate load) | 120 -> 60 -> 0 | `volatile_access_pin` |

Recorded by the app's judge: 0, "byte-identical". Chaining the families from the saved best (prove-chain style, fast_judge)
reaches 1695 -> 740 -> 435 -> 375 -> 315 -> 280 -> 250 with `record_member_plain`, `region_local_split`,
`volatile_access_pin` x2, `join_tail_arm_split` and `float_literal_chain`.

Failed experiments that would otherwise be repeated:

- Statement order of the three statements in the lone arm (the word read, the coordinate read, the mode store) in all six
  orders: identical code. The scheduler's order for equal-priority loads is not the source order.
- Every arm written out in full instead of jumping to a shared tail: 6350-7960, the cross-jump no longer merged them.
- A bit-field store for the mode half-word: it moved the store after the three coordinate loads (an in-struct store does
  not conflict with a load from a scalar global).
- `volatile` on the mode store alone: nothing; both accesses of a pair must be volatile (sched.c `true_dependence`).

What is controlled evidence and what is one function:

Controlled on this function: every row above is a measurement of one edit on the state before it. The rules behind them
are read from the compiler source (sched.c `true_dependence`/`read_dependence`, `rank_for_schedule` and
`LAUNCH_PRIORITY`; local-alloc.c `block_alloc`'s three-quantity sort; see the finding on it). One function: whether the
original source used `volatile` fields is not known; `volatile_access_pin` asks for the compiler's order, not the game's
declaration.
