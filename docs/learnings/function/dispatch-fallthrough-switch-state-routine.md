---
title: dispatch fallthrough switch state routine
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 94-instruction N64 state routine and its
101-instruction neighbour, both working on one 0x64-byte record array indexed by a `u16` argument, both `-O1` beside the
game's `-O2` default. The signs of `-O1` in the target: the record index recomputed in every block, and a just-stored
field read back. m2c's draft of the first wrote one case of a three-case `switch` behind its comparison tree, with the
other arms leaving by `return` or by `goto` into it.

The source change, and the measured result before and after (scores, the judge used): plain C (the record array, a
`switch`, a `u16` parameter) was exact in the app. The 101-instruction one: from its draft under `-O1`,
`indexed_symbol_records`, `branch_orientation`, `flag_temp_inline` reach 0 in the standalone judge (its saved best, with
a `volatile` parameter and byte offsets, stopped at 2420). The 94-instruction one: from its draft the library stopped at
2715; with `indexed_symbol_records`, the new `dispatch_fallthrough_switch` (2675), `local_weight_ladder` (1265),
`conditional_store_arms` (250) and `assignment_at_use` it reaches 0.

Failed experiments that would otherwise be repeated: halfword arrays indexed by `i * 0x32` instead of a record or a
two-dimensional array (6680), and keeping the saved best's `volatile` parameter with the record array (7455).

What is controlled evidence and what is one function: the chains are standalone-judge runs on these two functions; the
profile was applied from an exact `compiler_experiments` trial.
