---
title: Two float locals share one saved register where the target has two (-O1, unsolved)
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 383-instruction N64 sound test page,
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`. Two float values, each loaded from a signed byte, clamped, kept across
calls: the target has them in `$f20` and `$f22` (frame 0x28), plain C puts both in `$f20` (frame 0x20).

The source change, and the measured result before and after (scores, the judge used): plain C 317; the product of the
first block in a local 272; library `float_local_merge` 160, then `fence_statement_deep` 25; a redundant `= 10.0f` to the
second local inside the first value's block 10 (cse then uses that register for the next add). Not matched.

Failed experiments that would otherwise be repeated: dead stores in either direction between the two locals, parameters
instead of locals, an unused float return type and declaration order all stay at 317: flow deletes the store before
global.c builds conflicts, and global.c always reuses `$f20` for two values that do not conflict (read in `find_reg`).
SN 2.7.2 builds and `gcc-2.8.1-pm` score far worse.

What is controlled evidence and what is one function: one function. Open: a faithful source that makes the second local
live during the first block without an instruction that uses its register.
