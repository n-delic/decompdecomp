---
title: A HUD bar width advanced in place after the remainder is computed
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-28
---

An N64 HUD drawer (166 instructions): a copied three-byte colour and four-word table, a `u16` loop of text calls with a
float scale, three display-list set-up commands, then a loop drawing four bars (colour, filled rectangle, and the unfilled
rest when there is one). Compiler `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

The source change and the scores (the app's judge):

| Source | Score |
| --- | --- |
| Typed rewrite of the m2c draft: struct copies for the two local arrays, `for` loops over `u16`, command macros, `u16` bar width | 435 |
| The bar width as `u32` (no `andi` of it) | 335 |
| `rest = 50 - filled;` moved after the first rectangle (the constants are then loaded in the target's order) | 110 |
| `filled += 60;` after it, later reads `filled` instead of `filled + 60` (`addiu $a1, $a1, 0x3c`) | 0 |

The library's `bias_advance_in_place` offers the last step; `bias_advance_past_use` offers the last two as one child
from the 335 source. No declared repair offered either before it.

Failed experiments: the library chain from the 110 source judged 80 of 287 children and none improved.

What is controlled evidence and what is one function: one function. Why the constants' order follows the statements'
order was not confirmed in the compiler source (the SN build has none), so it is a hypothesis.
