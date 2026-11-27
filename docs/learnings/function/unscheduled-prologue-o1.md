---
title: A prologue that saves every register before copying the parameters was built -O1
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 116-instruction list lookup with four
parameters. The target stores `$ra`, `$fp` and `$s7`..`$s0`, then copies the four parameters into saved registers, and
its first loop recomputes `i * 2` each turn (no strength reduction). The project default is `-O2`, where GCC's second
scheduler pairs each store with its copy.

The source change, and the measured result before and after (scores, the judge used): the app judge. Saved C: 5112 under
`-O2`, 107 under `-O1`. Plain C: 3500 under `-O2`, 85 under `-O1`, exact with the table declared unsized (the library's
`unsized_extern_array`) and `for (i = 0, found = 0; ...)`. The brief now says to try `-O1` for such a prologue.

Failed experiments that would otherwise be repeated: `-O2 -fno-strength-reduce -fno-schedule-insns
-fno-schedule-insns2` 1715; `found` as `s32` or a second counter local 10-240.

What is controlled evidence and what is one function: of one KMC game's matched functions with at least three saved
registers and two parameter copies in the first block, all 87 built `-O2` interleaved them and all 13 built `-O1` did not.
