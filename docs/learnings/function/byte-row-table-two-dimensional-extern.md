---
title: A flat byte table read stalls on a swapped sum; declared two-dimensional it matches
kind: function
compiler: sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: verified
date: 2026-11-27
---

A 137-instruction N64 sound-cue reader (SN GCC 2.7.2, per-function profile `sn-n64-272-0001 -G0 -mips3 -O1`) reads
two byte tables at the same place, `row = global / 10` (21 bytes a row), `column = an s16 global`, and calls a helper
with the bytes. m2c drafts each read as `*(((D_80093278 / 10) * 0x15) + &D_80095E20 + D_800B84C0)` (draft: 3284).

The source change: declare both tables `extern u8 D_X[][0x15];` and read `D_X[D_80093278 / 10][D_800B84C0]`. From
the saved best (40, flat byte arithmetic: a block-scoped `char *p = D_X;` between the division and the `* 0x15`
product, then `((u8 *)(x * 0x15 + p))[D_800B84C0]`) that one edit was exact (0, the app's judge). The only rows left at
40 were the address sum's two registers swapped (`addu v1,v0,a0; lh v0; addu v0,v1,v0` where the target has
`addu v0,v0,a0; lh v1; addu v0,v0,v1`). Hypothesis, not checked in expr.c: the array subscript expands the row address and the column in the order GCC
keeps for `ARRAY_REF`, which no spelling of the flat sum reproduced. On the m2c draft the same retype alone moves
3284 -> 2339 (the rest is the draft's other shapes).

Failed experiments (all from the 40 source, isolated judge): the table cast to a row pointer inline,
`((u8 (*)[0x15])D_X)[x / 10][col]` with `char D_X[]` kept: 950; flat `*(u8 *)(D_X + (x / 10) * 0x15 + col)` and its
operand orders: 950; `((u8 *)D_X)[(x / 10) * 0x15 + col]`: 950; column first in that index: 1350. An earlier
agent's: the index as its own statement 50, the base set after the product 850, the base declared before the
division, worse. So the declared type matters, not only the subscript: a cast to the same 2D type does not do it.

decomp-repair `symbol_row_arrays` now writes the byte form (a column term with no scale, the table declared as m2c's
unknown type or a byte, the dereference uncast or a byte cast); controlled: from the flat 950 source and from the m2c
draft its first child is the two-dimensional table (0 and 2339). One function; the cast-versus-declaration result is
from this function alone.
