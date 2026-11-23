---
title: a colour-update arm ladder: guard collapse, fence unwrap and a mid-line scratch inline
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 130-instruction N64 object-colour update (KMC GCC
2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), saved best 870, 24 register rows. Arms picked three bytes of one record from a
sine, a literal, a random value or three globals. The m2c best kept an identical-arm guard (`if (temp) { S } else { S }`)
on an unassigned byte, a `do { } while (0)` around the first arm, and a function-level scratch local set to a literal in
one arm and to a global's byte just before the store in another.

The source change, and the measured result before and after (scores through the app's judge, recorded 870 -> 0):
1. Collapse the guard: 870 -> 2585 alone (the two random-colour arms then cross-jump into one block).
2. Read the global in the store (`*(...) = (G);`, the assignment `var_v0 = G;` removed): 1170 alone.
3. Unwrap the `do { } while (0)` (keep the braces): 870 alone, no change.
All three: 0. (Writing the function from scratch with one local per arm, a struct for the record and `u32` for the
unsigned global matched too; sharing one scratch local across the arms scored 2790, sharing the random arms' local 2230.)
The match relies on no scaffolding.

Why (a hypothesis from the measured results; not read in the compiler's source): jump2 merges tails after allocation
only when the arms' registers agree, so the arms' own pseudos decide which tails join; a scratch local shared between
arms, or a guard that keeps one arm's value live longer, changes which register each arm's value gets and so which tails
merge.

Failed experiments that would otherwise be repeated: literals for the `0x40` and `8` stores did not matter once the
three edits were in; splitting the shared locals by hand without the guard collapse (1170, 2745); `prove_chain.py
--families all` from the saved best found nothing (each step alone is worse), hence the combined family.

What is controlled evidence and what is one function: one function, every step scored with the app's exact judge.
Library: decomp-repair `collapsed_guard_fence_inline` (declares REPAIR; reproduces the 0 from the saved best as a
single child) and `single_use_inline`, whose adjacent form now accepts an assignment that shares a line with the
statement before it (PR RetroReversing/decomp-repair#1322).
