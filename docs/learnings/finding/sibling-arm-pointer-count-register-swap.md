---
title: Mode dispatch whose pointer, count and flag registers are swapped (KMC -O1)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Five N64 predicates of 67-75 instructions in one family: a count guard, then `if (mode == 0) .. else if (mode == 3)`,
each arm comparing a two-byte record through a pointer and setting a flag. Logic and opcodes matched; the target keeps
the count in `$a0`, the first arm's pointer in `$a1` and the flag in `$a2`, and our builds permuted them (scores 40, 55,
70 as saved near-misses). Compiler `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (a per-function profile).

The source change, and the measured result before and after (scores, the judge used):

The app's judge, isolated, every one exact (0):

- one pointer local per arm (not one shared local), and the first arm's `if` chain in `do { } while (0);`
  (two functions: 40 and 75 -> 0; either step alone scored 55 or no better);
- where the first arm reads the guarding global only after its first compare, that global held in a local used by the
  guard and that arm (the other arms reload it) *and* only the `if` after the `else` wrapped,
  `else do { if (..) { flag = 1; } } while (0);` (two functions: 415 and 425 -> 0);
- the fifth function (a different shape) was plain C under `-O1`: a `u16` parameter used as an index into a
  100-byte record, and a flag set inside the guard (0 on the first try after about 40 attempts around 105).

The permuter had each part except two: `run_weight_scope` offers statements of a block, not the `if` after an `else`,
and nothing held a guarding global. decomp-repair `sibling_arm_scope` now offers the combinations as single children
(proven 40, 75, 415, 425 -> 0 on the four functions).

Failed experiments that would otherwise be repeated:

Declaration order; caching the count in a local for every arm (1120); wrapping the whole guard or the whole first arm
when the count is held (70); one shared `goto` label for the flag set (3120, the flag's weight is not the lever);
joining an arm's tests with `||` (the pointer folds into direct accesses, 540 and up). Under `-O1` the cse pass does not
follow the branch into an `else`, which is why the held local matters there (a hypothesis, from the dumps' behaviour).

What is controlled evidence and what is one function:

The two wrapper shapes were each measured on two functions with the step alone as control. The allocator explanation
(priority is `floor_log2 (refs) * refs / live`, one more reference per loop) matches the `-dl -dg` table's numbers but was
not read in the compiler source for this case.
