---
title: A product by ten written as the game's shift-and-add chain
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
A 30-instruction N64 volume scaler: a table byte times `arg1 * 0xA`, over 4096, held at 15. Compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2.

The source change, and the measured result:
`(arg1 * 0xA)` became `(((arg1 << 2) + arg1) << 1)`; the match followed. Family `multiply_shift_add` (decomp-repair 0.6.250) spells `x * K` as shift-and-add for K = (2^a+1)*2^b or (2^a-1)*2^b. Run on the pre-edit C it gives the matched C byte for byte. Controlled: the app's judge was not run on the family's child.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: controlled.
