---
title: interpreter derived cursors and opcode local
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 359-instruction byte-code interpreter (an opcode switch in a `goto`-shaped loop; the
format opcodes set a table pointer and a format number; the draw opcodes decode three table entries per triangle in
four element formats, once as a counted loop and once as a single record, and call a plane builder). The saved best
(550 in the app's judge, 410 recorded earlier) was a pile of scaffolding: three derived cursors beside the output
pointers and the code pointer (`q = p + 2` with `q[-1]`, `q[0]` and lockstep `q += 3`, or `q[i * 3 - 1]` for the loop
counter), a byte helper that added 2 to a halfword pointer (the program wrote the first element twice), a base load held
in a local across three call arguments, an `if (x) { S } else { S }` guard, and a dead reload overwritten in one arm.

The source changes, with the measured result (the app's own judge, `fast_judge`; confirmed by `compile_and_diff`: matched):

| step | score | family |
| :-- | --: | :-- |
| saved best | 550 | |
| the cursors folded into offsets from the one pointer (`p[1]`, `p[2]`, one `p += 3` where the cursor's was) | 380 | `cursor_lockstep_fold` (new) |
| the opcode byte read once into a `u8` local for the loop test and the `switch` | 275 | `dispatch_byte_local` (new) |
| the base load written into each call argument, twice | 215, 155 | `product_inline` |
| a per-arm rename of the argument locals | 130 | `arm_private_rename` |
| the remaining scaffolding stripped | 20 | `scaffold_strip` |
| the arm's dead reload dropped, one block-scoped `u8` local per loaded value | 0 | `arm_reload_restore` (new) |

The chain is `prove_chain.py ... --families all` from the saved best: seven steps, each the best child of its step.

Why each works (loop.c, sched.c): a user cursor `q = p + 2` read as `q[-1]`, `q[0]` is a pointer GCC 2.7.2 would cut
itself: loop.c records every `p[k]` with a constant `k` as a DEST_ADDR giv, reduces it to a register of its own and
combines the `k` that share a base (`giv at ... combined with giv at ...` in the `-dL` dump), so the target's
`addiu $s2, $s0, 2` at the loop head and its `-1($s2)` / `0($s2)` accesses come from plain `p[1]`, `p[2]`. The giv's
increment is emitted just before the biv's (`emit_iv_add_mult` before `tv->insn`), which is why the target's `addiu $s2,
$s2, 3` sits ahead of the call and the explicit `addiu $s0, $s0, 3` after it; a user cursor's step is an insn of its own
that sched1 orders by LUID (the earliest of the three loop-carried adds is hoisted above the call, the rest sink in
source order), so a lockstep pair puts the wrong one first. A `u8` local keeps its QImode register: `lbu $a0, 0($s0);
andi $v1, $a0, 0xff; bne $v1, $v0` (the target's opcode test) where an unnamed read folds to a plain `lbu $v1`.

Failed experiments that would otherwise be repeated: an explicit cursor with `q[-1]` (a biv `q` with a `-1` offset is
reduced to yet another register: 2270 here); the cursors' store moved after the base's step by value (`s5[-1]` after
`s5 += 3`: 2300, the giv is based on the advanced pointer); a function-level `u8 first` instead of a block-scoped one
(205 against 0); the dead reload's overwrite just dropped (600: the reload is then a second load GCC cannot fold across
the store); `local_weight_ladder` (500 children) and the other existing families never offered the cursor fold.

What is controlled evidence and what is one function: the seven-step chain is one function. Each new family has a
synthetic test (`tests/test_cursor_lockstep_fold.py`, `test_dispatch_byte_local.py`, `test_arm_reload_restore.py` in
decomp-repair); the loop.c reading of the giv is confirmed in the `-dL` dump of this function only. The helper-width
step (`+2` bytes on a halfword pointer read as two elements) is a hypothesis the judge confirmed here.
