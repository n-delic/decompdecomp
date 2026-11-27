---
title: -O1 drafts of record tables reach 0 as record arrays (entry-value while, stored-address write)
kind: function
compiler: gcc-2.7.2-kmc
evidence: matched
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Two functions of an N64 game, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, in a project whose default for them was
`-O2`. One scans 7 records of 0x8F0 bytes against a zero-terminated list of 16-byte points (two nested loops, `sqrtf`);
one fills the object a call returned, through a 100-byte slot array whose first member is a pointer.

The source change, and the measured result before and after (scores, the judge used):

Plain C with the records declared as arrays and `u16` counters was exact at `-O1` (3433 and 2603 under `-O2`).
From the machine draft, `prove_chain` with `-O1` reaches 0: for the first, `indexed_symbol_records`,
`u16_counter_loop`, `entry_value_while`, `m2c_refinement`, `inner_loop_invariant_inline`, 5156 -> 0; for the second,
`indexed_symbol_records` (7690 -> 550), `m2c_refinement`, `stored_address_write` -> 0.

The loop tell: two registers hold 0.0, one hoisted out of the outer loop for the entry test and one set in the inner
preheader for the bottom test. A guard over a `do` shares one; a `while` copies its test (hypothesis for why).

Failed experiments that would otherwise be repeated:

`-O2` with `-fno-strength-reduce` got the first to 2387, never closer. The existing `spill_slot_copy_collapse` merged
the inner loop's cursor into its base, which dropped the cursor's reset each outer iteration (fixed in decomp-repair).

What is controlled evidence and what is one function:

The two chains are measured with the app's judge. Both families come from one function each.
The profile probe now judges the draft's `indexed_symbol_records` form under `-O1` too.
