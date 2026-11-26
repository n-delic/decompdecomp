---
title: A word read through a loaded pointer at the target's load width
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

An N64 per-object update of 124 instructions, built with KMC GCC 2.7.2 at -O1 while the project's default was -O2
(plain C with the object table declared as records matched at -O1 by itself). The saved C, a search's, read the
object's first state byte as `*((s32 *) (*((s32 *) (table + off))))`; the target reads it as `lw $v0` then
`lbu $v0, 0($v0)`.

The source change, and the measured result before and after (scores, the judge used):

Write the outer read at the target's width, `*((u8 *) (*((s32 *) X)))`. With that one change the last differing row
went (200 -> 0). No field offset or symbol names such a read, so `field_width` skipped it and a chain of every library
family stopped at 200. With decomprepair `loaded_pointee_width` (0.6.148), `prove_chain` from the stripped saved C
under -O1 runs 360 -> 160 -> 145 -> 0 (`loaded_pointee_width`, `flag_temp_inline`, `masked_value_reuse`).

Failed experiments that would otherwise be repeated:

The saved C under -O2 (615) carried identical-arm guards and a `do { } while (0)` ladder; under -O1 it scored 1000,
so the -O1 probe of the saved C alone gave no hint. Its scaffolding-free form scored 360 under -O1.

What is controlled evidence and what is one function:

The regression test covers the rewrite; the chain is measured on one function.
