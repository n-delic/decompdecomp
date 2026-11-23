---
title: A counter stepped in each arm of a forked loop body is one `for` step; the copies change the back jump's slot and the saved-register order
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

A 252-instruction N64 menu builder (`gcc-2.7.2-kmc`, `-O2 -G0 -mips3 -mgp32 -mfp32`): `for (;;) { if (!(i < n)) break; ... }`
whose body forks. m2c wrote the counter step in each arm (`i += 1; release(p);` in one, a `goto block_13;` to a shared
`block_13: i += 1;` closing the other). Saved best 360: the counter and the entry pointer had their saved registers
swapped (`$s2`/`$s3`) and the back jump after the last call had the step in its delay slot (`j L; addiu $s3,$s3,1`) where
the target steps before it and puts the loop head's `move $a0,$zero` there (`addiu; j L+4; move $a0,$zero`).

Fix, one edit: write the step once as the loop's own, `for (;; i += 1)`, and drop it from the arms (and the label/goto
pair). 360 -> 0 in the standalone judge and in the app, with every other statement as it was (the saved best still carries
m2c's `__repair_*` temporaries and a phase union: the match relies on that scaffolding, which polishing removes).

Rules (read in `reorg.c`, `fill_simple_delay_slots`): the backward scan for a delay-slot insn starts at the jump and
stops at a label (`stop_search_p(..., 1)`), and a jump whose scan finds nothing is filled from the loop head
(`fill_slots_from_thread`, retargeting the jump past the copied insn). A `for` step is emitted once, at the continue
label, so the body ends in one join and one back jump. Copies of the step in the arms end in as many jumps and change
the reference counts `global.c` orders the saved registers by (hypothesis for the `$s2`/`$s3` swap: the counter lost
priority against the entry pointer once the step references were counted once, not per arm; only the outcome was measured,
not the priority table).

What did not work first: a weight ladder (double `do { } while (0)` around the three entry-pointer references) fixed the
register swap alone (360 -> 300, `local_weight_ladder` has no nested form and found nothing in 571 children), and
duplicating the step into each arm (`goto_shared_tail`) kept 300. The existing families found nothing on the saved best
(`prove_chain.py --families all`: best 340, `live_range_retarget`).

Library: decomp-repair `loop_step_fold` (RetroReversing/decomp-repair#1287) offers the fold as a child of any
`for (;;)`/`while (1)` whose every path ends in the same step. Controlled evidence: its tests. One function: the match.
