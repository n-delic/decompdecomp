---
title: A pointer table stored through its symbol's address is an array
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 205-instruction state initialiser filling a
table of 21 pointers, which m2c wrote as `M2C_FIELD(&D_x, M2C_UNK **, k)` stores (flags above).

The source change, and the measured result before and after (scores, the judge used): `extern M2C_UNK *D_x[];` and
`D_x[k / 4]` (library family `symbol_field_array`): 1510 -> 740. Then `store_tail_into_arms` (an if/else that picked a
local, stored in each arm instead): 740 -> 0. The app's judge.

Failed experiments that would otherwise be repeated: a `volatile` guard global (740, no change) and a `do { } while (0)`
around the first store (520).

What is controlled evidence and what is one function: one function.
