---
title: byte tables and a tree of guarded leaves, exact from the machine draft
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

A 99-instruction N64 setup routine, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (the project's default is `-O2`,
where the same exact C scores 11810; its matched neighbours are `-O1`).

Plain C with `u8` arrays, a `for` loop and a `switch` with ascending cases was exact at once. From the machine draft
(4890 with m2c's macros) two decomp-repair families reach 0, measured with `prove_chain.py` against the target:
`unknown_byte_array` (3275: `extern M2C_UNK D;` as `u8 D[]`, and `v != G` inside `if (G == 1)` as `v != 1`) and
`guarded_leaf_switch` (0: the compare tree whose leaves return, as a `switch`).

Failed: the switch with its cases in the tree's order (2, 3, 1) scores 580; earlier attempts with pointer locals
stayed above 1900. `m2cfix.refine_draft` split an indexed table into one variable per offset (fixed with the same
library change).

One function.
