---
title: A spawned record's field copies written together before the constants stored beside them
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 104-instruction N64 object spawner (one
record from an allocation call, then eight more in a `u16` loop), each filled with the parent's position (two `u16`
copies) and constants. The saved best was m2c's draft at 250 after 22 attempts and 2386 automatic repair trials: only
register and order rows. The target loads the second coordinate before the `li` of the type word and stores it in the
next call's delay slot; the draft wrote that copy after the constant stores.

The source change, and the measured result before and after (scores, the judge used): isolated judge (the app's own),
then `compile_and_diff`. In both runs the second copy goes directly after the first; in the run before the loop the two
constants also swap (`type = 0x3F7;` before `state = 2;`).

- loop run alone: 250 -> 190
- first run alone: 250 -> 60
- both: 250 -> 0 (also a typed struct rewrite of the whole function, first try)

decomp-repair `field_copy_group` offers every run grouped in one child, with each literal order; from the saved best its
third child is exact (its first three scored 60 / 285 / 0).

Failed experiments that would otherwise be repeated: `store_adjacent_gather` moves the loop run's copy but not the first
run's (that record pointer is a second local, so it is not known to be new). A greedy chain of `statement_slide`,
`store_adjacent_gather`, `store_run_order`, `store_order_climb` and `offset_store_order` stalled at 60 after 90 judged
variants; `statement_group_orders` does not move a load past a store.

What is controlled evidence and what is one function: the scores are measured; the rule is from one function. That the
scheduler delays the copy's store into the delay slot is read from the target, not from the compiler source (hypothesis).
