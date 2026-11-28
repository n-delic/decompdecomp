---
title: lost lo pairing reads as field offset
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 26-instruction state toggle (three arms on one global word). The disassembly kept one store, in a jump's delay slot,
as a raw immediate (`sw $a0, 0x5D54($v1)`) while the `lui` before the branch was `%hi(D_80075D54)`. m2c read the
immediate as a field of the symbol: `M2C_FIELD(&D_80075D54, s32 *, 0x5D54) = 1;`, a store to the wrong address. The
offset is the symbol's own low half: the game stored to `D_80075D54`.

The app's judge, from the machine draft (800): `exit_run_outline` 305, then `symbol_low_half_offset` 0
(decomprepair, RetroReversing/decomp-repair#2272). The other order is 760, then 0. Written by hand as a plain
`if`/`else if`/`else`, the function was exact at the first compile.

Controlled: from the draft, only the offset change gives 760, only the arm change gives 305, both give 0. One function.
