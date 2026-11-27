---
title: A previous-value save and a step are two plain statements, not a pair of temporaries
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 46-instruction N64 object update (a five-way jump-table dispatch, then a guarded "keep the previous value and
advance" on two struct fields). Under the SN64 2.8.1 profile above the saved best was at 10, all of it two register
rows: the target loads the old value into `$v1` and copies it to `$v0` for the add; ours loaded into `$v0` and copied to
`$v1`.

The source change (applied by the app's judge, score 10 -> 0):

    t = FIELD(p, 0x1C); u = t; t = t + 0x800; FIELD(p, 0x40) = u; FIELD(p, 0x1C) = t;
    ->  FIELD(p, 0x40) = FIELD(p, 0x1C); FIELD(p, 0x1C) += 0x800;

decomp-repair `old_value_save_compound` writes it from the saved best.

Failed experiments, same judge: one temporary with the step in the final store, from the split spelling's variants
(305: the copy is folded away and the function is one instruction short); `FIELD(p, 0x1C) = t + 0x800` stored before
the old value (125). `prove_chain.py` over the whole library from the saved best (353 children, 90 merges) had
nothing below 10; from the one-temporary spelling `product_inline` reaches 0.

The same C under the project's default KMC 2.7.2 profile scores far worse: the profile is per function. This is one
function. That cse's own temporaries are what takes the target's registers is a hypothesis; the passes were not read.
