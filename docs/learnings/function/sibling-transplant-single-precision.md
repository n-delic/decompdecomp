---
title: sibling groups matched by adapting a matched copy, under its -O1 profile
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: verified
date: 2026-11-27
---

Groups of two to four copies of one routine (direction rotations of a grid neighbour lookup and of a menu cursor,
weapon spawners, per-object animation state machines, 150-300 instructions) whose open copies sat under the project's
`-O2` default while the matched copy had `-G0 -mips3 -mgp32 -mfp32 -O1`. Taking the matched copy's C, changing what the
target's diff against the copy's assembly shows (symbols, immediates, which direction's condition each block tests,
the order of one statement) and judging under the copy's profile matched most of them on the first compile. Judged
under their own `-O2` profile the same exact C scored 2500-10000, so a transplant must be judged under the donor's
profile before its score means anything.

Compare each open copy's assembly with the matched copy's (labels normalised, a unified diff) rather than reading it
alone: the diff is the list of edits.

One difference a family now writes: a copy whose matched sibling used double arithmetic (`x * 3.14 / 180.0`, `+ 4.0`,
`i * 45`) did the same in float here (`lui; ori; mtc1`, `mul.s`). decomp-repair `target_float_literals` (0.6.212)
writes the literals as the float words the target builds: 3185 under the sibling's spelling, 1035 with the float
literals only, 0 with the integer factor as `45.0f` too (two functions; the same state also needed a guard around one
call, written by hand).

One that did not close: a copy that differs from its sibling by an early return stayed at 35, one saved-register
exchange between a float local held in a general register and the hoisted constant 1. Statement, declaration and
`do {} while (0)` placement searches and the whole repair library left it there.
