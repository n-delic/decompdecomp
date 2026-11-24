---
title: projectile update 2503 cross jump and sched1
kind: function
compiler: KMC gcc 2.7.2 (-G0 -mips3 -mgp32 -mfp32 -O2)
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 2,503-instruction projectile/state update of an N64 game continued from
`projectile-update-2503-struct-copies-and-records` (2285, 63 rows), KMC gcc 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`.
Worked with `fast_judge.py`, the app-faithful cc1 from `preprocess.py` and its `-dJ -ds -dc -dS -dg` dumps.

The source change, and the measured result before and after (scores, the judge used):

App's judge (fast_judge, each new best confirmed with `compile_and_diff`), in the order applied:
- the jump table row (`%hi(?local)` against `jtbl_...`) was not a real difference: the judge places the candidate's
  own table at the target's address and checks its words, and the case targets were 12 bytes off while three
  instructions were missing. It went away by itself once the instruction count matched.
- the second arm's impact tail (spawn + blast calls) duplicated from the first instead of `goto`ing into it, so jump2's
  cross-jump merges only from the first differing insn back (`move $a1, $s0`): 2285 -> 3370 at first, because
  cross-jumping redirects the *earlier* of two jumps to one label into the later copy (jump.c: the jump being visited
  is the one rewritten). Stream 1 of `find_cross_jump` stops at a label, stream 2 skips labels: a label right before
  the first arm's jump (`block_tail: do { goto block_263; } while (0);`, the second arm `goto block_tail;`) keeps the
  first arm's code and sends the second into it: 3370 -> 1475 (the loop note stops jump1 threading the label away).
  This is scaffolding (a `do {} while (0)` around a goto); the faithful spelling is still open.
- two branches' spark counts in one pair of locals (`var_s1_2`/`var_s4_4` for both `if (gTargetObject)` and its
  `else`): 1475 -> 1225.
- the m2c comma chain that stores `flags | K` as an if/else-if chain of stores: 1225 -> 1200.
- cast-pointer stores right before a read of a plain global as anonymous-struct member stores (MEM_IN_STRUCT_P never
  aliases a fixed scalar in gcc 2.x, so the global's load moves above and the store fills the delay slot):
  1200 -> 1060 (decomp-repair `member_store_global_load`).
- a pointer local assigned after the call it is passed to, the call given the expression (`var_s0 = arg0 + 0x24`
  after `spawnConfiguredEffect(..., arg0 + 0x24, ...)`): 1060 -> 1000.
- a callee m2c declared `(f32 *, s32)` called with `0x3B03126F`: `(f32 *, f32)` and `0.002f` (the float goes in `$a1`
  as one `li`, which changes the order of the argument setup): 1000 -> 680.
- `a < b` as `b > a` for one float compare (register order of the two loads): 680 -> 665.
- `v = (dot + 1.0f) * 0.5f` as `v = dot; v += 1.0f; v *= 0.5f;`: 665 -> 625.
- the entry constant `var_fs1 = 65536.0f` moved below the first 12-byte struct copy (sched2 kept it at the top while it
  was among the first statements; after the block move it stays in source order): 625 -> 295.
- `G->58 &= -0x1C1` as `{ s32 t = -0x1C1; t &= G->58; G->58 = t; }`: two sets of the mask's register take it out of
  sched1's `birthing_insn_p` boost, so it loads first in the block and local-alloc gives it `$v0`: 295 -> 5
  (decomp-repair `mask_local_twice_set`).

Result 2285 -> 5 (one register row): the target's `and $v0, $v0, $a3` has the mask register first; ours is
`and $v0, $a3, $v0` because cse's fold_rtx puts an operand it knows to be constant second. The target's mask register
was not known constant to cse but was a block-local register.

Failed experiments that would otherwise be repeated:

- the mask spelled `~0x1C0`, `0xFFFFFE3F`, `u32`, a record member, operands swapped, a set-once mask local, a mask
  set before the call (crosses the call: 3000+), a far function-level mask (rematerialised by reload into `$t0`, right
  operand order, wrong registers: 280), a bitfield clear (425), loop notes around it (900+): none closes the last row.
- duplicating the tail without the label (cross-jump goes the wrong way, 3370); a label alone or a dead store after it
  (jump1 threads it away).

What is controlled evidence and what is one function:

One function; each number is the app's judge on the full function. The two library families reproduce their steps
from the state before them.
