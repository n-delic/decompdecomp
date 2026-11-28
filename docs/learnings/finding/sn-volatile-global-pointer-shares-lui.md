---
title: A reloaded global pointer with one shared lui is volatile under SN GCC 2.8.1, not KMC
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 9-instruction N64 function storing three words through a global pointer. The target has one
`lui $v1, %hi(ptr)` and three `lw %lo(ptr)($v1)`, one before each store. Compiler:
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64` (the function's own profile).

The source change, and the measured result before and after (scores, the judge used):

The m2c draft declares `extern void *ptr;` and keeps the pointer in one register (220, the app's judge).
`extern void *volatile ptr;` with the stores unchanged is exact (0). decomprepair's `reloaded_global`
proposes this child, and `prove_chain.py` reached 0 with it in one step.

Failed experiments that would otherwise be repeated:

The same volatile source under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scores 220, the same as the
plain draft: KMC writes a `lui` before every reload (two extra instructions). A repair search run under
the KMC profile therefore sees `reloaded_global` as a neutral step. Judge a reload repair under both
profiles before discarding it.

What is controlled evidence and what is one function:

One function; the two profiles were judged on the same two sources.
