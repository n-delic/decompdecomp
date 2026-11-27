---
title: -O1 record builder: casted offsets and a goto-default compare tree
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 functions of 158 and 257 instructions in a game whose default is `-O2`, both exact under `-O1` only. Neither
saves a register, so the prologue does not say `-O1`. What does: no strength reduction (the index is multiplied out
at every access, with the first shift in the loop branch's delay slot) with loop constants still held in registers,
and a store followed by a load of the neighbouring byte through the same address register (`sb` then `lbu -1`).

The source change, and the measured result before and after (scores, the judge used):

Plain C, app judge. The 158-instruction one: 7150 at `-O2` for the saved best, 0 as plain loops, a `switch` and a
two-element record array under `-O1`. From the same source with m2c's nested-`if` tree instead of the `switch`: 250,
and 0 with `narrow_return_compare`.

The 257-instruction record builder: saved best 4220, 0 as plain C. From its machine draft with the externs typed:

- casted offsets (`*(u16 *)(BYTES + 0x2108 + t)`) kept: 29545 for otherwise exact C. The record array is essential;
- casted table reads (`*(u16 *)((char *)D + i * 2)`) or the order of the `switch` arms: no difference (0);
- `cast_offset_records` with `compare_tree_default_switch` (one child): 5735, then `switch_layout`,
  `narrow_widening`, `default_neighbour_case`, `unused_frame_local`, `cfg_integer_lifetimes` to 0;
- without the `switch` the same chain stopped at 2730;
- with the last field collected in a variable and stored once after the `switch` it stopped at 1705: the store has to
  be written in each arm (GCC merges them itself).

Failed experiments that would otherwise be repeated:

One 2D array `u16 D[8][2]` for eight parallel tables (8770): the target loads each table's own symbol, so they are
eight arrays.

What is controlled evidence and what is one function:

The scores above are controlled (one change at a time, app judge). The `-O1` tells are one game.
