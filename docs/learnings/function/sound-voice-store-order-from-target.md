---
title: sound voice store order from target
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64)
evidence: observation
date: 2026-11-27
---

A 65-instruction N64 sound routine that fills a voice record through a global pointer, under SN GCC 2.8.1
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). The saved best (an m2c draft) scored 2985.
A matched neighbour held a `static inline` voice starter with the same body; adapting its types (a `volatile` pointer
to the record) was exact on the first variant. Looking there first is the cheap route.

From the saved best the library also reaches 0, which is what the permuter lacked one step of (exact judge, one chain):
`symbol_base_unwrap` 2575, `branch_value_run_sink` 1420, `volatile_reload` 785, `target_store_sequence` 220,
`branch_value_run_sink` 110, `deref_compound_store` 0. `target_store_sequence` is new: it writes a run of stores
through one pointer in the order of the target's `sb`/`sh`/`sw` offsets, all moves in one child.

Failed experiments: without it a greedy chain over `store_run_order`, `deref_compound_store` and
`branch_value_run_sink` stalled at 190, having hoisted the offset 0 store above two others because that one move scored
best (300). `volatile` on the stored halfword casts is not needed once the pointer itself is `volatile`.

What is controlled evidence and what is one function: the scores are the exact judge's on this one function.
