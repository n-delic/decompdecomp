---
title: A pointer local that carries its widened index, a stored temporary read back from its frame slot, and a flag m2c made of a delay-slot compare
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g; sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float; gcc-2.8.1-pm-sn-as -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Three functions of an N64 racing game, each a few rows from exact, judged by the app's judge:
- A record initialiser (77 instructions, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g`): an `s16` slot index kept
  unextended in `$s1`, then a record address `slot * 0x24 + TABLE` in `$s0`. The target extends the index straight
  into `$s0` (`sra $s0,$v0,16; sll $v0,$s0,3; addu $v0,$v0,$s0; ...; addu $s0,$v0,$v1`); the draft extended it into
  `$v0` and used `$v1` for the product: seven register rows, 35.
- A projection routine (109 instructions, `sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float`): `t = acc >> 12;
  sp0[0] = t;` and later `(f32)(G * t)`. One row: `mult $t0,$v0` against the target's `mult $v0,$t0`, 5.
- A scaler (89 instructions, `gcc-2.8.1-pm-sn-as -G0 -mips3 -mgp32 -mfp32 -O1`): m2c wrote the compare reorg had put
  in a branch's delay slot as a flag local (`f = a < 0xAF0; if (a > 0x1000) { ...; f = a < 0xAF0; } if (f)`): the
  compare landed in `$v1` instead of `$v0`, 15.

The source change, and the measured result before and after (scores, the judge used):

- Record initialiser, 35 -> 0: the pointer local holds the widened index first, then the address:
  `rec = (void *)(s32)slot; rec = ((s32)rec * 0x24) + TABLE;` (one pseudo for both values; the allocation reading is a
  hypothesis). decomp-repair family `pointer_index_carrier`, which emits exactly this child from the saved best.
- Projection, 5 -> 0: read the stored slot back, `G * sp0[0]`; cse still uses the register, but the operand order of
  the commutative `mult` follows the source (hypothesis). decomp-repair family `frame_slot_read_back`.
- Scaler, 15 -> 0: plain `if (a > 0x1000) { ... } if (a < 0xAF0) { ... } else { ... }`. The existing family
  `delay_slot_duplicate` reaches it in one step from the saved best (prove_chain --families all --children 40).

Failed experiments that would otherwise be repeated:

- Record initialiser: `arg3 = slot;` before the address, an `s32 i = slot;` block local, `&TABLE[slot * 0x24]` and
  `(s32)arg3 * 0x24` all stayed at 35; separate raw and `s16` index variables were 895 (an earlier agent).
  `prove_chain --families all` went 35 -> 20 in one step and then no child of 565 improved.
- Projection: `temp_t0 * G` (operands swapped in C) stayed at 5, so did `sp0[0] = t = acc >> 12`; `sp0[0] * G` is 5.
  No library family of 437 children improved on it.
- Scaler: the flag kept in `var_v0` stayed 15; in `var_v0_2` 125 (an earlier agent).

What is controlled evidence and what is one function:

Each result is one function, measured with the app's judge (fast_judge and the app's compile_and_diff agree). The
families were proved by running their `variants` on the saved bests: each gives a child the judge scores 0. Not a
controlled study of the passes involved.
