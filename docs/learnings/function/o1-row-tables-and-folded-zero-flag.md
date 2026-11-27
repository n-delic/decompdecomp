---
title: Two -O1 functions: row tables, a folded zero flag and a switch's shared tail
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

Two N64 functions of a game whose default is `-O2`, both exact as plain C under `-O1` (per-function profile).

A 119-instruction list builder: saved best 3400 under `-O2`. The same plain C scored 8070 under `-O2`, 1695 with
`-fno-strength-reduce` and 0 under `-O1`. The tables are `u16 rows[][150]` and `u32 masks[][8]`; m2c's draft typed
them `M2C_UNK` and added byte offsets. Draft under `-O1`: 7260; with `symbol_row_arrays` (decomp-repair) 1430, and a
repair chain from there 400.

A 142-instruction state routine: saved best 3605. Plain C was 6033 under `-O2` and 600 under `-O1`; the last 600 was a
callee prototype that sign-extended `0xFF` arguments. m2c wrote the button test as `(0 & 0x10)`: the target holds the
zero in a saved register across the calls, so the source had a local. From the draft with its record table typed
(6437) the library reaches 0 with `dispatch_fallthrough_switch`, `folded_zero_flag` (4292 -> 1240) and
`switch_tail_arm_order` (1240 -> 0).

The shared tail: `D++;` after the switch has to be in each breaking arm, and the arm the target lays out last has to
come last. Alone the copied tail scored 1240 and the arm order 920 (both on one chain state); together 0.

What is controlled evidence and what is one function: each number is one function's; the explanations are hypotheses.
