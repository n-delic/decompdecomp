---
title: A held symbol address read with negative offsets at -O1 means the neighbouring globals are one struct
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

The target does `lui $s0; addiu $s0` of a halfword cursor and then reads `lhu 0($s0)` and `lb -4($s0)`, `lb -3($s0)`,
`lb -2($s0)` across calls (a 572-instruction menu handler). Separate externs, `((s8 *) &cursor)[-4]` and a pointer
local all folded the offsets back into `%lo(symbol)` loads (best 3300). Declaring one struct at the lowest address
(`struct { s8 x, z, w, pad; u16 sel; } S;` and reading `S.x`, `S.sel`) gave 150, and the last row was the record
field's signedness (`li -1` wants `s8` fields): 0. GCC expresses the other members relative to the address register it
already holds only when they are offsets of the same symbol.

Verified on the menu handler and its twin; the mechanism (cse's related-value lookup) is a hypothesis.
