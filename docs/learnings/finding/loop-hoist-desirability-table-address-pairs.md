---
title: loop hoist desirability of table address pairs
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What decides whether loop.c (GCC 2.8.1, SN64's cc1n64) hoists a table's `lui/addiu` pair out of a loop with calls:
move_movables moves a movable when `threshold * savings * lifetime >= insn_count`; threshold starts at
`1 + n_non_fixed_regs` (61 on MIPS -mips3 here) and drops by 3 for every register already moved in that loop. The
high/lo_sum pair is one movable: lifetimes add (high 1, lo_sum up to its plus) and the forced pair doubles savings
to 2. So a pair with life 4 in a 450-insn loop (61 * 2 * 4 = 488) is moved and one with life 2 is not.

What sets the life: an ARRAY_REF used as a value (`v = T[m]`) expands the table address before the index (high, lo,
index load, shift, plus: life 4); `&T[m]`, `T + m` and every cast spelling expand the index first (life 2), and an index
cse already has makes the pair short too. Two tables indexed by the same `m` in one loop: the first one written as a
value is hoisted, the second is not.

What keeps a pointer local from being moved: scan_loop moves a user variable set from an invariant only when the set
is sure to run before its uses (`!maybe_never`), or when every use is in the set's own basic block
(`reg_in_basic_block_p`, which reads the first and last use over the whole function). A pointer local set in a loop
arm and also used elsewhere in the function is never moved, so only the lone high part is left, which is not worth
moving: the address is built in place (the level-loader match, function/spilled-counter-postinc-and-table-pointer-user-var.md).

Knock-on effect: each moved register lowers the threshold for the movables after it, so hoisting one table more or less
can make an unrelated later movable (a frame address used by three calls, life 24, savings 1: 13 * 24 < 450 against
19 * 24 >= 450) flip, and the whole function's allocation with it (seen on a 450-insn shadow-probe loop, still open:
the target hoists the second table and not the first).
