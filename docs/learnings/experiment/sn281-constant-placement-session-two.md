---
title: Four SN64 GCC 2.8.1 near-misses, second pass - loop address hoisting, global colour constant, sched2 ties
kind: experiment
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

The same four functions of one N64 game as docs/learnings/finding/sn281-constant-that-is-not-a-birth.md (profile
above; scores are `fast_judge.py` on a worktree with the PR 3640 judge): a 37-instruction init routine (saved 200), a
72-instruction object state handler (saved 70), a 122-instruction cheat-table walker (saved 220, with a two-deep
`do { } while (0)` around the outer read, scaffolding) and a 262-instruction display-list builder (saved 470).

The source change, and the measured result before and after (scores, the judge used):

None matched. What this pass established:

- Cheat-table walker. Reading the key global in the loop as `*(volatile s32 *)&G` (outer test a plain `G != 0`)
  leaves exactly two differing rows: loop.c hoists only the `%hi` (`move $a2,$a0`) and the `%lo` stays folded in the
  load, where the target hoists the whole address (`addiu $a2,$a0,%lo(G)`, `lw 0($a2)`). Score 350 because both rows
  are operand rows. Reading it through an absolute address, `*(s32 *)0x801070A0` (no volatile), gives the target's
  shape for the loop: the load stays in the loop (alias.c cannot separate a constant address from the table stores)
  and the address is hoisted into the preheader after the table-empty test, but as `lui/ori` instead of an `addiu` on
  the outer test's `%hi`: 310. cse.c `find_best_addr` replaces a register address by any equal-cost member of its
  class with a larger rtx cost, so a symbol address set in the same extended basic block is always folded back to
  `lo_sum`; a constant integer address is not a legitimate address, which is why it survives to loop.c.
- Display-list builder. A colour local that takes two values in the vertex block (`c = 0xFF; ...; c = 0; ob[2] = c;
  c = 0xFF; ...`) dies twice, so local-alloc leaves it to global.c and it gets the target's `$t6` (the second `li` is
  dropped after reload). But both sets are then non-births and the first `li` and its byte stores move to the top of
  the block: 1120-1988 depending on where the break is (`u8 c` the same). A single-set colour in the target is still
  unexplained: `$t5` is used earlier in the function, so global.c's "never allocate a register for the first time in
  pass 0" rule does not by itself account for `$t6`.
- Init routine. In both schedulers of this compiler every store in the join block has priority 1 and is ready at once
  (`-dS -dR` dumps), so the order is LUID order plus the `%hi`/`li` births launched beside their users; sched2 keeps
  sched1's order apart from hazard swaps. A tail copied into both arms (jump2 cross-jumps it back) 610/745; the first
  two stores in both arms 230; `arg0 = 0xFF` set in both arms 225 (the `li` is cross-jumped to the join start in
  `$a1`, as with a local); a pointer local `p = D_800CD108` at any of 21 positions 230 or worse (cse folds it);
  `volatile` on the candidate's extern declarations made no difference.
- Object state handler. The store written as a comma expression inside any of the three call arguments: 190, the same
  as the plain literal, because calls.c `expand_call` evaluates register arguments with side effects before the
  hard-register loads; nothing from C can be emitted between the `a1` and `a2` loads, which is where the constant's
  LUID would have to be. `k = 0x8000` at six positions: no change; inside `do { } while (0)`: 655-965 (loop notes are
  barriers for both schedulers). Without the saved best's three `volatile` pins the plain source is 190.

Failed experiments that would otherwise be repeated:

All of the above, plus: on the walker, `-fforce-addr`, `-fforce-mem`, `-fno-cse-follow-jumps`,
`-f(no-)expensive-optimizations` and `-fstrength-reduce` change nothing (220/320/350), `-fno-rerun-cse-after-loop` is
worse (420-675); the key set in the loop condition 765, in the increment 695, at the end of the body (uninitialised on
the first pass, not faithful) 315, after an explicit `if (c->codes)` guard 315 (cse makes it a copy of the outer test's
address register); `prove_chain --families all --children 60` from the volatile form: one step, `volatile_read_drop`,
back to 320. On the init routine a hill-climb over store order and five forms of the 0xFF (literal, `arg0` reuse,
local, local in both arms, early local) stayed above 300.

What is controlled evidence and what is one function:

Observations on single functions; the cse.c, calls.c, sched.c and global.c rules are read in gcc-2.8.1's source and
checked against the SN compiler's dumps.
