---
title: A plain global read is the nearer base global's field (G as *(T *)((s8 *)H + k))
kind: function
compiler: SN GCC 2.7.2 -O1
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler: a 100-instruction N64 display-list routine (SN GCC 2.7.2 `-O1`, N64 `sn-n64-272-0001`). It appends a dozen display-list commands through one list pointer global, then passes a state word, held by a second extern global 0x1C bytes after the first (`D_x + 0x1C` by the symbol names), to `osVirtualToPhysical`. The saved C read that word as its own symbol (`lui; lw %lo(G)`); the target loads `lw $a0, 0x1c($s0)` from the register that already holds the first global's address.

The source change and result: `osVirtualToPhysical(G)` became `osVirtualToPhysical(*(s32 *)((s8 *)H + 0x1C))`, `H` the list pointer's symbol. Judge (the app's `fast_judge`): 250 -> 0. Only that one read changed; the function's other reads of `G` (through `(s8 *)G + 0x1130C`) stayed, and rewriting them too gave 435 and 365 singly, 250 for all three together. The new decomp-repair module `global_read_via_base` offers exactly these children (each read alone, then all) for a scalar `D_xxxxxxxx` and another `D_xxxxxxxx` within 0x100 bytes below it that the function also names.

Failed experiments that would otherwise be repeated: converting every read of the neighbour (the other two reads were in the target separate loads off the symbol).

Evidence: one function (verified by the judge, from the saved pre-edit source). Hypothesis for why: cse's related-value lookup folds a symbol-plus-constant address onto a register that already holds the base's address, so only a read written as base-plus-offset takes the register form. Names that were replaced by the project (no `D_` address) are not covered.
