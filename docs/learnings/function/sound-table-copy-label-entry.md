---
title: Table-copy function: initialiser set at the goto-entered label, byte index widened first (30 -> 70 -> 0)
kind: function
compiler: gcc-2.7.2-kmc, -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---

A byte table copy (83 instructions, three arms: a 0x00-0x3F pair of copies, a 0x80/0x81/0x84/0x85 pair, and a single
byte), each arm computing `table + bank * 0x86 + index`. Compiler `gcc-2.7.2-kmc`, `-O2 -G0 -mips3 -mgp32 -mfp32`.
The saved best (30) declared `u8 *base = &table;` with its initialiser *before* the `pair_entry:` label that a `goto`
from outside the block entered, so the goto path skipped the address load (not a faithful program).

Edit list, scored with the app's judge (one function, so observation, not controlled):
1. `base` initialiser set after the label (`pair_entry: base = &table;`): 30 -> 70. Registers now right; the `bnez`'s
   delay slot holds `addiu` (from the fall-through) instead of the target's `andi`.
2. A widening copy at the label before the address load (`pair_entry: idx = var_v0; base = &table; dst = idx * 0x86;`):
   70 -> 0. Only pays off after step 1; alone it scores worse.

Why (reorg.c `fill_eager_delay_slots` / `mostly_true_jump`, read; the effect is measured here, the generality is a
hypothesis): a `bnez` is predicted taken, so its slot is filled from the branch target first; a target reached by two
paths is not owned, so only its first instruction is a candidate. The address load is a two-instruction pair that
cannot be a slot, so with the load first nothing is taken from the target and the fall-through's `addiu` fills the slot.
With the zero-extension first at the label, the slot gets the `andi`, the label moves past it and the pair follows.

Failed: statement permutations of the arm body (24 orders x 3 spellings of the product x 4 sum shapes, goto and `||` forms, label on
the first statement or an empty one; best 70, scaffolding-free spellings of the same step); the natural 2-D array form
(`D[bank][i]`, 1230); the library's existing families on the 70 (no child beat it).

Library family: `label_entry_initializer` (decomp-repair), both steps as one child; from the saved best it emits 70 and 0.
