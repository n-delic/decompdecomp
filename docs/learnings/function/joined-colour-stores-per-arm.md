---
title: Colour bytes joined after an if/else, stored at the end of each arm instead
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An 80-instruction N64 object update: a frame counter bumped, a position word advanced, a spark spawned on one frame,
then three colour bytes set from a two-arm `if` (a constant after a call in one arm, `0x2D - (u8) timer * 2` in the
other). Flags `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, scored by the app's judge. The old best
was an m2c draft at 1065.

The source change, and the measured result before and after (scores, the judge used):

With everything else exact, `v = E;` in each arm and `a = v; b = v; c = v;` after the join scores 100: the target
keeps the value in `$v0` and the candidate in `$v1`, with the counter's reload between the stores. Writing the three
stores at the end of both arms (the literal arm storing the literal, the other keeping `v`) is 0. Declaring `v` as
`s32` or `u16` and reordering the shared stores did not move the 100 rows. decomp-repair `carried_store_arms` moved
only a single store before; it now moves the whole run.

The other edits from the old best, all needed: the counter tested through the member and not an `unsigned char` copy
(`stored_compare_readback`, `narrow_widening`), one halfword copy moved up beside its neighbours
(`late_load_store_hoist`), byte constants above 0x7F stored through unsigned lvalues (`constant_store_unsigned`), the
halfword sum's operands reordered (`narrow_widening`), and the search's `do { } while (0)` and `if (0) goto` fences
removed.

Failed experiments that would otherwise be repeated:

Each of those edits alone is neutral or worse from the old best (removing the fences alone: 1065 to 1600), and a
greedy chain of the families above stops at 635 after its first step. `gcc-2.7.2-kmc` was the closer profile for the
old draft (740) but not the function's.

What is controlled evidence and what is one function:

Controlled on this function: undoing only the per-arm stores gives 100, and the library's child from that state gives
0. The reason (the joined value lives across the join and is given another register) is a hypothesis. One function.
