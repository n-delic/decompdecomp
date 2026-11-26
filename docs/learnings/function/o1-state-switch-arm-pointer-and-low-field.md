---
title: o1 state switch arm pointer and low field
kind: function
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

A 145-instruction N64 state switch (float easing in two arms, a counter step in the third), gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O1`. The signs of -O1: the `u16` parameter is copied and masked again at each use, no
instruction is scheduled into the float hazard `nop`s, and an 8-byte frame with nothing stored in it.

The source change, and the measured result: typed record arrays (0x64 and 0x8F0 bytes), compound float updates
(`x += (k - x) * 0.4f`), the arm's member pointer read above the `switch` (`lw $t0` before the case value), the
counter written `n = f & 7; f = (f & 0xF8) | (n - 1);`, and an unused `s32 pad[2]` (the frame). Exact in the app.
From the machine draft in cast form, `prove_chain` gives 1310 -> 940 (`reload_temp_inline`) -> 240
(`signed_switch`) -> 35 (`case_pointer_prefetch`) -> 0 (`low_field_step`); the last two families are new.

Failed experiments: `-O2` with any spelling (best 1790 after a search); `(f & 0xF8) | ((f & 7) - 1)` with the word
in one local (35), operands swapped (215 with the frame).

One function; the -O1 reading matches several other functions of the same program.
