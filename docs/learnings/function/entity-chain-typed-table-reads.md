---
title: entity chain draft to exact through typed table reads and a swapped comparison
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 108-instruction N64 routine that walks to the end of a linked chain of entities, then releases the last one or
appends a new one and picks a kind through four small getters. Its machine draft scored 2780 (the app's judge).

The draft declared two tables `char` and read them as `*(s32 *)((k * 2) + TABLE)` and `(u16) *((k * 2) + TABLE)`; the
target loads both with `lhu`. It also had m2c's join ladder (a `goto` into the last arm over a never-set
`saved_reg_s3`), a switch value held in a temporary the target reads again, `TABLE[k] < self->count` where the target
loads the count first, and a field store the target's load of the count precedes.

Chain, each step a library family: `m2c_goto_join_ladder` 1960, `late_load_store_hoist` 1065, `field_copy_reread` 620,
`asm_address_read_width` 280, `asm_byte_table_deref_width` 80, `comparison_sides` 60, `store_adjacent_gather` 0.

Two steps were not offered before this was found: `comparison_sides` ended `*(s32 *)(sum)` at its cast, and
`asm_byte_table_deref_width` skipped a dereference behind a scalar cast (`(u16) *(sum)` still loads a byte). Both are
fixed in decomp-repair.

A `switch` on the new entity's kind (a compare tree) is wrong here: 2310 against 80 for the plain `if`/`else if` chain
in an otherwise identical rewrite.

Controlled: the chain's scores above, on this one function. The first four steps were a greedy `prove_chain.py` run,
the last three were applied one at a time to its output.
