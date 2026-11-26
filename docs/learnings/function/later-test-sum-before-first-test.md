---
title: later test sum before first test
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 106-instruction list search with
`if (addr < p->start) break; else if (end <= p->start + 0x400) {...}`. The target has the add before the first test's
branch (`lw; addiu; slt; bnez; slt`). Compiler as in the header (the function's profile had been -O1; -O2 was right).

The source change, and the measured result before and after (scores, the judge used): the sum held in a local ahead of
the first test (`limit = p->start + 0x400;`), 215 -> 0 with the app's judge. decomp-repair `later_test_sum_stage`.

Failed experiments that would otherwise be repeated: swapping the second test's sides (`p->start + 0x400 >= end`) and
`0x400 + p->start` left it at 215.

What is controlled evidence and what is one function: one function.
