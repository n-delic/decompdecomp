---
title: a 664-instruction sprite drawer from the SDK's macros
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3
evidence: observation
date: 2026-11-23
---

A display-list sprite drawer (texture load, a grid of scissored texture rectangles) saved at 13278 as an m2c draft full of raw
stores and search scaffolding. A rewrite that spells the commands as the SDK's macros the neighbouring matched drawers use
(`gDPLoadTextureBlock_4b`, a scissored `gSPTextureRectangle` with the `MAX`/`MIN` s and t correction) went 13278 -> 9682 on
the first try and 6035 with signed rows and columns read from the packed word; the exact judge, isolated.

Steps in order, with the isolated score they reached: literal `if (hires) {a = K; b = K2} else {...}` pairs (the constants stay in
registers), `u8` fields read as ints (signed `div`), the packed row/column nibbles read through `u32` then `(s32)` (signed
`slt`), the second operand of the texture-rectangle word made conditional (6035 -> 3175, see the finding on fold), a `u16`
shadow of the cell's y (3175 -> 2585) and a copy of x at the head of the high-resolution arm (-> 2175), x typed `u16` (-> 1885).

Further steps measured later: a constant scale factor set in both arms of the picking `if` (9682 -> 7545, decomp-repair
`arm_constant_pair`); a wide position retyped `u16` (2700 -> 2010, `wide_local_to_halfword`).

Open at 1810 when written: the loop counters (u16 for the first tests, but the multiply reads them unextended), a copy
(`move $t3, $t2`) the target keeps between the position and the arm copy, the register order of the hoisted invariants
(arg1 should outrank the loop's `s`: 18 weighted references against 15 moves it above, the correction operand written
`(y16 + arg1) << 2` gave 27).
