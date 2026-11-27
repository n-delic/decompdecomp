---
title: unsized array declarations keep a global's address in a register; neighbours are offsets from it
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 141-instruction menu state handler reads a flag word as `lui; addiu; lhu 0($s2)` and its neighbours as
`-8($s2)`, `-0x10($s2)`, `-0x2E($s2)`.

The source change: scalar externs 6301; the three accessed flags declared `extern T D_x[];` and read as `D_x[0]` 1980;
then the neighbours written as `((u8 *)D_x)[-8]` and `*(u16 *)(D_y - 3)`, only at the sites where the target does, 0.

Failed: `volatile` scalars (7296). Earlier agents' scalar spellings under -O1 stopped 8 bytes of frame short.

One function. Library families: `unsized_extern_array`, `neighbour_symbol_rebase` (greedy chain 6301 to 0).
