---
title: interpreter result register needs a conflict
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1315-instruction N64 sound-sequence interpreter of interpreter-default-join-skip-and-arm-registers.md (KMC GCC
2.7.2 with the app's `-G8`, the app's judge), at its saved best 785. That source ends the command switch with the
"default join skip": `default: done = 0; goto command_test;`, then `done = commandResult; command_test: if (done & 0xFF)
break;`. The target's default is `move $a0, $zero` falling into `andi $v0, $a0, 0xff`; ours sets `$v0` and jumps past
the `andi`. Not matched in this run (785 -> 785); this entry records why the obvious fixes cannot work, so the next run
starts from the right question.

The source change, and the measured result before and after (scores, the judge used):

1. Why `done` never gets `$a0` (read in global.c, 2.7.2). `find_reg` takes the first free register in number order
   (MIPS has no `REG_ALLOC_ORDER`), then the lowest-numbered copy preference, then the lowest plain preference.
   `done` lives only from the default's `done = 0` and the join copy to the test: nothing conflicts with `$v0`/`$v1`
   there, and it inherits `$v0` copy preferences from `commandResult` (the copy where that dies shares preferences,
   global.c ~790) and a plain `$v0` preference from the test's `zero_extend` temporary. Types, `register`, declaration
   order and priority cannot change that: only a conflict with `$v0` and `$v1` can.
2. A conflict does it. Case 1 writing `done = 0` itself inside its `(t = p->x, done = 0, t != 0)` test and jumping
   straight to `command_test` makes `done` live across that test's `$v1`, and `done` takes `$a0` (940 with an `s32`
   `done`: the join becomes `andi $a0, $a0, 0xff; bnez $a0`).
3. But with `done` and `commandResult` both in `$a0` the join copy is a no-op, jump2 deletes it at its start, the
   default's `j` becomes a jump to the next insn and is deleted, and in the next sweep every arm ending in
   `$a0 = 0; j join` cross-jumps into the default's `$a0 = 0` (jump.c: the insns before the jump's own label first).
   `get_label_before` returns the default's *case label* (an old label), so the following sweep merges the redirected
   arms' bodies through `jump_chain` (u8 `done`: 22100; the single-variable `default: commandResult = 0;` form: 23385,
   also with an `s32` result tested `& 0xFF`). The skip structure works only because `done` is not in `$a0`.

So the default's register and the arms' merging are one problem. In the target the default's `$a0 = 0` sits right
before the join and 19 arms jump there with `move $a0, $zero` in their delay slot (reorg stealing it from the default
after a cross-jump), yet no arm bodies were merged. Hypothesis (not confirmed): at jump2 the insn before the default's
`$a0 = 0` was not its case label, so the cross-jump made a new label that `jump_chain` ignores; something invisible in
the output (a `CLOBBER`/`USE`, or an insn reorg later moved into a delay slot) or a default whose zero differs in mode
from the arms' (`(set (reg:SI a0) 0)` against `(reg:QI a0)`, which `rtx_renumbered_equal_p` keeps apart).

Failed experiments that would otherwise be repeated:

- `u8 done` tested `if (done)`: 890 (`done` still `$v0`, plus a `move`). `done = 0` before the switch with
  `default: goto command_test;`: 5795 (`$a0`, but the zero lands in the range check's delay slot and the call tails stop
  cross-jumping); the same with `done = 0` also in the default: 890 (the early store is dead).
- The command fetch: a non-volatile pointer 1140; a pointer to the source words or the stored word in a local 785
  (identical code); an 8-byte struct copy of both words 1395; one function-level `cmd` pointer shared with the two
  cases that store through it 1130/1485. `&D` is still folded into `%lo` addresses.
- The pan clamp (target: `andi $v0, $v1, 0xffff` before `sltiu`): 48 combinations of the sum's type (`s16`, `u16`,
  `s32`), the clamp temporary's type (`u16`, `s16`, `s32`, `u32`), stored-then-read versus computed-then-stored, and a
  `(u16)` cast in the compare: 785 at best, three spellings compile identically.
- The init block's `ori` (target: after the first two loads): `0x40000000 | 44100`, the constant in a local set before
  or after the first load: 785, identical; the `0x5C` store at each of the 25 positions in the block: 785 at best.
  sched1's verbose dump shows why: after the split the `lui`/`ori` pair sets one pseudo twice, so the `ori` ranks 1
  while the two loads carry the birthing boost (`7f000001`) and are launched ahead of it.

What is controlled evidence and what is one function:

One function, each candidate scored with the app's judge (fast_judge); the allocation and cross-jump readings are from
the 2.7.2 sources (global.c `find_reg`, jump.c `find_cross_jump`/`get_label_before`) and the `-dl -dg -dS` dumps. The
new-label explanation for the target is a hypothesis.
