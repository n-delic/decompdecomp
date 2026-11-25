---
title: level loader invariant sum and one arg call
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 629-instruction N64 level loader (SN64 GCC 2.8.1; scores under the `-mfp32` profile with the app's judge-worker): four
passes over a record stream, then an object-type filter. op83's rewrite from the asm scored 1442; now 615, not matched.

The source change, and the measured result before and after (scores, the judge used):

- A callee whose matched C takes one argument (`signatures.py` said so): the `0x12` m2c passed in `$a1` is the value of a
  later `e->fF = 0x12` store, hoisted by loop.c and caller-saved around the call (`sw/lw $a1, 0x130($sp)`); dropping the
  argument was worse alone (1442 -> 1472) and right once the loops below were fixed (1530 -> 1060 with them).
- Spill slots follow declaration order (pseudos are numbered at their declaration and `alter_reg` gives slots in
  pseudo order): declaring the four spilled scalars in the target's slot order fixed every stack row.
- A constant local compared in three loops written as the literal (`sentinel_local_inline`): loop 1's literal is then
  hoisted into `$a1` as in the target, loops 2-4 materialise it in place.
- An entry walk `e = tbl; .. e++` written through the function's generic cursor (`p = (s32 *)tbl; e = (Entry *)p; ..;
  p = (s32 *)(e + 1);`) gives the target's loop-made pointer in `$a0` with its saved copy: 1615 -> 1285.
- `base + i + 1` staged as `t = base + 1;` inside the loop (`i + t`): every in-place spelling folds the literal outward
  or shares `i + 1` with the counter, so nothing computes `base + 1`; the staged local is a movable invariant loop.c
  hoists into `$s0`: 1060 -> 625. New decomp-repair family `loop_invariant_sum_stage` (PR 1730).
- `cp = &TABLE[idx]; *cp = cnt;`: 625 -> 615. The natural `TABLE[idx] = cnt` expands lui, lo, sll, add (the target's
  order) but loop.c hoists the address (`-dL`: life 3, savings 2, moved; it is then rematerialised through `$t1`, which
  flips every later `$t0`/`$t1` reload). The pointer form has life 2 and stays, but expands the index first.

Failed experiments that would otherwise be repeated:

Loop 3's last rows (the target's `i++` before the call and the spilled counter's store in the jal slot) are invariant
under 24 spellings (post-increment in the argument, separate statements, `for` header, staged `e->fF`, `u8` prototype):
the byte load is QImode, and sched.c's `true_dependence` exempts in-struct varying accesses only for non-QImode, so it
cannot pass the spilled counter's store. Nine other spellings of the table store: 615-2200.

What is controlled evidence and what is one function:

One function; the threshold explanation (`move_movables` lowers `threshold` by 3 per register moved) is read from loop.c
and the `-dL` dumps, not tested in isolation.
