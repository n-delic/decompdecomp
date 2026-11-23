---
title: flag current a1 a2 split web negative results
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

Case: a 207-instruction text-wrapping function (three sequential loops over two stack buffers), saved best at 15. The
three remaining rows are all one fact: the 0/1 flag of loop 1 and the shared `end` value of loop 2 are one variable in
the best (`$a2` in both), the target has the flag in `$a2` in loop 1 and a separate value in `$v1` in loop 2. See
`finding/allocation-flag-avoids-preferred-register.md` for the rule (the flag skips `$a1` only when a lower-priority
pseudo that prefers `$a1` conflicts with it; in the best that is the loop-2 start pointer, reached by sharing the
variable).

Split the shared variable in loop 2 (`local_web_split`-like, 15 -> 35): loop 2 is then exact, loop 1 gets the flag in
`$a1` and the character copy in `$a2` again (7 register rows). Library chain from that split: `prove_chain.py --combine 12`
over about 820 children, and all pairs of the seven split children with every child scoring at most 700: nothing below 15.

Tried on the split source, none moved the flag to `$a2` (judge scores, exact diffs read):
- Declaration order of every scalar (300 random shuffles): no effect (pseudo numbers do not decide a priority order).
- Types of the flag (`u8`, `s8`, `s16`, `u16`, `char`; `u32` is neutral), of the character copy and of the loop variable:
  narrow types change code (`andi`), none changes the order.
- The flag written as `?:`, as a value, as if/else (code changes, 535), inverted (665): `?:` and the value form compile
  identically to the `if (...) flag = 1`.
- The flag set at the top of the loop body: its live range grows from 13 to 17 insns (priority 2.77 -> 2.12), still above
  the character copy (1.17); the delay slot then takes `bnel` from the target thread and the code differs (830). Needing
  the copy first means a live length of 31 or more for the flag, or 16 weighted references for the copy.
- `do { } while (0)` around the copy's set and store (1 to 4 deep, each alone and both): the wrapper is a sched1 boundary,
  the copy's `move` moves after the compare and the score is 265 or worse.
- Duplicating the store/advance block into the second arm instead of `goto block_14` (the copy gets a fourth reference):
  622, the registers change shape (cursor and copy move to `$t0`/`$a2`).
- Sharing the flag with the loop-3 truncated value (the target also has `$a2` there): the shared pseudo takes `$a1`,
  nothing in loop 3 prefers it (55). Sharing each integer of loop 1 with each integer of loop 2 (36 pairs, retyped to
  `s32`): the one pair that helps is the flag with the `end` value, the best again.
- The character copy and the start pointer as one variable (`s32` for both, casts): 210, a different allocation again.

What a source must do: give the flag a conflict with a non-call-crossing pseudo that has a copy preference for `$a1`
and is not the loop-2 value (the call-crossing ones are pruned in `prune_preferences`). No such pseudo exists in loop 1
of this function (the second call argument is the call-crossing width parameter), so the target's source is probably
shaped differently from m2c's loop 1 in a way not yet found; `passes.py` on any candidate shows which pseudos prefer
which register.

Evidence level: observation, one function; the allocation rule itself is from global.c `find_reg`/`prune_preferences`.
