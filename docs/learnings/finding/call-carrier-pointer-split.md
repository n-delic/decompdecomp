---
title: stray trailing pointer arguments and a reused pointer local: each alone no better, together 0
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 140-instruction menu function (a draw routine with one multiply-and-mask argument built from a call's result, a
ternary store and two stores through a pointer loaded before and after) sat at 60: the only rows were the order of the
two loads of the last call's byte and halfword arguments against the first shift of the multiply chain
(`lhu, lbu, sll` where the target has `lhu, sll, lbu, addu`).

Edits from the saved best (m2c's draft plus an earlier agent's `volatile` reads), each judged by the app's judge:

| edit | score |
| --- | --- |
| saved best | 60 |
| drop the `volatile` on the two reads | 60 |
| drop the trailing `temp_a1` / `var_a1` from `func_80022E88(3, temp_a1)` and `func_80022B60(3, var_a1)` | 790 |
| the second load into a pointer local of its own (`temp_b`) | 60 |
| both, volatile kept | 0 |
| both, no volatile | 0 |

Both edits are needed; the volatile reads were scaffolding and the 0 does not rely on them. A matched twin of the same
menu code (found with `similar.py`) had neither the stray arguments nor the reused local, which is what pointed at both.

Mechanism, read from sched.c and global.c of the pinned source (the scheduler part verified with `-fsched-verbose-2`
on the saved best and the twin, the register part measured only):
- sched.c's first scheduler runs backwards. After each insn it launches (priority `LAUNCH_PRIORITY`) the producers
  that can issue right before it, and `adjust_priority` gives that priority to an insn whose destination is live and
  set exactly once (`birthing_insn_p`: `reg_n_sets[dest] == 1`). For a call's byte argument the destination is the
  hard `$a1`, counted over the whole function: stray `a1` arguments on two other calls made it three, so `andi $a1`
  was not launched and the multiply chain (whose pseudos are single-set) was launched ahead of it. With the stray
  arguments gone the order is the twin's, `lhu, add, andi, sll, lbu, addu, sll, ...`, which the second scheduler
  turns into the target's.
- With only that edit the first pointer local took `$a2` (790): global.c's `find_reg` leaves a register out of its first
  pass when a lower-priority conflicting pseudo prefers it (the parameter's copy prefers `$a1`), and the stray argument
  had been giving the pointer its own `$a1` preference. The second load in its own local is a pseudo with its own
  short life, allocated `$a1` first, and the parameter's copy lands in `$a3` as in the target.

Failed on the way: statement orders of the three arguments (a grid of 6 orders, always 60: the scheduler decides, the
source order does not), arguments through prototypes (`u16`/`u8` parameters), multi-set multiply chains (`m = r << 1; m
+= r; ...`: 270, 120), `(s8 *)` casts, a parameter typed `u8`.

decomprepair `call_carrier_pointer_split` (0.5.650) emits the combination as one child and each half alone, for callees
declared with an empty parameter list; from the saved best its first child is the exact one.
