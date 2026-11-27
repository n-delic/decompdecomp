---
title: Respawn limit tests, a named offset sum and a staged count load (1385 to 0)
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An N64 enemy-respawn routine (118 instructions), SN64 GCC 2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`, from an m2c best at 1385. Judged by the app's judge.

The source changes, in order:

- The `&&` chain of limit tests written `limit <= field`, all four at once, where m2c wrote `field >= limit`
  (1385 -> 1135 alone). `comparison_sides` now moves a scalar cast with its operand and swaps every comparison at once.
- `(o = angle + 0x200) - (random() >> 5)`: `named_sum_difference` (-> 925).
- `t = f(); if (t == 0) p->x = K; else p->x = t;`: `conditional_store_tested_arms` (-> 865).
- The count field loaded into a local above a run of stores: `store_run_load_stage` (865 -> 380 with both sites). The
  second site alone was 215; the family already offers it as its own child.
- In the first block, the byte store moved below the count store: `statement_slide` (215 -> 0).

The match keeps two named intermediates (`o`, `temp_load`) in the source.

The library's chain with the comparison change reached 0 from the saved best: store_run_load_stage (735),
comparison_sides (485), named_sum_difference (275), conditional_store_tested_arms (215), statement_slide (0).

A greedy chain that took the first improving batch did not reach `store_run_load_stage` in time on near-misses like
this: `prove_chain` now judges a few children of every family first.

Failed experiments: writing the second child's `+ 1` as a read-back of the field it stores `1` to, a local set to 1
before the store, or the assignment inside the addition, all compiled the same (865 with the surrounding edits).

One function: no controlled test of the compiler rules; the library test covers the comparison rewrite only.
