---
title: A global record's element offset held in a local hides the array from the record view
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the case looked like: an 11-instruction N64 leaf that pushes a word of a global record onto the record's own stack
(a byte depth and a word array inside the record). The m2c draft declared the global `extern u8 D_x[];` and held the
element's byte offset in a local: `t = (M2C_FIELD(D_x, u8 *, 0xD) & 0xFF) * 4; ... M2C_FIELD((t + D_x), s32 *, 0x10)`.
Judged 1110.

The source change, in two steps: the global declared as one record with the array member, and the local holding the index
instead of the offset (100); then the local retyped `u8` with the `& 0xFF` dropped (0, the app's judge). A one-line
`D_x.marks[D_x.depth++] = D_x.top;` over the same record is exact too.

Permuter: decomp-repair `m2c_field_record_view` read an element only when the product was inline; PR
RetroReversing/decomp-repair#2290 handles the held offset, and `mask_local_retype` on its child is exact.

Failed experiments: keeping the index in an `s32` local, with the mask on the read or with `depth += 1`, stayed at 100.

What is controlled evidence and what is one function: the unit tests cover the rewrite and its refusals; the scores are
one function.
