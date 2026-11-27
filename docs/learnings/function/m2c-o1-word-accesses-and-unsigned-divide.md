---
title: -O1 machine drafts, word accesses of symbols and unsigned divides
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

Two KMC GCC 2.7.2 functions built with `-G0 -mips3 -mgp32 -mfp32 -O1`: a 250-instruction object update and a
204-instruction effect spawner. Both prologues save every callee register before the parameter is copied or used, the
sign of -O1. Under the project's -O2 profile the saved bests sat at 2820 and 2796; plain C with the records as one
0x64-byte struct array was exact under -O1 on the first or second compile.

The source change, and the measured result before and after (app's judge, from the m2c draft):

- Object update: m2c read and wrote untyped symbols as words (`trunc.w.s`/`cvt.s.w`, `sw` for `sb`).
  `asm_symbol_access_type` takes `lwc1`/`swc1`/`sb` off the target's `%lo` accesses. Chain from the draft: 7005 ->
  `embedded_if_tree_switch` 3515 -> `word_symbol_byte_offset` 1965 -> `asm_symbol_access_type` 345 ->
  `store_temp_inline` 0. Without the new family the chain stopped at 925.
- Effect spawner: the draft divided signed (`mult`, `sra`) where the target has `multu`/`srl`
  (`unsigned_constant_divide`), and kept m2c's unsigned-to-double expansion (`unsigned_double_bias_fold`). Chain with
  the first: 12141 -> 1145 (1685 before). On a further repaired draft (1386): `single_use_inline` 930 ->
  `record_slot_table` 285; the bias fold made by hand then gave 15 (three register rows).

Failed experiments that would otherwise be repeated:

The spawner's draft was not taken to 0 by the library; the follow-up chain from the 15-point child was stopped before
it finished.

What is controlled evidence and what is one function:

The family unit tests are controlled. The scores are one run each on these two functions.
