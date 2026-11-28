---
title: A draft with a leftover MULTU_HI remainder was exact once repaired and moved to the SN profile
kind: experiment
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 31-instruction N64 leaf that writes seven decimal digits into a linked list of sprites. The m2c draft kept
`temp_hi = MULTU_HI(x, 0xCCCCCCCD)` with `x - ((temp_hi >> 3) * 0xA)`: a call to a function that does not exist.
The function was on `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`; the target was built with the SN profile above
(`-mfp32` is also exact: no floats).

The source change, and the measured result before and after (scores, the app's judge):

decomprepair `magic_high_divide` on the unchanged draft writes `x - (((u32) x / 10) * 0xA)`. That one child scores:

| source | KMC -O1 or -O2 | SN profile |
|---|---|---|
| draft | 2500 (-O1) | 2695 |
| `magic_high_divide` child | 170 | 0 |

A plain rewrite with `% 10` is also 0 under the SN profile; the app's `compiler_experiments` trial saved it.

Failed experiments that would otherwise be repeated:

Under KMC -O2, eight loop and digit spellings stayed at 170 or worse (the `i++` one slot late, the high word in
`$t2`): the difference is the compiler, not the C.

What is controlled evidence and what is one function:

One function. No new repair was needed: `prove_chain.py --families magic_high_divide` reaches 0 from the draft in
one step under the SN profile. A profile probe of the unrepaired draft cannot find it (2695), so probe profiles on
the repaired child.
