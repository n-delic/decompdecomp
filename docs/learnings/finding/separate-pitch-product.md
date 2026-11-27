---
title: Separate the pitch product from its input
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler overwrote a bend value with its scaled product
and then rounded and shifted it. Giving the product its own `s32` local, as
in a public SDK source, changed the isolated score from 260 to 235 under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`; the app recorded 235.

The existing `local_web_split` repair emits a different local name with the
same 235 score directly from the 260 source. Reversing multiplication operands
alone scored 260; combining the operand swap with the split also scored 260.
These are observations on one function, not controlled exact-match tests.
