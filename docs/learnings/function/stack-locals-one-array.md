---
title: Separate stack locals passed as one block are one word array
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

A 35-instruction N64 function that builds a 16-word argument block on its stack and passes its address to one call
(KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`). m2c split the block into `s32 sp10[2]; M2C_UNK *sp18; s32 sp1C[14];`
and an earlier source wrapped the call in a four-armed `if` tree of identical calls (`__repair_pointer_balance`
scaffolding). The saved best stood at 110.

The source change: one `s32 buf[16];` for the whole block (`buf[2] = (s32) &SYMBOL;` for the pointer local, the element
the C never named dropped so the block is the game's 16 words), the if tree removed, one call `g(buf)`. Judge: the app's
exact judge (prove_chain), 110 -> 15 (the one array alone) -> 0 (the existing `scaffold_strip` dropping the if tree).
The stores were already in the target's order once the objects were one (0..5, 9, 6, 7, 8, 11, 10, 12..15), so no
store-order search was needed; plain offset order scored 104.

Why (hypothesis, not read from the compiler source): separate locals are separate frame objects. The dead pointer
local's store (the word the callee reads) was removed, and the hole left by the unnamed element shifted every later
store one slot; as one array the frame, the callee's block and the store order are the target's.

Not needed or failed: a store-order grid (the order was right); `identical_arm_collapse` alone (the marker comment keeps
it from firing; `scaffold_strip` handles that spelling).

Controlled evidence: decomp-repair `stack_locals_one_array` (v0.6.97) reproduces 110 -> 15 from the saved best, and with
`scaffold_strip` the exact 0, by prove_chain. One function. The compact layout (dropping unindexed elements) is one
child beside the by-offset layout because m2c's names do not say how the game laid the object out.
