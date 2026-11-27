---
title: A narrowed word read beside a halfword read of the same offset is a pair of adjacent halfword fields
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
A 150-instruction N64 position/velocity update, last 20 points. Compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2.

The source change, and the measured result:
`(s16) FIELD(p, s32 *, 0x30)` next to `FIELD(p, s16 *, 0x30)` became halfword reads at 0x30 and 0x32 in turn. Family `adjacent_halfword_fields` (child `pair`) reproduces the matched C byte for byte from the pre-edit C. The `pair` child is not value-preserving on its own (it moves the second read); the judge decides. Judge not run on the child.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: controlled.
