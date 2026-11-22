---
title: counter init sink
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---
A KMC gcc 2.7.2 menu-setup function (442 instructions): a result temporary and the loop counter that follow it
sat in saved registers `$s1`/`$s2` the wrong way round; every other row was right.

Rule (local-alloc.c/global.c order: priority = floor_log2(refs) * refs / live length, a reference inside a loop
counting double): the counter, set to 0 at the m2c position before five calls the loop never meets, had 17 refs over 90
live insns (0.756); the loop's `lookupDataEntry` result had 8 over 23 (1.043), so it was allocated first and took the
earlier register. The target's counter has the higher priority. Sinking `i = 0;` below those calls to just before the
`if` that guards the loop shortens its live range, so it outranks the result (120 -> 0, `fast_judge`, confirmed by
`compile_and_diff`). The `li` stays in the first call's delay slot, so the emitted order does not change.

Not hypothesis: priorities were read from `passes.py` (greg table) and the cc1 `-dl -dg` dumps.

Second, separate cause on the same function (155 -> 120): a dummy `if (gCurrentSaveSlot) { a = x; } else { a = x; }`
(identical arms, kept by an earlier agent so jump threading leaves three copies of `addiu $a0`) read the slot after a
table load, keeping the slot pseudo live across it, so the loaded value could not take `$v1` and got `$a1`. The same
dummy condition on a value already live there (`temp_v1_3`) leaves the slot pseudo dead earlier and matched `$v1`. This
is scaffolding, not game code; it is reported with the match.

Library: decomp-repair 0.5.625 `loop_counter_init_sink` (in `loop_weight_repairs`, so wired). Before it, `prove_chain
--families all` scored 342 children from the 120 state and none improved.

Controlled evidence: the rule (priority arithmetic) and the 120 -> 0 step on one function. Not tried on other functions.
