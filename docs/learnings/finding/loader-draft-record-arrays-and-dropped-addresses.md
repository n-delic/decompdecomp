---
title: A loader's draft reaches 0 through record arrays, dropped call addresses and for loops
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 568-instruction N64 asset loader (copy two
163-entry tables to the stack, DMA each entry above the heap, about a hundred registration calls, two walks over the
slots), SN64 GCC 2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. A matched neighbour built the same
way gave the source: struct assignments for the table copies, an array of records, plain `for` loops.

The source change, and the measured result before and after (scores, the judge used): the app's judge. The m2c draft
scored 56774; a typed rewrite from the assembly was 615, and 0 once the record indexes were direct and the global store
stood just before the calls. From the draft, decomp-repair families alone reach 0:
`block_move_struct_assign` (51246), `dropped_address_argument` (1925: the draft's calls had lost the address each passes,
because the callee's matched definition takes one argument), `pointer_walk_to_index` (1775), `indexed_base_records`,
`unguarded_do_for`, `shift_back_scale`, `loop_counter_unify`, `split_accumulator` (40, eight register rows), and
`dead_scratch_reuse` (one size variable for both DMAs: 0).

Failed experiments that would otherwise be repeated: inlining every pointer local as `&symbol` without the record type
(25186: separate induction pointers per member); `((Row *) &stack)[i]` for two stack tables instead of declaring them
as rows (the two induction pointers are combined and the frame shrinks by 8).

What is controlled evidence and what is one function: one function. That the `for` loop's loop-continue note is what
turns `beqz` into `beql` with the step in its delay slot is a hypothesis (reorg.c), not read for this compiler.

Families: `block_move_struct_assign`, `dropped_address_argument`, `pointer_walk_to_index`, `indexed_base_records`,
`unguarded_do_for`, `loop_counter_unify`, `split_accumulator`, `shift_back_scale`, `dead_scratch_reuse`.
