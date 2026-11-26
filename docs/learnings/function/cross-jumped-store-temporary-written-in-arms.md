---
title: a store reached by goto and a picked temporary: write the store in every arm
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: an 83-instruction N64 text converter (a loop
over bytes with range tests and a pair table), gcc 2.7.2 KMC `-G0 -mips3 -mgp32 -mfp32 -O2`. The saved best (m2c form,
four register rows, score 20) set a temporary in two arms, one of them picked as `t = A; if (c) { t = B; }`, and stored
it once at a label the first arm reached by `goto`.

The source change, and the measured result before and after (scores, the judge used): a store in every arm
(`if (c) { *p = B; } else { *p = A; }`, `*p = C;`), the goto's labelled run written in the first arm and the next `if`
as its `else`: 20 -> 0 (app judge). A plain rewrite also matched first try: a `for` loop over a record array
`{u8 from; u8 to;}` (the target steps a byte index and a counter), no temporaries. decomp-repair
`conditional_store_arms` makes the first change in one child.

Failed experiments that would otherwise be repeated: the goto's run copied alone leaves 20; folding only the unpicked
arm 515; folding only the picked arm 520; a second u8/s32/u32 temporary for the two arms 75.

What is controlled evidence and what is one function: one function.
