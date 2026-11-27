---
title: A constant staged through a reused local hides the array form of an address-indexed table
kind: function
compiler: sn-n64-281
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 63-instruction N64 state update, SN64 GCC
2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Differing rows: the `lui` of a table's address and
of its index global in each other's order, and a word store one row early; in the near-miss, the stored literal also in
`$v1` for `$v0`.

The source change, and the measured result before and after (scores, the judge used), all from the app's judge:

- m2c draft, 80: it named the table as a scalar, `extern M2C_UNK D; ... (u32)(&D)[i]`. Written as `extern s32 D[];`
  and `(u32)D[i]` it is exact (so is `extern u32 D[];` without the cast, and the pointer local that
  `symbol_address_local` writes, which is what matched it live).
- Hand near-miss, 30: it had added `t = 0x190; p->f = t;` through a local the other arm reuses. The literal written in
  place scores 80, the array form alone 205, both together 0.

Failed experiments that would otherwise be repeated: `*(&D + i)` compiles like `(&D)[i]` (80). A greedy chain over all
families from the 30 near-miss did not move; the exact path through the older families starts with a child that is
worse than the seed (`local_web_split` 80, then `symbol_address_local` 0).

What is controlled evidence and what is one function: one function. decomp-repair `staged_constant_index` offers
`address_index_array`, `staged_constant_inline` and the two together.
