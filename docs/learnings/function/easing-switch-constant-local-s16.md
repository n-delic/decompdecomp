---
title: A switch's constant local declared s16 gives a KMC -O1 function its copy register and stack frame
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 76-instruction N64 routine eases a halfword toward a value that a switch picks from a byte flag, with a float
divide by eight. The target had an 8-byte stack frame with no load or store through it, and a copy of the switch
value in a second register. It is -O1 code under a function profile (applied through a compiler trial).

The source change, measured with the app's judge: from a plain rewrite (605), declaring the switch's local `s16`
instead of `s32` gave 80 (the frame appeared with it); `p[4] > v - 2` for `v - 2 < p[4]` gave 20; and
`p[4] = (s32) (...) + p[4];` for `p[4] += (s32) (...);` gave 0. decomp-repair `constant_local_narrow`,
`computed_compare_flip` and `compound_value_first` reach 0 with `prove_chain` from the plain rewrite.

Failed experiments: a plain `s32` copy of the value (coalesced, no change), inline helpers (605 to 1470).

What is controlled evidence and what is one function: one function.
