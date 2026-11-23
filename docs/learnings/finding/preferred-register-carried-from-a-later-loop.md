---
title: preferred register carried from a later loop
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: the 207-instruction text-layout function of
function/text-layout-loop-shapes (KMC GCC 2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`), saved best 15. A sentence loop's 0/1
flag outranks the character copy beside it in global.c, and its pass-0 `find_reg` skips only the registers in
`regs_someone_prefers` (the full preferences of LOWER-priority pseudos that conflict with it, `prune_preferences`).
Nothing in that loop prefers `$a1`, so the flag takes the lowest free register, `$a1`; the target has it in `$a2`. The
earlier fix (flag named also for the scan's `c == 0`, conflicting with the `$a1`-preferring start pointer) left that
value in `$a2` too (finding/allocation-flag-avoids-preferred-register).

Rule, read in global.c and loop.c (what decided it):
- A conflict that pushes the flag off `$a1` can come from any later region that has no calls: here the last loop
  (scale every table entry). The flag's variable holds the loop's truncated scale (`flag = (s32) scale;` before the
  `do`, the `sh` reads it: target `mfc1 $a2` there), and the loop bound, which the target copies into `$a1`, is kept in
  the line-start pointer's variable (an `s32` read through casts in its own loop) that `memcpy(buf, start, n)` copies
  into `$a1`: the bound prefers `$a1`, the merged flag conflicts with it and avoids `$a1`.
- `expand_preferences` merges the preferences of a copy's destination and a dying, non-conflicting source in both
  directions. The count copied into the bound would inherit `$a1` and take it (the target has it in `$a0`): 325. Naming
  the count with the line counter of the previous loop, which conflicts with the start pointer there, stops the merge.
- loop.c inserts hoisted invariants after every statement written before the `do`, and sched1 keeps the original order
  of equal-priority insns, so the target's `lui $at; mtc1` (the 4096.0f divisor) before `move $a1,$a0` needs the
  constant written in the preheader before the bound's copy: a float local (scaffolding).

The source change, and the measured result before and after (scores, the judge used; `fast_judge`, the app's judge):
15 (saved best) -> 0 with all of: the flag's later web split off, `flag = (s32) scale;`, `scale = 4096.0f;`,
`start = count;` before the `do`, `while (i < start)`, the count as the line counter. Each alone or in part: the flag's
merge with the truncation 55 (merged pseudo `$a1`), plus the bound 325 (count in `$a1`), plus the count 60 (copy before
the constant), all 0. decomp-repair `loop_preheader_role_reuse` emits the combination as one child (decomp-repair #1323).

Failed experiments that would otherwise be repeated: the character copy as an `s32` merged with the start pointer
(the copy turns into the sign extension: 210); `s32`/`s16` loop-carried characters (frame and extension rows, 812+);
natural `while ((c = *p) != 0)` rewrites of the sentence loop (1500+); the sentence loop's tail duplicated into both arms
(600+); the bound copy at the top of the scale loop's body, or after its table read (60), or at its end (not hoisted:
loop.c moves a user variable from after a jump only when it is used in one block; 170); the copy before `if (count > 0)`
(cse makes the copy canonical and the test reads it: 325).

What is controlled evidence and what is one function: one function; the global.c and loop.c rules are read in the
source, and each step's effect is measured on this function only. The int holding a pointer and the constant local are
reported as scaffolding.
