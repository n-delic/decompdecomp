---
title: twine voice table init
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like: a 68-instruction table initialiser (three small loops: a 4-byte-stride record loop, a
halfword clear loop with a `n * 4` bound, a 256-byte fill). m2c named one `i` for the first counter and, after the loop,
for the loaded count. The saved best (20) differed in four register rows: the second loop's counter in $a1, the target
has $a0.

Cause, read from `-dl -dg` (hypotheses, not proved in the compiler source): the shared pseudo carried a preference for
$a0 (the first loop's `i << 2` temp), so the `n << 2` bound preferred $a0 and global.c's pass 0 gave the higher
priority counter $a1.

Edits, each scored with the exact judge: split the first loop's counter into its own local 20 -> 50; wrap the loaded
value's statements (load, store, `-1`, `&= 0xFF`) in two nested `do { } while (0);` (flow.c weights references by loop
depth) 50 -> 200 (the registers are right, a branch-likely `bnel` replaces `bnez` in the first loop); sink the counter's
`= 0` into the following `if (n != 0) {` guard 200 -> 0. Library `init_sink_into_guard` already did the last step;
`loop_counter_value_split` (decomp-repair, PR 1212) emits the three as one child (children 50, 200, 0). The match relies
on the two `do { } while (0);` wrappers (a weight, not game code) and the split counter.

Failed experiments: loop forms (for, struct-indexed, `lim` local, u8/s32 casts), statement order grids, every
partition of the roles over one to five variables without the wrapper (best 20), wrapper depth 1 (the value stays
below the counter), wrapper placement/order grids with the init left outside the guard (200 at best).

What is controlled evidence and what is one function: one function only.
