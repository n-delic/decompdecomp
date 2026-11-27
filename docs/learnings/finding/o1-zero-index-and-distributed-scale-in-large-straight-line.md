---
title: O1 zero index, distributed plus-one and loop-top locals in large straight-line functions
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

Seven N64 functions of 601 to 3616 instructions (menu state machines, effect spawners, a HUD, a sound updater) judged
exact under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`. Most were exact within a few compiles from a source
generated from the target's assembly by a small symbolic reader (follow the jumps into cross-jumped tails; skip the
delay slot of a likely branch on the fall-through path).

- **`andi $s0,$s0,0xffff; jal; move $a0,$s0` for an argument that is always 0.** A `u16 player = 0;` that also indexes
  `pads[player]` after the call. The flow pass counts it live across the call before combine folds the index into an
  absolute `lui; lw %lo`, so the global reads as a plain symbol in the target. Absolute reads in C give `move $a0,$zero`.
  Library: `zero_index_member`.
- **`a * s + s` in a draft is `(a + 1.0f) * s` in the source.** Same `mul.s`/`add.s`, one use of `s` fewer, which
  reordered two saved registers (147 register rows to 4). Library: `unit_term_product`.
- **A constant address or float rematerialised through `$t0`/`$f4` inside a loop, where ours loads it straight into
  `$v0`/`$f0`.** loop.c moved it and no register was left. A pointer local assigned once at the top of the loop body
  (`post = TABLE;`) is moved too (270 -> 0 on 3616 instructions).
- **A record index computed before an unrelated constant is set.** `n = (u16) (arg * 5);` ahead of the constant,
  then byte-offset reads of the record (60 -> 0).
- **A loop-invariant `off + K` added after a product** stays unfolded with a narrowing cast on the sum:
  `y * 3 + (s16) (off + 0x48)` (45 -> 0). A local for it lost the register order.
- **A call argument computed in both arms of an `if` and not cross-jumped:** write the call in each arm, so each arm
  sets `$a0` its own way (a sign-extended copy in one, a fresh extension in the other).
- **Value-producing forms take a pointer.** `if (--rec[i].timer <= 0)` and `if ((rec[i].state = f()) == 1)` address the
  field through the record's base register; the two-statement spellings use `lui $at` (1340 -> 0).

Failed: an 875-instruction text-menu loop stayed at 3145 (a loop-invariant row base should outrank a constant stack
argument for the last saved register, and `slti $v0,$s1,1` for a one-pass loop came out as `blez` in every spelling).
