---
title: interpreter g0 join width pan cast 785 to 0
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1315-instruction N64 sound-sequence interpreter of interpreter-result-register-needs-a-conflict.md and
interpreter-default-join-skip-and-arm-registers.md, saved at 785 under the project's KMC GCC 2.7.2 `-G8` profile.
Matched (app judge: 0) with a per-function profile of `-G0 -mips3 -mgp32 -mfp32 -O2`, the flags the compiler map
already gave for this address range (the brief said so). Four source steps and the profile; each source step is a
decomp-repair family whose child reproduces it.

The source change, and the measured result before and after (scores, the judge used):

1. The join result (785 -> 670, under -G8). The arms' result `s32` and the tested flag `u8`, with case 1 setting the
   flag in its test (`(t = p->x, done = 0, t != 0)`) and going to the test instead of `break`. The conflict gives
   the flag `$a0` (global.c `find_reg`); the arms' zeros are then `(set (reg:SI a0) 0)` and the default's
   `(set (reg:QI a0) 0)`, which `rtx_renumbered_equal_p` keeps apart, so jump2 no longer cross-jumps the arms into the
   default's old case label (the 22100 merge of the earlier note); the arms cross-jump into another arm's zero at a
   new label, which `jump_chain` ignores. The word-to-byte copy is a truncation, no `andi`. Grid of result x flag
   types: u8/s8 flag with s32/u32/s16/u16 result 670; same widths 22100; s32 flag 880-1235.
   [`join_skip_width_conflict`]
2. The profile: `-G0` (670 -> 535 with the same source). Under `-G8` every symbol is `SYMBOL_REF_FLAG` (small data),
   `mips_address_cost` gives it 1 like a register, and cse's `find_best_addr` prefers the symbol on a cost tie, so the
   command word's pointer is folded into `%lo` addresses; under `-G0` a symbol costs 2 and the pointer stays in `$v1`.
   This was the "fetch in `$v1`" row every earlier run tried to spell.
3. Case 0x19's byte from the command word read as `(s8) (D >> 16)` instead of `(s8) *(u16 *) &D` (535 -> 165):
   `*(u16 *) &D` goes through `memory_address`, whose pseudo cse replaces with the arm's `cmd = &D` pointer (now a
   cheaper register); the variable's own MEM is never replaced and combine narrows the shift to `lhu`.
   [`symbol_high_half_read`]
4. The init block's `ori` (165 -> 105): `p->f8c = 0;` moved ahead of the block's two field loads; a grid of every
   single-statement move in the block found only that store. Scheduler reason not traced (hypothesis: the store
   gives the loads a memory dependence). [`init_store_before_loads`]
5. The pan clamp's `andi $v0, $v1, 0xffff` (105 -> 0): the delta written `(s8) (D >> 8)` instead of m2c's
   `((s32) (D << 16)) >> 24` (same `sll`/`sra`). With the shift pair combine folds the u16 compare's zero extension
   (`sltu` on the raw sum); with the cast it keeps it. Found with cc1 snippets: of ~1700 spellings of the clamp
   (types, compare forms, sign tests, shapes) only the delta's spelling mattered. [`shift_pair_sign_cast`]

Failed experiments that would otherwise be repeated:

- Pan clamp, under -G8 and -G0: sum type s16/s32/u16/u32 x clamp type x stored-then-read/computed-then-stored x
  compare `t >= 0x80`, `(u16) t`, `(t & 0xFFFF)`, `0xFFFFU`, `(u32)(u16)` x sign test `(sum << 16)`, `(s16) sum`,
  `sum`, single-variable `if/else if` and separate `if`s: never better than the saved spelling (an `(t & 0xFFFF) >= 0x80`
  int compare keeps the `andi` but is `slti`; a u16 local read back as s32 puts the `andi` before the branch). The
  clamp was never the problem.
- Case 0x19: `cmd = &D` at every later position (the pointer is hoisted out of the loop, 760-1866); other spellings
  of the read (`((u16 *) &D)[0]`, volatile, s16) 535.
- A `u8` flag tested `if (done)`, or a variable live at entry for the clamp temp: no change.

What is controlled evidence and what is one function:

One function; every step scored with the app's judge (fast_judge with `--flags=-G0`, then `try_compiler`/`suggest`,
which recorded the match). The jump.c, global.c and cse.c rules are read from the 2.7.2 source; the scheduler and
combine explanations of steps 4 and 5 are hypotheses. The volatile `frameSpace[12]` frame scaffolding from earlier
runs is still in the matched source.
