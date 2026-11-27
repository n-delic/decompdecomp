---
title: A configuration block split into stack locals needs its edits together
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: controlled
date: 2026-11-27
---

An N64 audio start-up function (52 instructions) fills a configuration block on its stack, passes its address to the
driver's init function and calls three more. m2c wrote the block as `s32 sp10[2]; s32 *sp18; s32 sp1C[13];`, kept the
scheduler's store order, wrapped part in `do { } while (0);` and copied a global from a temporary before setting it.
Compiled with the function's own SN GCC 2.7.2 flags (`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`).

Each edit alone, judged from the saved best (655): one array 393, the early read moved 899, array plus move 633, plus
fence removed 308 (fence removal with the array alone 273). One array with the stores ascending by slot, the
computation kept at its slot, and two non-zero constants in the target's order: 0. A plain typed struct written from
the assembly was 22, and 0 after swapping those two assignments.

Library: `frame_block_init_order` (decomp-repair) offers the whole set as one child (655 -> 0, first child). Before it,
a greedy chain over every family reached 232 after 110 judged variants, though `stack_locals_one_array`,
`read_before_assign`, `one_shot_unwrap`, `assignment_at_use` and `store_run_move` each existed.

Controlled: the scores above are one function. That constants sharing `$v0` come out in source order while `$zero`
stores sink later is an observation from this function's diffs, not read from the compiler source (hypothesis).
