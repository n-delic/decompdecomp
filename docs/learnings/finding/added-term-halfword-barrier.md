---
title: An added (x << k) - C term stays apart when converted to a halfword
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A nested grid of calls passed `(s32) pos[n].x + ((i << 6) - 0x40)` (and the same for `y` with `j`). The target
builds the term first (`andi; sll; addiu -0x40; addu`); GCC re-associates every plain spelling into
`(pos + (i << 6)) - 0x40`, which emits the `addiu -0x40` after the add.

`(s32) pos[n].x + (s16) ((i << 6) - 0x40)` for both terms, with `u8` counters and the position read as a field of a
0x64-byte record: 505 -> 0 (app judge, per-function `-O1`). The conversion emits nothing because the value fits.
Library family: `added_term_halfword`.

Failed on the same pair of terms (app judge): `(u32)` 791; `-0x40 + (i << 6)`, `- (0x40 - (i << 6))` and a comma
expression 505 (identical code); `(i - 1) * 0x40` 405; a ternary 1341. Under the project's `-O2` default the same
source scores 3875.

Controlled: each spelling above changed only the two terms of one function. That fold's re-association is what the
conversion stops is a hypothesis; the compiler source was not read.
