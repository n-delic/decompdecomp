---
title: byte copy split weight
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---
What the function looked like and the exact compiler and flags: a 133-instruction list walk (a `for (;;)` stepping a
byte cursor through a table) on KMC gcc 2.7.2 N64, `-O2 -G0 -mips3 -mgp32 -mfp32`. It loads a signed byte index
`idx = (s8) p[0xF]`, adds a table byte into the same byte (`p[0xF] = (u8) (((u8) idx) + tbl[idx * 8 + K])`) and later
stores `idx` through a reloaded pointer. The target keeps the index in two registers (`move $v1,$a1` for the sum,
`move $s3,$s0` for the late store) and the table pointer in `$s4`; ours put the table pointer in `$s3`. Saved best 160.

The source change, and the measured result (fast_judge, the app's judge): the permuter's families (prove_chain
`--families all`, 690 children) found nothing from 160. Three edits, each no better alone:
1. the sum re-reads its byte (`p[0xF] + ...`) instead of casting `idx` (cc1 `-dr -dj`: the `(u8) idx` QI copy was shared by
   the sum and the late store, and combine substitutes it into the sum, so only one pseudo stayed);
2. the late store reads a `u8` local copied from `idx` just before the sum;
3. that store goes through a fresh pointer local inside two `do { } while (0)` wrappers: weighted references (flow.c
   counts a reference by loop depth) of the copy go 4 -> 6, priority 0.387 over the table pointer's 0.333 (`passes.py`).
   Wrapping `(*(arg0+0x68))[0xF] = idx` itself lifted `arg0`'s weight (0.784 -> 0.88) above the index in `$s0`.
Alone: 3 gives 160 -> 130, 1+2 without 3 worse (371 / 63). Together 160 -> 33 (frame and operand order left); the
frame is `unused_frame_padding_shrink` (`volatile u32 pad[4]` -> `[2]`, 18 -> 0) and `base[(i * 8) + K]` written
`(base + (i<<3))[K]` puts the base second in the `addu` (15 -> 10). A fresh local declared last gave 0; an existing
pointer local (`temp_v1_5`) stayed 10 (pseudo numbers decide a tie), the position of the new locals did not matter.
decomp-repair `byte_copy_split_weight` (PR 1276) offers 1+2+3 as one child (and each alone, and the shifted base):
`prove_chain.py` from the saved best: 160 -> 18 (that family) -> 0 (`unused_frame_padding_shrink`).

Failed experiments that would otherwise be repeated: a plain `u8` copy of `idx` before the sum (cse folds it, 160); the
`ux = temp_a1; temp_a1 = 0;` cse barrier (260-990); `+=` for the sum (288-371); the late store reading the `s32`
savedIndex copy (1071, lengthens its range); wrapping the whole store (`arg0` climbs); local types other than `s32` for
`idx` (360+).

What is controlled evidence and what is one function: one function (the scores above are its judge scores); the compiler
reading is from cc1 dumps and `passes.py` priorities, a hypothesis for the exact cse/combine lines.
