---
title: address difference split segment loader
kind: finding
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

A 363-instruction N64 segment loader, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (a per-function profile; the project
default was `-O2`): sixteen straight-line loads, each `size = end - start`, a message call and a DMA call, each segment
ending where the next starts. The target loads the end address into `$s1`, the start into `$s0`, subtracts into `$s1`,
and loads the next segment's start again although it is the previous end.

The source change: `size = (s32) end; size -= (s32) start;` in place of `size = end - start;`. The address of `end` then
lives only in `size` and is overwritten, so cse has nothing to reuse. 20321 -> 0 (all sixteen statements together, the
rest of the function already right), the app's judge. decomp-repair `compound_operand_split` offers it.

Failed experiments: `-O2` with either spelling (24883 for the first draft); the one-expression form keeps the end address
in a saved register and carries it into the next load (21051).

Also on that function: a byte global tested in a loop is read straight in the test (the compiler hoists the load), and
`len = (s32) end; from = start; len -= (s32) from;` builds a one-off `(start, end - start, end)` call's addresses.

What is controlled evidence and what is one function: one function. The cse explanation is a hypothesis.
