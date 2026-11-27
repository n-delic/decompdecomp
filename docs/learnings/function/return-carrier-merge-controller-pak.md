---
title: A returned carrier and two loop counters written as one variable each (controller pak routine)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 116-instruction N64 routine that clears two flag arrays, then either initialises the pak on every plugged-in port or
on the first port and returns a result code. Saved m2c best: 1075.

The source change, measured with the app's draft pass and judge, one step after another:

1. `goto_forward_else` (the two arms' shared `*flag = 1` tail written in each arm): 1075 -> 425.
2. `register_name_merge` / `loop_counter_unify` (the two loops' counters as one variable): 425 -> 140.
3. `return_carrier_merge` (the tested call result, the codes the arms replace it with and the returned local as one
   variable; new in decomp-repair): 140 -> 0, and the app records the hand-written equivalent as matched.

Without step 2, step 3 takes 425 to 285. A greedy chain before the new family took `copy_register_reuse` at step 2
(425 -> 370), whose child returned a local that one path never assigned; the family now stops where the copy no longer
holds.

Failed experiments that would otherwise be repeated: the shared flag tail written through a pointer local that the
clearing loop also uses (730 against 15 for a plain array store); using the one-port path's result variable for the
loop path's call result too (15, the loop's value is in another register).

What is controlled evidence and what is one function: one function. The library tests control the merge's safety
conditions, not the register allocation.
